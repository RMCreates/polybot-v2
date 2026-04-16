from __future__ import annotations
import importlib
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Signal, Market, OrderBookSnapshot, PriceSnapshot
from signals.registry import get as get_provider
from signals.market_categorizer import session_for_hours
from config.settings import settings
from observability.logger import get_logger

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

log = get_logger(__name__)

MAX_BELIEVABLE_EDGE = 0.35   # anything above this is almost certainly a data error
STALE_THRESHOLD_MINUTES = 70  # skip orderbook snapshots older than this

def compute_edge(fair_prob: float, market_mid: float) -> float:
    """Edge = fair probability - market midpoint. Positive = market underpricing YES."""
    return round(fair_prob - market_mid, 6)

async def run_analysis_cycle(session: AsyncSession, markets: list[Market]) -> list[Signal]:
    # Import the plugin module so it self-registers via register() at module level
    try:
        importlib.import_module(f"signals.plugins.{settings.signal_provider}")
    except ModuleNotFoundError:
        log.error("signal_plugin_not_found", extra={"provider": settings.signal_provider})
        return []

    provider = get_provider(settings.signal_provider)
    # Inject session so providers can access DB (e.g., for learning feedback)
    if hasattr(provider, '_session'):
        provider._session = session
    signals = []

    for market in markets:
        if not provider.is_applicable(market):
            continue

        # Latest order book snapshot
        snap_result = await session.execute(
            select(OrderBookSnapshot)
            .where(OrderBookSnapshot.market_id == market.id)
            .order_by(OrderBookSnapshot.captured_at.desc())
            .limit(1)
        )
        snap = snap_result.scalar_one_or_none()

        # Security Fix 5: Skip stale snapshots — data older than 70 min is unreliable
        if snap is not None:
            age = _utcnow() - snap.captured_at
            if age > timedelta(minutes=STALE_THRESHOLD_MINUTES):
                log.warning("stale_orderbook_skipped", extra={
                    "market_id": market.id,
                    "snapshot_age_min": int(age.total_seconds() // 60),
                })
                continue

        # Time-to-close filter: only trade markets within our session windows
        if market.closes_at is None:
            continue  # close date unknown — skip until next collection populates it
        hours_left = (market.closes_at - _utcnow()).total_seconds() / 3600
        if hours_left < settings.min_hours_to_close:
            continue  # expires too soon
        if hours_left > settings.longterm_max_hours:
            log.debug("market_beyond_1yr", extra={"market_id": market.id, "hours": round(hours_left)})
            continue  # beyond 1-year cap

        # Price history (last 50 snapshots)
        history_result = await session.execute(
            select(PriceSnapshot)
            .where(PriceSnapshot.market_id == market.id)
            .order_by(PriceSnapshot.captured_at.desc())
            .limit(50)
        )
        history = list(history_result.scalars())

        result = await provider.compute_signal(market, snap, history)
        mid = snap.midpoint if snap else 0.5
        edge = compute_edge(result.fair_probability, mid)

        # Security Fix 4: Reject suspiciously high edges — almost certainly a data error
        if abs(edge) > MAX_BELIEVABLE_EDGE:
            log.warning("edge_sanity_check_failed", extra={
                "market_id": market.id,
                "edge": edge,
                "fair_prob": result.fair_probability,
                "market_mid": mid,
                "provider": result.provider_name,
            })
            try:
                from notifications.telegram import send_alert
                await send_alert(
                    f"⚠️ <b>UNUSUAL SIGNAL REJECTED</b>\n"
                    f"Market: <i>{market.question[:60]}</i>\n"
                    f"Edge: {edge*100:.1f}% (limit: 35%) — likely data error, skipped."
                )
            except Exception:
                pass  # never let alert failure break the cycle
            continue

        # Tag signal with session label + hours_to_close for paper trader routing
        session_label = session_for_hours(hours_left, market.question)
        enriched_meta = {**(result.metadata or {}), "session": session_label, "hours_to_close": round(hours_left, 1)}

        sig = Signal(
            market_id=market.id,
            provider_name=result.provider_name,
            fair_probability=result.fair_probability,
            confidence=result.confidence,
            metadata_json=enriched_meta,
            market_midpoint=mid,
            edge=edge,
        )
        session.add(sig)
        signals.append(sig)
        log.info(
            "signal_generated",
            extra={
                "market_id": market.id,
                "edge": edge,
                "confidence": result.confidence,
                "provider": result.provider_name,
            }
        )

    await session.commit()
    return signals
