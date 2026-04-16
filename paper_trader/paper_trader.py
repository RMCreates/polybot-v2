from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Signal, Market, Trade, PriceSnapshot, OrderBookSnapshot
from risk.risk_engine import DefaultRiskEngine, TradeProposal
from executor.paper_executor import PaperExecutor
from config.settings import settings
from signals.market_categorizer import session_for_hours, categorize_market
from observability.logger import get_logger

log = get_logger(__name__)

MIN_TRADE_USD = 3.0
STOP_LOSS_PP = 0.15  # auto-close if price moves -15pp against entry

# All session labels used in the system
ALL_SESSIONS = ["sports", "politics", "crypto", "longterm", "daily"]

# Capital per session
SESSION_CAPITAL = {
    "sports":   settings.session_capital_usd,
    "politics": settings.session_capital_usd,
    "crypto":   settings.session_capital_usd,
    "longterm": settings.longterm_session_capital_usd,
    "daily":    settings.daily_session_capital_usd,
}


def _get_trade_session(trade: Trade, market: Market) -> str:
    """Determine which session a trade belongs to (from reasoning_trace or inferred)."""
    meta = trade.reasoning_trace or {}
    if "session" in meta:
        return meta["session"]
    # Fallback: infer from market close time
    if market.closes_at:
        hours_left = (market.closes_at - datetime.now(timezone.utc)).total_seconds() / 3600
        return session_for_hours(hours_left, market.question)
    return categorize_market(market.question)


async def check_and_apply_stop_losses(session: AsyncSession, markets: list[Market]) -> int:
    """Auto-close any open paper trade that has drifted -15pp against entry (simulates selling back tokens)."""
    market_map = {m.id: m for m in markets}
    open_trades = list((await session.execute(
        select(Trade).where(Trade.is_paper == True, Trade.status == "OPEN")
    )).scalars())

    closed = 0
    for trade in open_trades:
        latest_snap = (await session.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.market_id == trade.market_id)
            .order_by(PriceSnapshot.captured_at.desc())
            .limit(1)
        )).scalar_one_or_none()
        if not latest_snap:
            continue

        current_price = latest_snap.yes_price if "YES" in trade.side else latest_snap.no_price
        drift = current_price - trade.entry_price  # negative = moving against you

        if drift <= -STOP_LOSS_PP:
            direction = 1 if "YES" in trade.side else -1
            trade.exit_price = round(current_price, 4)
            trade.pnl_usd = round((current_price - trade.entry_price) * trade.size_usd * direction, 4)
            trade.status = "RESOLVED"
            trade.closed_at = datetime.now(timezone.utc)
            if trade.reasoning_trace:
                trade.reasoning_trace = {**trade.reasoning_trace, "stop_loss_triggered": True}
            market = market_map.get(trade.market_id)
            if market:
                try:
                    from learning.store import LearningStore
                    await LearningStore(session).record_trade_resolved(trade, market)
                except Exception as _le:
                    log.warning("stop_loss_learning_failed", extra={"trade_id": trade.id, "error": str(_le)})
            log.info("stop_loss_triggered", extra={
                "trade_id": trade.id,
                "drift_pp": round(drift * 100, 1),
                "pnl_usd": trade.pnl_usd,
            })
            closed += 1

    if closed:
        await session.commit()
    return closed


