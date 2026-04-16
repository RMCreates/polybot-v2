from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from db.engine import AsyncSessionLocal
from observability.logger import get_logger

log = get_logger(__name__)

# ── In-memory state (reset on bot restart) ────────────────────────────────────
_bot_paused: bool = False
_last_cycle: dict = {}


def is_paused() -> bool:
    return _bot_paused


def set_paused(paused: bool) -> None:
    global _bot_paused
    _bot_paused = paused


def get_last_cycle() -> dict:
    return _last_cycle


async def run_full_cycle() -> None:
    """
    Full pipeline: collect → analyze → paper trade → resolve → learn → alert.
    Runs every 30 minutes. max_instances=1 prevents overlapping runs.
    """
    if _bot_paused:
        log.info("cycle_skipped_paused")
        return

    cycle_start = datetime.now(timezone.utc)

    try:
        async with AsyncSessionLocal() as session:

            # ── Stage 1: Collect market data ──────────────────────────────────
            from collectors.gamma_collector import GammaCollector
            from collectors.clob_collector import CLOBCollector

            gamma = GammaCollector(session)
            markets = await gamma.fetch_and_upsert_markets()
            await gamma.snapshot_prices(markets)

            clob = CLOBCollector(session)
            await clob.snapshot_orderbooks(markets)

            # ── Stage 2: Load all active markets ─────────────────────────────
            from sqlalchemy import select as sa_select
            from db.models import Market as MarketModel

            all_active = list((await session.execute(
                sa_select(MarketModel).where(MarketModel.is_active == True)
            )).scalars())

            # ── Stage 2b: Stop-loss check ─────────────────────────────────────
            from paper_trader.paper_trader import check_and_apply_stop_losses
            stop_loss_closed = await check_and_apply_stop_losses(session, markets)
            if stop_loss_closed:
                log.info("stop_losses_applied", extra={"closed": stop_loss_closed})

            # ── Stage 3: Signal engine ────────────────────────────────────────
            import signals.plugins.combined_signal  # triggers self-registration
            from analyzer.edge_analyzer import run_analysis_cycle

            signals_out = await run_analysis_cycle(session, all_active)

            # ── Stage 3b: Generate daily recommendations ─────────────────────
            try:
                from dashboard.api import generate_daily_recommendations
                daily_recs = await generate_daily_recommendations(session, all_active)
                _last_cycle["daily_recommendations"] = daily_recs
            except Exception as e:
                log.warning("daily_recs_generation_failed", extra={"error": str(e)})
                _last_cycle["daily_recommendations"] = []

            # ── Stage 4: Paper trading ────────────────────────────────────────
            from paper_trader.paper_trader import run_paper_trading_cycle

            trades_out = await run_paper_trading_cycle(session, signals_out, all_active)

            # ── Stage 4b: Record new trades in learning store ─────────────────
            if trades_out:
                from learning.store import LearningStore
                from notifications.telegram import send_alert, trade_placed_message
                from config.settings import settings

                store = LearningStore(session)
                market_map = {m.id: m for m in all_active}
                signal_map = {s.id: s for s in signals_out}

                for trade in trades_out:
                    sig = signal_map.get(trade.signal_id)
                    mkt = market_map.get(trade.market_id)
                    if sig and mkt:
                        # Determine session label for learning entry
                        trace = trade.reasoning_trace or {}
                        sess_label = trace.get("session", "unknown")

                        await store.record_trade_placed(trade, sig, mkt, session_label=sess_label)
                        await send_alert(trade_placed_message(
                            trade_side=trade.side,
                            question=mkt.question,
                            size_usd=trade.size_usd,
                            entry_price=trade.entry_price,
                            edge=sig.edge,
                            confidence=sig.confidence,
                        ))
                        # Alert on max-size trade (favorable cap hit)
                        if trade.size_usd >= settings.max_favorable_position_usd:
                            await send_alert(
                                f"🔥 <b>MAX SIZE TRADE PLACED</b>\n"
                                f"{trade.side} — <i>{mkt.question[:60]}</i>\n"
                                f"Size: ${trade.size_usd:.2f} (max cap hit) | Edge: {sig.edge*100:.1f}%"
                            )

            # ── Stage 5: Resolve closed markets ──────────────────────────────
            from collectors.data_collector import DataCollector

            resolver = DataCollector(session)
            await resolver.check_and_resolve_trades()

            # ── Stage 5b: Update learning store with outcomes + send alerts ───
            if resolver.last_resolved:
                from learning.store import LearningStore
                from notifications.telegram import send_alert, trade_resolved_message
                store = LearningStore(session)
                market_map_res = {m.id: m for m in all_active}

                for trade in resolver.last_resolved:
                    mkt = market_map_res.get(trade.market_id)
                    if not mkt:
                        from db.models import Market as MarketModel2
                        mkt = await session.get(MarketModel2, trade.market_id)
                    if mkt:
                        await store.record_trade_resolved(trade, mkt)
                        outcome = "WIN" if (trade.pnl_usd or 0) > 0 else "LOSS" if (trade.pnl_usd or 0) < 0 else "FLAT"
                        await send_alert(trade_resolved_message(
                            outcome=outcome,
                            question=mkt.question,
                            side=trade.side,
                            pnl_usd=trade.pnl_usd or 0.0,
                            entry_price=trade.entry_price,
                            exit_price=trade.exit_price or trade.entry_price,
                        ))

            # ── Stage 6: Archive stale lessons ───────────────────────────────
            try:
                from learning.store import LearningStore
                await LearningStore(session).archive_stale_lessons()
            except Exception as e:
                log.warning("archive_stale_lessons_failed", extra={"error": str(e)})

            duration = round((datetime.now(timezone.utc) - cycle_start).total_seconds(), 1)
            _last_cycle.update({
                "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M local"),
                "duration_sec": duration,
                "markets": len(markets),
                "active_markets": len(all_active),
                "signals": len(signals_out),
                "trades": len(trades_out),
                "resolved": len(resolver.last_resolved),
                "status": "ok",
            })

            log.info("full_cycle_complete", extra={
                "markets_collected": len(markets),
                "active_markets": len(all_active),
                "signals_generated": len(signals_out),
                "new_trades": len(trades_out),
                "trades_resolved": len(resolver.last_resolved),
                "duration_sec": duration,
            })

    except Exception as e:
        _last_cycle.update({
            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M local"),
            "duration_sec": round((datetime.now(timezone.utc) - cycle_start).total_seconds(), 1),
            "status": "error",
            "error": str(e),
        })
        log.error("full_cycle_failed", extra={"error": str(e)})


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_full_cycle,
        "interval",
        minutes=5,
        id="full_cycle",
        max_instances=1,
    )
    scheduler.start()
    return scheduler
