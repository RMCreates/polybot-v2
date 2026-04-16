from __future__ import annotations
import time
import httpx
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Trade, Signal, Market, OrderBookSnapshot, PriceSnapshot, LearningEntry
from signals.market_categorizer import session_for_hours, categorize_market
from config.settings import settings


def _utcnow() -> datetime:
    """Naive UTC datetime for comparison with SQLite-stored naive datetimes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _build_reasoning_summary(meta: dict, trace: dict, side: str) -> dict:
    blend = meta.get("blend_used", "")
    momentum_edge = meta.get("momentum_edge", 0) or 0
    momentum_conf = meta.get("momentum_conf", 0) or 0
    ob_edge = meta.get("ob_edge", 0) or 0
    ob_imbalance = meta.get("ob_imbalance", 0) or 0
    manifold_conf = meta.get("manifold_conf", 0) or 0
    fair_prob = trace.get("fair_probability") or meta.get("market_midpoint") or 0.5
    mid = trace.get("market_midpoint") or meta.get("market_midpoint") or 0.5
    edge_val = trace.get("edge", 0) or 0
    final_size = trace.get("final_size_usd", 0) or 0
    direction = "YES" if "YES" in side else "NO"

    momentum_dir = "climbing" if momentum_edge > 0 else "falling"
    ob_skew = "buyer-heavy" if ob_imbalance > 0 else "seller-heavy"
    price_view = "underpriced" if edge_val > 0 else "overpriced"
    edge_cents = abs(edge_val * 100)
    mom_pct = abs(momentum_edge * 100)
    ob_pct = abs(ob_imbalance * 100)

    if blend == "manifold_weighted":
        headline = (
            f"Price has been {momentum_dir} ({mom_pct:.1f}% momentum) and external prediction "
            f"markets agree — the market looks {price_view} by {edge_cents:.1f}¢ vs its current {mid*100:.0f}¢."
        )
    elif blend == "internal":
        if abs(momentum_edge) > 0.001 and abs(ob_imbalance) > 0.01:
            headline = (
                f"Price has been {momentum_dir} ({mom_pct:.1f}% momentum) and the order book is "
                f"{ob_pct:.0f}% {ob_skew} — the market looks {price_view} by {edge_cents:.1f}¢."
            )
        elif abs(momentum_edge) > 0.001:
            headline = (
                f"Price has been {momentum_dir} with {mom_pct:.1f}% momentum — "
                f"the market looks {price_view} by {edge_cents:.1f}¢ vs current price of {mid*100:.0f}¢."
            )
        else:
            headline = (
                f"Order book is {ob_pct:.0f}% {ob_skew} with no clear price trend — "
                f"the market looks {price_view} by {edge_cents:.1f}¢."
            )
    else:
        headline = f"No directional signal — used midpoint ({mid*100:.0f}¢) as fair value. Low conviction, small position."

    bullets = []
    if abs(momentum_edge) > 0.001:
        trend = "UP" if momentum_edge > 0 else "DOWN"
        bullets.append(
            f"📈 Momentum: trending {trend} — {momentum_edge*100:+.1f}% edge "
            f"(trend confidence: {momentum_conf*100:.0f}%)"
        )
    if abs(ob_imbalance) > 0.01:
        side_str = "buyers" if ob_imbalance > 0 else "sellers"
        bullets.append(
            f"📊 Order book: {side_str} dominate — "
            f"imbalance {ob_imbalance:+.2f} → {ob_edge*100:+.1f}% edge"
        )
    if blend == "manifold_weighted" and manifold_conf > 0:
        bullets.append(
            f"🔗 Manifold consensus: matched external market ({manifold_conf*100:.0f}% confidence)"
        )
    edge_cents = edge_val * 100
    bullets.append(
        f"💡 Fair value: {fair_prob*100:.0f}¢ vs market {mid*100:.0f}¢ "
        f"→ {edge_cents:+.1f}¢ edge → BET {direction} ${final_size:.2f}"
    )

    return {"headline": headline, "bullets": bullets}


async def get_trades_with_signals(session: AsyncSession, limit: int = 100) -> list[dict]:
    result = await session.execute(
        select(Trade, Signal, Market)
        .join(Signal, Trade.signal_id == Signal.id, isouter=True)
        .join(Market, Trade.market_id == Market.id)
        .where(Trade.status != "CANCELLED")
        .order_by(desc(Trade.placed_at))
        .limit(limit)
    )
    rows = []
    for trade, signal, market in result:
        current_price = None
        unrealized_pnl = None
        signal_drift = None
        if trade.status == "OPEN":
            latest_snap = (await session.execute(
                select(PriceSnapshot)
                .where(PriceSnapshot.market_id == trade.market_id)
                .order_by(PriceSnapshot.captured_at.desc())
                .limit(1)
            )).scalar_one_or_none()
            if latest_snap:
                current_price = latest_snap.yes_price if "YES" in trade.side else latest_snap.no_price
                direction = 1 if "YES" in trade.side else -1
                unrealized_pnl = round(
                    (current_price - trade.entry_price) * trade.size_usd * direction, 2
                )
                # Signal drift: pp moved in your favor since entry
                signal_drift = round((current_price - trade.entry_price) * 100, 1)

        meta = signal.metadata_json if signal else {}
        trace = trade.reasoning_trace or {}

        # Determine session label — reasoning_trace is authoritative (paper_trader's decision)
        session_label = (
            trace.get("session")
            or meta.get("session")
            or (session_for_hours(
                    (market.closes_at - _utcnow()).total_seconds() / 3600,
                    market.question
                ) if market.closes_at and market.closes_at > _utcnow()
                else categorize_market(market.question))
        )

        # Hours to close
        now = _utcnow()
        if market.closes_at and market.closes_at > now:
            hours_to_close = round((market.closes_at - now).total_seconds() / 3600, 1)
        else:
            hours_to_close = None

        pnl = trade.pnl_usd
        if trade.status == "OPEN":
            outcome_label = "OPEN"
        elif pnl is not None and pnl > 0:
            outcome_label = "WIN"
        elif pnl is not None and pnl < 0:
            outcome_label = "LOSS"
        elif trade.status == "RESOLVED":
            outcome_label = "FLAT"
        else:
            outcome_label = "OPEN"

        rows.append({
            "id": trade.id,
            "question": market.question,
            "side": trade.side,
            "size_usd": round(trade.size_usd, 2),
            "entry_price": trade.entry_price,
            "current_price": current_price,
            "unrealized_pnl": unrealized_pnl,
            "exit_price": trade.exit_price,
            "pnl_usd": round(pnl, 2) if pnl is not None else None,
            "status": trade.status,
            "outcome_label": outcome_label,
            "placed_at": trade.placed_at.strftime("%Y-%m-%d %H:%M") if trade.placed_at else "",
            "is_paper": trade.is_paper,
            "fair_probability": signal.fair_probability if signal else None,
            "market_midpoint": signal.market_midpoint if signal else None,
            "edge": round(signal.edge, 6) if signal else None,
            "signal_confidence": round(signal.confidence, 2) if signal else None,
            "provider_name": signal.provider_name if signal else "unknown",
            "blend_used": meta.get("blend_used"),
            "sources_used": meta.get("sources_used", []),
            "signal_metadata": meta,
            "reasoning_trace": trace,
            "session": session_label,
            "closes_at": market.closes_at.strftime("%m/%d %H:%M") if market.closes_at else None,
            "hours_to_close": hours_to_close,
            "signal_drift": signal_drift,
            "reasoning_summary": _build_reasoning_summary(meta, trace, trade.side),
        })
    return rows


async def get_system_status(session: AsyncSession) -> dict:
    now = _utcnow()
    today = now - timedelta(hours=24)

    markets_count = (await session.execute(select(func.count(Market.id)))).scalar() or 0
    signals_today = (await session.execute(
        select(func.count(Signal.id)).where(Signal.created_at >= today)
    )).scalar() or 0
    avg_edge = float((await session.execute(
        select(func.avg(Signal.edge)).where(Signal.created_at >= today)
    )).scalar() or 0.0)
    trades_open = (await session.execute(
        select(func.count(Trade.id)).where(Trade.status == "OPEN", Trade.is_paper == True)
    )).scalar() or 0
    trades_today = (await session.execute(
        select(func.count(Trade.id)).where(Trade.placed_at >= today, Trade.is_paper == True)
    )).scalar() or 0
    total_exposure = float((await session.execute(
        select(func.sum(Trade.size_usd)).where(Trade.status == "OPEN", Trade.is_paper == True)
    )).scalar() or 0.0)
    total_pnl = float((await session.execute(
        select(func.sum(Trade.pnl_usd)).where(Trade.is_paper == True, Trade.pnl_usd.isnot(None))
    )).scalar() or 0.0)

    # OB snapshots — show today count, fallback to total if 0
    ob_today = (await session.execute(
        select(func.count(OrderBookSnapshot.id)).where(OrderBookSnapshot.captured_at >= today)
    )).scalar() or 0
    if ob_today == 0:
        ob_total = (await session.execute(
            select(func.count(OrderBookSnapshot.id))
        )).scalar() or 0
        ob_display = f"0 today ({ob_total} total)"
    else:
        ob_display = str(ob_today)

    return {
        "markets_tracked": markets_count,
        "orderbook_snapshots_today": ob_display,
        "signals_today": signals_today,
        "avg_edge_today": round(avg_edge, 4),
        "open_positions": trades_open,
        "trades_today": trades_today,
        "total_exposure_usd": round(total_exposure, 2),
        "total_pnl_usd": round(total_pnl, 2),
    }


async def get_performance_data(session: AsyncSession) -> dict:
    resolved = list((await session.execute(
        select(Trade)
        .where(Trade.is_paper == True, Trade.status == "RESOLVED", Trade.pnl_usd.isnot(None))
        .order_by(Trade.closed_at)
    )).scalars())

    dates, cumulative = [], []
    running = 0.0
    for t in resolved:
        running += t.pnl_usd
        dates.append(t.closed_at.strftime("%m/%d %H:%M") if t.closed_at else "")
        cumulative.append(round(running, 2))

    return {"dates": dates, "cumulative_pnl": cumulative, "current_pnl": round(running, 2)}


async def close_paper_trade(session: AsyncSession, trade_id: int) -> dict:
    trade = await session.get(Trade, trade_id)
    if not trade or trade.status != "OPEN" or not trade.is_paper:
        return {"error": "Trade not found or not closeable"}

    latest_snap = (await session.execute(
        select(PriceSnapshot)
        .where(PriceSnapshot.market_id == trade.market_id)
        .order_by(PriceSnapshot.captured_at.desc())
        .limit(1)
    )).scalar_one_or_none()

    if latest_snap:
        exit_price = latest_snap.yes_price if "YES" in trade.side else latest_snap.no_price
    else:
        exit_price = trade.entry_price  # fallback: close at entry (0 P&L)

    direction = 1 if "YES" in trade.side else -1
    trade.exit_price = round(exit_price, 4)
    trade.pnl_usd = round((exit_price - trade.entry_price) * trade.size_usd * direction, 4)
    trade.status = "RESOLVED"
    trade.closed_at = datetime.now(timezone.utc)

    market = await session.get(Market, trade.market_id)
    if market:
        from learning.store import LearningStore
        await LearningStore(session).record_trade_resolved(trade, market)

    await session.commit()
    return {"status": "closed", "pnl_usd": trade.pnl_usd, "exit_price": trade.exit_price}


async def check_source_health() -> dict:
    metaculus_key = settings.metaculus_api_key.get_secret_value()
    metaculus_url = "https://www.metaculus.com/api/questions/?limit=1"

    checks = [
        ("kalshi", "https://trading-api.kalshi.com/trade-api/v2/markets?limit=1&status=open", {}),
        ("manifold", "https://api.manifold.markets/v0/markets?limit=1", {}),
    ]
    news_key = settings.news_api_key.get_secret_value()
    if news_key:
        checks.append(("newsapi", "https://newsapi.org/v2/top-headlines?country=us&pageSize=1", {"X-Api-Key": news_key}))
    else:
        checks.append(("newsapi", None, {}))

    sources = {}
    async with httpx.AsyncClient(timeout=5) as client:
        # Metaculus — try with auth first, fall back to public if auth fails
        t0 = time.monotonic()
        try:
            meta_headers = {"Accept": "application/json"}
            if metaculus_key:
                meta_headers["Authorization"] = f"Token {metaculus_key}"
            r = await client.get(metaculus_url, headers=meta_headers)
            if r.status_code >= 400 and metaculus_key:
                # Auth failed — retry without key (public endpoint)
                r = await client.get(metaculus_url, headers={"Accept": "application/json"})
            sources["metaculus"] = {"ok": r.status_code < 400, "ms": round((time.monotonic() - t0) * 1000)}
        except Exception:
            sources["metaculus"] = {"ok": False, "ms": None}

        for name, url, headers in checks:
            if url is None:
                sources[name] = {"ok": False, "ms": None, "note": "no API key"}
                continue
            t0 = time.monotonic()
            try:
                r = await client.get(url, headers=headers)
                sources[name] = {"ok": r.status_code < 400, "ms": round((time.monotonic() - t0) * 1000)}
            except Exception:
                sources[name] = {"ok": False, "ms": None}
    return sources


async def generate_daily_recommendations(session: AsyncSession, markets: list) -> list[dict]:
    """Generate top N daily trade recommendations with Kalshi vs Polymarket comparison."""
    from signals.sources.kalshi_consensus import find_kalshi_probability
    from config.settings import settings as cfg

    daily_markets = []
    now = _utcnow()

    for market in markets:
        if not market.closes_at:
            continue
        hours_left = (market.closes_at - now).total_seconds() / 3600
        if hours_left <= 0 or hours_left > 48:
            continue
        daily_markets.append((market, hours_left))

    recommendations = []
    for market, hours_left in daily_markets:
        # Get Polymarket price
        latest_snap = (await session.execute(
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.market_id == market.id)
            .order_by(OrderBookSnapshot.captured_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        poly_price = latest_snap.midpoint if latest_snap else 0.5

        # Get Kalshi price
        kalshi_result = await find_kalshi_probability(market.question)
        if kalshi_result is None:
            continue

        kalshi_prob, kalshi_sim, kalshi_meta = kalshi_result
        price_diff = kalshi_prob - poly_price
        price_diff_pp = round(price_diff * 100, 1)
        is_arbitrage = abs(price_diff_pp) > 3.0

        # Determine direction
        if kalshi_prob > poly_price:
            direction = "BUY YES"
            edge = round(kalshi_prob - poly_price, 4)
        else:
            direction = "BUY NO"
            edge = round(poly_price - kalshi_prob, 4)

        # Plain-English reasoning
        if is_arbitrage:
            reasoning = (
                f"Kalshi prices this at {kalshi_prob*100:.0f}% while Polymarket has it at "
                f"{poly_price*100:.0f}% — a {abs(price_diff_pp):.1f}pp gap suggesting arbitrage. "
                f"Kalshi match: \"{kalshi_meta.get('kalshi_title', '')}\" "
                f"(similarity: {kalshi_sim*100:.0f}%). "
                f"Consider {direction} on Kalshi at the better price."
            )
        else:
            reasoning = (
                f"Kalshi ({kalshi_prob*100:.0f}%) and Polymarket ({poly_price*100:.0f}%) "
                f"show a {abs(price_diff_pp):.1f}pp difference. "
                f"Kalshi match: \"{kalshi_meta.get('kalshi_title', '')}\" "
                f"(similarity: {kalshi_sim*100:.0f}%). "
                f"Edge is modest — {direction} if you have conviction."
            )

        recommendations.append({
            "question": market.question,
            "poly_price": round(poly_price, 4),
            "kalshi_price": round(kalshi_prob, 4),
            "kalshi_ticker": kalshi_meta.get("kalshi_ticker", ""),
            "kalshi_title": kalshi_meta.get("kalshi_title", ""),
            "price_diff_pp": price_diff_pp,
            "direction": direction,
            "edge": edge,
            "confidence": round(kalshi_sim, 3),
            "reasoning_text": reasoning,
            "is_arbitrage": is_arbitrage,
            "closes_at": market.closes_at.strftime("%m/%d %H:%M") if market.closes_at else None,
            "hours_to_close": round(hours_left, 1),
        })

    # Sort by edge descending, take top N
    recommendations.sort(key=lambda r: r["edge"], reverse=True)
    return recommendations[:cfg.daily_recommendation_count]


async def get_learning_summary(session: AsyncSession) -> dict:
    from learning.store import LearningStore
    return await LearningStore(session).get_performance_summary()


async def get_session_stats(db: AsyncSession) -> dict:
    """Per-session capital, exposure, P&L and win rate."""
    from paper_trader.paper_trader import ALL_SESSIONS, SESSION_CAPITAL, _get_trade_session

    # Load all paper trades + markets
    open_rows = list((await db.execute(
        select(Trade, Market)
        .join(Market, Trade.market_id == Market.id)
        .where(Trade.is_paper == True, Trade.status == "OPEN")
    )).all())

    resolved_rows = list((await db.execute(
        select(Trade, Market)
        .join(Market, Trade.market_id == Market.id)
        .where(Trade.is_paper == True, Trade.status == "RESOLVED", Trade.pnl_usd.isnot(None))
    )).all())

    today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    session_exposure: dict = {s: 0.0 for s in ALL_SESSIONS}
    session_pnl: dict = {s: 0.0 for s in ALL_SESSIONS}
    session_open_count: dict = {s: 0 for s in ALL_SESSIONS}
    session_wins: dict = {s: 0 for s in ALL_SESSIONS}
    session_losses: dict = {s: 0 for s in ALL_SESSIONS}
    daily_used_today: int = 0

    for t, m in open_rows:
        s = _get_trade_session(t, m)
        session_exposure[s] = session_exposure.get(s, 0.0) + t.size_usd
        session_open_count[s] = session_open_count.get(s, 0) + 1
        if s == "daily" and t.placed_at and t.placed_at >= today_start:
            daily_used_today += 1

    for t, m in resolved_rows:
        s = _get_trade_session(t, m)
        pnl = t.pnl_usd or 0.0
        session_pnl[s] = session_pnl.get(s, 0.0) + pnl
        if pnl > 0:
            session_wins[s] = session_wins.get(s, 0) + 1
        elif pnl < 0:
            session_losses[s] = session_losses.get(s, 0) + 1
        if s == "daily" and t.placed_at and t.placed_at >= today_start:
            daily_used_today += 1

    result = {}
    SESSION_LABELS = {
        "sports": "Sports",
        "politics": "Politics",
        "crypto": "Crypto",
        "longterm": "Long-term",
        "daily": "Daily",
    }
    for s in ALL_SESSIONS:
        cap = SESSION_CAPITAL.get(s, settings.session_capital_usd)
        exposure = round(session_exposure[s], 2)
        pnl = round(session_pnl[s], 2)
        available = round(cap + pnl - exposure, 2)
        wins = session_wins[s]
        losses = session_losses[s]
        total_resolved = wins + losses
        win_rate = round(wins / total_resolved, 3) if total_resolved > 0 else None
        result[s] = {
            "label": SESSION_LABELS.get(s, s.title()),
            "capital": cap,
            "exposure_usd": exposure,
            "pnl_usd": pnl,
            "available_usd": available,
            "open_trades": session_open_count[s],
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "daily_used_today": daily_used_today if s == "daily" else None,
            "daily_max": settings.daily_max_trades if s == "daily" else None,
        }
    return result
