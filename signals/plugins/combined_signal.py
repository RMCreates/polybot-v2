from __future__ import annotations
import httpx
from difflib import SequenceMatcher
from datetime import datetime, timezone

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from signals.base import SignalProvider, SignalResult
from signals.registry import register
from signals.sources.kalshi_consensus import find_kalshi_probability
from observability.logger import get_logger

log = get_logger(__name__)


def _momentum_signal(price_history: list) -> Tuple[float, float]:
    """
    Linear regression on last 20 yes_price snapshots.
    Returns (edge, confidence).
    Edge   = slope × 3, capped at ±12%
    Confidence = R² of the trend line, max 0.55
    """
    if not price_history or len(price_history) < 5:
        return 0.0, 0.0

    # price_history is newest-first; reverse to oldest-first
    samples = [s.yes_price for s in reversed(price_history[:20])]
    n = len(samples)
    x_vals = list(range(n))
    x_mean = sum(x_vals) / n
    p_mean = sum(samples) / n

    num = sum((x - x_mean) * (p - p_mean) for x, p in zip(x_vals, samples))
    den = sum((x - x_mean) ** 2 for x in x_vals)
    if den < 1e-9:
        return 0.0, 0.0

    slope = num / den
    edge = max(-0.12, min(0.12, slope * 3))

    # R² — how linear the movement is
    predicted = [p_mean + slope * (x - x_mean) for x in x_vals]
    ss_res = sum((p - pred) ** 2 for p, pred in zip(samples, predicted))
    ss_tot = sum((p - p_mean) ** 2 for p in samples)
    r_sq = 1.0 - ss_res / max(ss_tot, 1e-9)

    confidence = max(0.0, min(0.55, r_sq * 0.60))
    return round(edge, 5), round(confidence, 4)


def _orderbook_signal(snapshot) -> Tuple[float, float, float]:
    """
    Order book bid/ask volume imbalance.
    Returns (edge, confidence, imbalance_ratio).
    Edge = imbalance × 0.05, max ±5%
    """
    if snapshot is None:
        return 0.0, 0.0, 0.0

    bids = snapshot.bids or []
    asks = snapshot.asks or []

    bid_vol = sum(float(b.get("size", 0)) for b in bids[:5])
    ask_vol = sum(float(a.get("size", 0)) for a in asks[:5])
    total = bid_vol + ask_vol

    if total < 50:
        return 0.0, 0.0, 0.0

    imbalance = (bid_vol - ask_vol) / total
    edge = max(-0.05, min(0.05, imbalance * 0.05))
    confidence = min(0.45, abs(imbalance) * 0.70)
    return round(edge, 5), round(confidence, 4), round(imbalance, 4)


async def _manifold_signal(question: str) -> Tuple[float, float]:
    """
    Quick Manifold consensus check. Returns (probability, similarity_confidence).
    Returns (0.0, 0.0) if no match or request fails.
    """
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(
                "https://api.manifold.markets/v0/markets",
                params={"term": question[:80], "limit": 10},
            )
            if resp.status_code != 200:
                return 0.0, 0.0
        markets = resp.json()
        for m in markets:
            sim = SequenceMatcher(None, question.lower(), m.get("question", "").lower()).ratio()
            prob = m.get("probability")
            if sim > 0.40 and prob is not None:
                log.info("manifold_match_found", extra={"sim": round(sim, 3), "prob": prob})
                return float(prob), float(sim)
    except Exception as e:
        log.warning("manifold_fetch_failed", extra={"error": str(e)})
    return 0.0, 0.0


