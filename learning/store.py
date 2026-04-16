from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional
from sqlalchemy import select, update, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import LearningEntry, Trade, Signal, Market
from observability.logger import get_logger

log = get_logger(__name__)

POLITICAL_KEYWORDS = [
    "election", "president", "congress", "senate", "governor",
    "vote", "ballot", "candidate", "democrat", "republican", "primary",
]

def _detect_category(question: str) -> str:
    q = question.lower()
    if any(kw in q for kw in POLITICAL_KEYWORDS):
        return "politics"
    return "general"

def _build_lesson_text(
    trade: Trade,
    signal: Optional[Signal],
    market: Market,
    outcome: Optional[str],
) -> str:
    q = market.question[:70]
    edge = round(signal.edge, 3) if signal else "?"
    conf = round(signal.confidence, 2) if signal else "?"
    blend = signal.metadata_json.get("blend_used", "?") if signal and signal.metadata_json else "?"

    if outcome is None:
        return (
            f"OPEN: {trade.side} on '{q}' at {trade.entry_price:.3f} | "
            f"edge={edge} conf={conf} via {blend}"
        )

    pnl = f"${trade.pnl_usd:+.2f}" if trade.pnl_usd is not None else "?"
    exit_p = f"{trade.exit_price:.3f}" if trade.exit_price is not None else "?"

    if outcome == "LOSS" and blend == "fallback":
        note = " — FALLBACK_BLEND_LOSS: no consensus data available"
    elif outcome == "WIN":
        note = ""
    else:
        note = ""

    return (
        f"{outcome} {pnl}: {trade.side} on '{q}' "
        f"entered {trade.entry_price:.3f} → resolved {exit_p} | "
        f"edge was {edge}, conf={conf}, blend={blend}{note}"
    )