async def run_paper_trading_cycle(
    session: AsyncSession,
    signals: list[Signal],
    markets: list[Market],
) -> list[Trade]:
    risk = DefaultRiskEngine()
    executor = PaperExecutor(session)
    market_map = {m.id: m for m in markets}

    # Bootstrap: count resolved trades to determine risk mode
    resolved_count = int((await session.execute(
        select(func.count(Trade.id))
        .where(Trade.is_paper == True, Trade.status == "RESOLVED")
    )).scalar() or 0)

    # ── Load all open + resolved paper trades into memory ──────────────────────
    open_rows = list((await session.execute(
        select(Trade, Market)
        .join(Market, Trade.market_id == Market.id)
        .where(Trade.is_paper == True, Trade.status == "OPEN")
    )).all())

    # Per-category open position count for R7 correlation check
    category_open_count: dict[str, int] = {}
    for t, m in open_rows:
        cat = categorize_market(m.question)
        category_open_count[cat] = category_open_count.get(cat, 0) + 1

    resolved_rows = list((await session.execute(
        select(Trade, Market)
        .join(Market, Trade.market_id == Market.id)
        .where(Trade.is_paper == True, Trade.status == "RESOLVED", Trade.pnl_usd.isnot(None))
    )).all())

    # ── Per-session exposure + P&L (computed in Python) ───────────────────────
    session_exposure: dict[str, float] = {s: 0.0 for s in ALL_SESSIONS}
    session_pnl: dict[str, float] = {s: 0.0 for s in ALL_SESSIONS}

    for t, m in open_rows:
        s = _get_trade_session(t, m)
        session_exposure[s] = session_exposure.get(s, 0.0) + t.size_usd

    session_wins: dict[str, int] = {s: 0 for s in ALL_SESSIONS}
    session_losses: dict[str, int] = {s: 0 for s in ALL_SESSIONS}

    for t, m in resolved_rows:
        s = _get_trade_session(t, m)
        pnl = t.pnl_usd or 0.0
        session_pnl[s] = session_pnl.get(s, 0.0) + pnl
        if pnl > 0:
            session_wins[s] += 1
        elif pnl < 0:
            session_losses[s] += 1

    # Win-rate multiplier per session (requires ≥10 resolved trades to activate)
    session_wr_multiplier: dict[str, float] = {}
    for s in ALL_SESSIONS:
        wins = session_wins[s]
        losses = session_losses[s]
        total = wins + losses
        if total < 10:
            session_wr_multiplier[s] = 1.0  # not enough data yet
        else:
            wr = wins / total
            if wr >= 0.65:
                session_wr_multiplier[s] = 1.3   # hot session → +30%
            elif wr >= 0.55:
                session_wr_multiplier[s] = 1.1   # slightly hot → +10%
            elif wr <= 0.35:
                session_wr_multiplier[s] = 0.7   # cold session → -30%
            elif wr <= 0.45:
                session_wr_multiplier[s] = 0.9   # slightly cold → -10%
            else:
                session_wr_multiplier[s] = 1.0   # neutral

    # Daily pocket: count trades placed today
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_trades_today = sum(
        1 for t, m in open_rows
        if _get_trade_session(t, m) == "daily" and t.placed_at >= today_start
    )

    # Quick check: any session with capital available?
    any_capital = any(
        SESSION_CAPITAL[s] + session_pnl[s] - session_exposure[s] >= MIN_TRADE_USD
        for s in ALL_SESSIONS
    )
    if not any_capital:
        log.warning("all_sessions_at_capacity", extra={
            "exposure": {s: round(session_exposure[s], 2) for s in ALL_SESSIONS},
        })
        return []

    executed_trades = []

    for signal in signals:
        market = market_map.get(signal.market_id)
        if not market:
            continue

        # One open position per market
        existing = (await session.execute(
            select(Trade).where(
                Trade.market_id == market.id,
                Trade.status == "OPEN",
                Trade.is_paper == True,
            )
        )).scalar_one_or_none()
        if existing:
            continue

        # Determine session for this signal
        sig_meta = signal.metadata_json or {}
        session_label = sig_meta.get("session")
        if not session_label:
            # Fallback: infer from market close time
            if market.closes_at:
                hours_left = (market.closes_at - datetime.now(timezone.utc)).total_seconds() / 3600
                session_label = session_for_hours(hours_left, market.question)
            else:
                session_label = categorize_market(market.question)

        # Daily quota check
        if session_label == "daily":
            bootstrap_mode = resolved_count < 30
            if daily_trades_today >= settings.daily_max_trades and not bootstrap_mode:
                continue  # daily quota full (skip in normal mode)

        session_cap = SESSION_CAPITAL.get(session_label, settings.session_capital_usd)
        available_capital = session_cap + session_pnl.get(session_label, 0.0) - session_exposure.get(session_label, 0.0)

        if available_capital < MIN_TRADE_USD:
            log.info("session_at_capacity", extra={
                "session": session_label,
                "available": round(available_capital, 2),
            })
            continue

        # Determine direction
        if signal.edge > 0:
            side = "BUY_YES"
            effective_edge = signal.edge
            token_id = market.yes_token_id
        else:
            side = "BUY_NO"
            effective_edge = -signal.edge
            token_id = market.no_token_id

        if effective_edge <= 0:
            continue

        # Full Kelly sizing with win-rate multiplier
        kelly = effective_edge / max(1 - signal.fair_probability, 0.01)
        wr_mult = session_wr_multiplier.get(session_label, 1.0)
        kelly_size = kelly * session_cap * wr_mult
        kelly_size = max(kelly_size, 5.0)
        is_favorable = (
            effective_edge >= settings.favorable_edge_threshold
            and signal.confidence >= settings.favorable_confidence_threshold
        )
        size_cap = (
            settings.max_favorable_position_usd if is_favorable
            else settings.max_single_position_usd
        )
        proposed_size = min(kelly_size, size_cap, available_capital)

        # Get latest orderbook snapshot for risk engine
        snap = (await session.execute(
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.market_id == market.id)
            .order_by(OrderBookSnapshot.captured_at.desc())
            .limit(1)
        )).scalar_one_or_none()

        proposal = TradeProposal(
            market_condition_id=market.condition_id,
            side=side,
            token_id=token_id,
            edge=effective_edge,
            fair_probability=signal.fair_probability,
            market_midpoint=signal.market_midpoint,
            signal_confidence=signal.confidence,
            proposed_size_usd=proposed_size,
            spread=snap.spread if snap else 0.0,
            market_liquidity_usd=market.liquidity_usd,
            market_category=categorize_market(market.question),
        )

        cat = categorize_market(market.question)
        decision = risk.evaluate(
            proposal,
            session_exposure.get(session_label, 0.0),
            [],
            resolved_count,
            category_open_count=category_open_count.get(cat, 0),
        )

        reasoning_trace = {
            "signal_id": signal.id,
            "market_id": signal.market_id,
            "market_condition_id": market.condition_id,
            "fair_probability": signal.fair_probability,
            "market_midpoint": signal.market_midpoint,
            "edge": signal.edge,
            "effective_edge": effective_edge,
            "confidence": signal.confidence,
            "provider_name": signal.provider_name,
            "is_favorable_trade": is_favorable,
            "size_cap_used": size_cap,
            "kelly_size": round(kelly_size, 4),
            "proposed_size_usd": round(proposed_size, 4),
            "risk_approved": decision.approved,
            "risk_rejection_reasons": decision.rejection_reasons,
            "risk_rules_applied": decision.applied_rules,
            "final_size_usd": decision.final_size_usd,
            "available_capital": round(available_capital, 2),
            "session": session_label,
            "session_capital": session_cap,
            "resolved_count": resolved_count,
            "wr_multiplier": wr_mult,
            "session_wins": session_wins.get(session_label, 0),
            "session_losses": session_losses.get(session_label, 0),
        }

        if decision.approved:
            trade = await executor.execute(proposal, decision, signal.id, reasoning_trace)
            session_exposure[session_label] = session_exposure.get(session_label, 0.0) + decision.final_size_usd
            if session_label == "daily":
                daily_trades_today += 1
            executed_trades.append(trade)
            log.info(
                "paper_trade_executed",
                extra={
                    "trade_id": trade.id,
                    "session": session_label,
                    "edge": round(effective_edge, 4),
                    "is_favorable": is_favorable,
                    "session_available_after": round(
                        session_cap + session_pnl.get(session_label, 0) - session_exposure[session_label], 2
                    ),
                }
            )
        else:
            log.info(
                "paper_trade_rejected",
                extra={"market_id": signal.market_id, "session": session_label, "reasons": decision.rejection_reasons}
            )

    return executed_trades