class CombinedSignal:
    """
    Signal plugin combining price momentum, order book imbalance,
    and optional Manifold consensus. No Metaculus. No NewsAPI.
    Works entirely on data the bot already collects.

    Bootstrap mode: first 30 resolved trades — trades aggressively
    to build the learning dataset as fast as possible.
    """
    name = "combined_signal"

    def __init__(self, session=None):
        self._session = session

    def is_applicable(self, market) -> bool:
        return True  # all markets — let the signal decide

    async def compute_signal(self, market, orderbook_snapshot, price_history) -> SignalResult:
        question = market.question
        mid = orderbook_snapshot.midpoint if orderbook_snapshot is not None else 0.5

        # ── Signal A: Price Momentum ──────────────────────────────────────────
        mom_edge, mom_conf = _momentum_signal(price_history)

        # ── Signal B: Order Book Imbalance ────────────────────────────────────
        ob_edge, ob_conf, ob_imbalance = _orderbook_signal(orderbook_snapshot)

        # ── Signal C: Manifold Consensus (optional) ───────────────────────────
        manifold_prob, manifold_conf = await _manifold_signal(question)

        # ── Signal D: Kalshi Consensus (highest priority) ─────────────────
        kalshi_result = await find_kalshi_probability(question)
        kalshi_prob, kalshi_conf = (0.0, 0.0)
        kalshi_meta = {}
        if kalshi_result is not None:
            kalshi_prob, kalshi_conf, kalshi_meta = kalshi_result

        # ── Blend ─────────────────────────────────────────────────────────
        sources_used = []

        if kalshi_conf > 0.45 and manifold_conf > 0.40:
            # Dual consensus — both external sources agree (highest weight)
            fair_prob = (
                kalshi_prob * 0.30
                + manifold_prob * 0.25
                + (mid + mom_edge) * 0.25
                + (mid + ob_edge) * 0.20
            )
            confidence = min(0.55,
                kalshi_conf * 0.30
                + manifold_conf * 0.25
                + mom_conf * 0.25
                + ob_conf * 0.20
            )
            blend_used = "dual_consensus"
            sources_used = ["kalshi", "manifold"]
            if mom_conf > 0.05:
                sources_used.append("momentum")
            if ob_conf > 0.05:
                sources_used.append("orderbook")

        elif kalshi_conf > 0.45:
            # Kalshi only — strong single external source
            fair_prob = (
                kalshi_prob * 0.50
                + (mid + mom_edge) * 0.30
                + (mid + ob_edge) * 0.20
            )
            confidence = min(0.50,
                kalshi_conf * 0.50
                + mom_conf * 0.30
                + ob_conf * 0.20
            )
            blend_used = "kalshi_weighted"
            sources_used = ["kalshi"]
            if mom_conf > 0.05:
                sources_used.append("momentum")
            if ob_conf > 0.05:
                sources_used.append("orderbook")

        elif manifold_conf > 0.40:
            # Manifold only (existing logic preserved)
            fair_prob = (
                manifold_prob * 0.50
                + (mid + mom_edge) * 0.30
                + (mid + ob_edge) * 0.20
            )
            confidence = min(0.50,
                manifold_conf * 0.50
                + mom_conf * 0.30
                + ob_conf * 0.20
            )
            blend_used = "manifold_weighted"
            sources_used = ["manifold"]
            if mom_conf > 0.05:
                sources_used.append("momentum")
            if ob_conf > 0.05:
                sources_used.append("orderbook")

        elif mom_conf > 0.05 or ob_conf > 0.05:
            # Internal signals only (existing logic preserved)
            total_conf = mom_conf + ob_conf
            if total_conf > 0:
                weighted_edge = (mom_edge * mom_conf + ob_edge * ob_conf) / total_conf
            else:
                weighted_edge = 0.0
            fair_prob = mid + weighted_edge
            confidence = min(0.45, total_conf / 2)
            blend_used = "internal"
            if mom_conf > 0.05:
                sources_used.append("momentum")
            if ob_conf > 0.05:
                sources_used.append("orderbook")

        else:
            # No signal
            fair_prob = mid
            confidence = 0.05
            blend_used = "no_signal"

        # ── Learning feedback (dampen if similar markets keep losing) ─────────
        if self._session is not None and blend_used != "no_signal":
            try:
                from learning.store import LearningStore
                store = LearningStore(self._session)
                lessons = await store.get_lessons_for_market(question)
                recent_losses = [
                    l for l in lessons
                    if l.outcome_label == "LOSS"
                    and l.confidence_at_entry > 0.30
                    and l.created_at is not None
                    and (_utcnow() - l.created_at).days < 30
                ]
                if len(recent_losses) >= 3:
                    confidence = min(confidence, 0.30)
                    log.info("learning_dampened_confidence", extra={
                        "market": question[:50],
                        "losses": len(recent_losses),
                        "new_conf": confidence,
                    })
            except Exception as e:
                log.warning("learning_feedback_failed", extra={"error": str(e)})

        fair_prob = max(0.01, min(0.99, fair_prob))

        return SignalResult(
            market_condition_id=market.condition_id,
            fair_probability=round(fair_prob, 4),
            confidence=round(confidence, 4),
            provider_name=self.name,
            metadata={
                "momentum_edge": mom_edge,
                "momentum_conf": mom_conf,
                "ob_edge": ob_edge,
                "ob_conf": ob_conf,
                "ob_imbalance": ob_imbalance,
                "manifold_conf": round(manifold_conf, 4),
                "kalshi_prob": round(kalshi_prob, 4),
                "kalshi_conf": round(kalshi_conf, 4),
                "kalshi_ticker": kalshi_meta.get("kalshi_ticker", ""),
                "kalshi_title": kalshi_meta.get("kalshi_title", ""),
                "market_midpoint": mid,
                "blend_used": blend_used,
                "sources_used": sources_used,
            },
        )


register(CombinedSignal())