class LearningStore:
    """Records trades, resolves outcomes, and feeds lessons back into signals."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_trade_placed(
        self,
        trade: Trade,
        signal: Signal,
        market: Market,
        session_label: str = "unknown",
    ) -> None:
        """Call immediately after a new paper trade is committed to DB."""
        sources = signal.metadata_json.get("sources_used", []) if signal.metadata_json else []
        blend = signal.metadata_json.get("blend_used", "unknown") if signal.metadata_json else "unknown"

        entry = LearningEntry(
            trade_id=trade.id,
            market_id=market.id,
            market_question=market.question,
            market_category=_detect_category(market.question),
            signal_sources_used=json.dumps(sources),
            blend_type=blend,
            edge_at_entry=signal.edge,
            confidence_at_entry=signal.confidence,
            side=trade.side,
            entry_price=trade.entry_price,
            outcome_label="OPEN",
            lesson_text=_build_lesson_text(trade, signal, market, outcome=None),
            session_label=session_label,
        )
        self.session.add(entry)
        await self.session.commit()
        log.info("learning_trade_recorded", extra={
            "trade_id": trade.id,
            "market": market.question[:50],
            "blend": blend,
        })

    async def record_trade_resolved(
        self,
        trade: Trade,
        market: Market,
    ) -> None:
        """Call after DataCollector resolves a trade. Updates outcome label and lesson text."""
        entry = (await self.session.execute(
            select(LearningEntry).where(LearningEntry.trade_id == trade.id)
        )).scalar_one_or_none()

        if not entry:
            log.warning("learning_entry_not_found", extra={"trade_id": trade.id})
            return

        # Load original signal for lesson text
        signal = None
        if trade.signal_id:
            signal = await self.session.get(Signal, trade.signal_id)

        pnl = trade.pnl_usd or 0.0
        if pnl > 0:
            outcome = "WIN"
        elif pnl < 0:
            outcome = "LOSS"
        else:
            outcome = "FLAT"

        entry.outcome_label = outcome
        entry.pnl_usd = pnl
        entry.exit_price = trade.exit_price
        entry.was_correct = pnl > 0
        entry.resolved_at = datetime.now(timezone.utc)
        entry.lesson_text = _build_lesson_text(trade, signal, market, outcome=outcome)

        await self.session.commit()
        log.info("learning_trade_resolved", extra={
            "trade_id": trade.id,
            "outcome": outcome,
            "pnl": pnl,
        })

    async def get_lessons_for_market(
        self,
        question: str,
        limit: int = 10,
    ) -> list[LearningEntry]:
        """
        Fuzzy-match question against past resolved lessons.
        Updates last_referenced_at for matched entries (resets the expiry clock).
        """
        resolved_labels = ("WIN", "LOSS", "FLAT")
        all_entries = list((await self.session.execute(
            select(LearningEntry)
            .where(LearningEntry.outcome_label.in_(resolved_labels))
            .order_by(LearningEntry.created_at.desc())
            .limit(200)
        )).scalars())

        # Fuzzy match — keep anything above 0.4 similarity
        scored = [
            (e, SequenceMatcher(None, question.lower(), e.market_question.lower()).ratio())
            for e in all_entries
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        matched = [e for e, score in scored if score > 0.4][:limit]

        # Update last_referenced_at for matched entries (resets 180-day expiry clock)
        now = datetime.now(timezone.utc)
        for entry in matched:
            entry.last_referenced_at = now
            entry.reference_count = (entry.reference_count or 0) + 1
        if matched:
            await self.session.commit()

        return matched

    async def archive_stale_lessons(self) -> int:
        """
        Archive lessons that haven't been consulted in 180 days.
        A lesson is only forgotten if no similar market has appeared in 6 months.
        """
        stale_cutoff = datetime.now(timezone.utc) - timedelta(days=180)
        result = await self.session.execute(
            update(LearningEntry)
            .where(
                LearningEntry.outcome_label.in_(("WIN", "LOSS", "FLAT")),
                or_(
                    and_(
                        LearningEntry.last_referenced_at.isnot(None),
                        LearningEntry.last_referenced_at < stale_cutoff,
                    ),
                    and_(
                        LearningEntry.last_referenced_at.is_(None),
                        LearningEntry.created_at < stale_cutoff,
                    ),
                )
            )
            .values(outcome_label="ARCHIVED")
        )
        await self.session.commit()
        count = result.rowcount
        if count > 0:
            log.info("learning_entries_archived", extra={"count": count})
        return count

    async def get_performance_summary(self) -> dict:
        """Summary stats for the /learning dashboard page."""
        all_resolved = list((await self.session.execute(
            select(LearningEntry)
            .where(LearningEntry.outcome_label.in_(("WIN", "LOSS", "FLAT")))
            .order_by(LearningEntry.created_at.desc())
        )).scalars())

        wins = sum(1 for e in all_resolved if e.outcome_label == "WIN")
        losses = sum(1 for e in all_resolved if e.outcome_label == "LOSS")
        total_pnl = sum(e.pnl_usd or 0.0 for e in all_resolved)
        avg_conf = (
            sum(e.confidence_at_entry for e in all_resolved) / len(all_resolved)
            if all_resolved else 0.0
        )
        high_conf_losses = [
            e for e in all_resolved
            if e.outcome_label == "LOSS" and e.confidence_at_entry > 0.50
        ]
        fallback_results = [e for e in all_resolved if e.blend_type == "fallback"]
        fallback_wins = sum(1 for e in fallback_results if e.outcome_label == "WIN")

        return {
            "total_resolved": len(all_resolved),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / max(len(all_resolved), 1), 3),
            "total_pnl": round(total_pnl, 2),
            "avg_confidence_at_entry": round(avg_conf, 3),
            "high_conf_losses": len(high_conf_losses),
            "fallback_total": len(fallback_results),
            "fallback_wins": fallback_wins,
            "fallback_win_rate": round(fallback_wins / max(len(fallback_results), 1), 3),
            "recent_lessons": [
                {
                    "lesson_text": e.lesson_text,
                    "outcome_label": e.outcome_label,
                    "pnl_usd": e.pnl_usd,
                    "edge_at_entry": e.edge_at_entry,
                    "confidence_at_entry": e.confidence_at_entry,
                    "blend_type": e.blend_type,
                    "created_at": e.created_at.strftime("%Y-%m-%d %H:%M") if e.created_at else "",
                    "market_question": e.market_question[:80],
                }
                for e in all_resolved[:20]
            ],
        }

    async def get_blend_performance_by_session(self) -> dict:
        """Win rates per blend_type per session_label for feedback loop."""
        resolved = list((await self.session.execute(
            select(LearningEntry)
            .where(LearningEntry.outcome_label.in_(("WIN", "LOSS", "FLAT")))
        )).scalars())

        result: dict = {}
        for entry in resolved:
            sess = entry.session_label or "unknown"
            blend = entry.blend_type or "unknown"
            if sess not in result:
                result[sess] = {}
            if blend not in result[sess]:
                result[sess][blend] = {"wins": 0, "losses": 0, "total": 0}
            result[sess][blend]["total"] += 1
            if entry.outcome_label == "WIN":
                result[sess][blend]["wins"] += 1
            elif entry.outcome_label == "LOSS":
                result[sess][blend]["losses"] += 1

        # Compute win rates
        for sess in result:
            for blend in result[sess]:
                d = result[sess][blend]
                d["win_rate"] = round(d["wins"] / max(d["total"], 1), 3)

        return result
