from __future__ import annotations
import httpx
from typing import Optional, Tuple
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from signals.base import SignalProvider, SignalResult
from signals.registry import register
from signals.sources.consensus_markets import (
    find_metaculus_probability,
    find_manifold_probability,
)
from signals.sources.polling_aggregator import fetch_fte_polling_average
from signals.sources.brave_search import search_political_news
from observability.logger import get_logger

log = get_logger(__name__)

POLITICAL_KEYWORDS = [
    "election", "president", "congress", "senate", "house",
    "governor", "poll", "approval", "vote", "ballot",
    "legislation", "impeach", "candidate", "party", "democrat",
    "republican", "primary", "inaugur",
]


class PoliticsSignal:
    """
    Signal plugin for US political prediction markets.
    Sources: Metaculus → Manifold → polling average → news sentiment.
    Brave Search fires only when consensus_conf < 0.55 (low-confidence fallback).
    Learning system dampens confidence when similar markets have recently lost.
    """
    name = "politics_signal"

    def __init__(self, session: Optional[AsyncSession] = None):
        self._session = session  # set by run_analysis_cycle before use

    def is_applicable(self, market) -> bool:
        return True  # process all markets; Manifold/Metaculus match what they can

    async def _fetch_consensus_probability(self, question: str) -> Tuple[float, float]:
        """Try Metaculus first, then Manifold. Returns (prob, confidence)."""
        result = await find_metaculus_probability(question)
        if result:
            return result
        result = await find_manifold_probability(question)
        if result:
            return result
        return (0.5, 0.0)

    async def _fetch_polling_average(self, question: str) -> Optional[float]:
        words = [w.strip("?.,") for w in question.split() if len(w.strip("?.,")) > 4]
        return await fetch_fte_polling_average(words[:4])

    async def _fetch_news_sentiment(self, question: str) -> float:
        """Returns -0.1 to +0.1 based on NewsAPI headline analysis."""
        from config.settings import settings
        api_key = settings.news_api_key.get_secret_value()
        if not api_key:
            return 0.0
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": question[:80],
                        "pageSize": 10,
                        "apiKey": api_key,
                        "sortBy": "publishedAt",
                        "language": "en",
                    }
                )
                if resp.status_code != 200:
                    return 0.0
            articles = resp.json().get("articles", [])
            if not articles:
                return 0.0
            positive_words = {"win", "lead", "ahead", "surge", "gain", "victory", "leads"}
            negative_words = {"lose", "behind", "drop", "fall", "trail", "defeat", "loses"}
            positive = 0
            negative = 0
            for article in articles:
                text = (
                    (article.get("title") or "") + " " +
                    (article.get("description") or "")
                ).lower()
                positive += sum(1 for w in positive_words if w in text)
                negative += sum(1 for w in negative_words if w in text)
            total = len(articles)
            raw_sentiment = (positive - negative) / total
            return max(-0.1, min(0.1, raw_sentiment * 0.05))
        except Exception as e:
            log.warning("news_sentiment_failed", extra={"error": str(e)})
            return 0.0

    async def _apply_learning_feedback(
        self,
        question: str,
        confidence: float,
        blend_used: str,
    ) -> float:
        """
        Query past lessons for similar markets and dampen confidence if patterns of loss emerge.
        Returns possibly-reduced confidence value.
        """
        if self._session is None:
            return confidence

        try:
            from learning.store import LearningStore
            store = LearningStore(self._session)
            lessons = await store.get_lessons_for_market(question)

            # Pattern 1: 3+ high-confidence losses on similar markets in the last 30 days
            recent_losses = [
                l for l in lessons
                if l.outcome_label == "LOSS"
                and l.confidence_at_entry > 0.50
                and l.created_at is not None
                and (datetime.now(timezone.utc) - l.created_at).days < 30
            ]
            if len(recent_losses) >= 3:
                new_conf = min(confidence, 0.40)
                log.info("learning_dampened_confidence", extra={
                    "market": question[:50],
                    "similar_losses": len(recent_losses),
                    "old_conf": confidence,
                    "new_conf": new_conf,
                })
                confidence = new_conf

            # Pattern 2: This market would use fallback blend and fallback keeps losing
            if blend_used == "fallback":
                fallback_losses = [l for l in recent_losses if l.blend_type == "fallback"]
                if len(fallback_losses) >= 2:
                    new_conf = min(confidence, 0.30)
                    log.info("learning_fallback_unreliable", extra={
                        "market": question[:50],
                        "fallback_losses": len(fallback_losses),
                        "new_conf": new_conf,
                    })
                    confidence = new_conf

        except Exception as e:
            log.warning("learning_feedback_failed", extra={"error": str(e)})

        return confidence

    async def compute_signal(self, market, orderbook_snapshot, price_history) -> SignalResult:
        question = market.question
        mid = orderbook_snapshot.midpoint if orderbook_snapshot is not None else 0.5

        consensus_prob, consensus_conf = await self._fetch_consensus_probability(question)
        polling_avg = await self._fetch_polling_average(question)
        # Only fetch news when consensus found something — preserves NewsAPI daily quota
        news_adj = await self._fetch_news_sentiment(question) if consensus_conf > 0.20 else 0.0

        if consensus_conf > 0.40:
            # Strong external match — use weighted blend
            weight_consensus = 0.60
            weight_polling = 0.30 if polling_avg is not None else 0.0
            weight_news = 0.05
            weight_market = max(0.0, 1.0 - weight_consensus - weight_polling - weight_news)

            fair_prob = (
                consensus_prob * weight_consensus
                + (polling_avg or mid) * weight_polling
                + mid * weight_market
                + (mid + news_adj) * weight_news
            )
            # Security Fix 6: Hard cap at 0.90 (was 0.95)
            confidence = min(consensus_conf * 0.9, 0.90)
            blend_used = "weighted"
        else:
            # No strong external match — use Brave Search as conditional fallback
            brave_adj = await search_political_news(question)
            fair_prob = mid + news_adj + brave_adj
            confidence = 0.20
            blend_used = "fallback"

        # Clamp to valid probability range
        fair_prob = max(0.01, min(0.99, fair_prob))

        # Security Fix 6 addendum: tiny edge with high confidence is suspicious
        if abs(fair_prob - mid) < 0.005:
            confidence = min(confidence, 0.20)
            log.info("tiny_edge_confidence_dampened", extra={
                "market": question[:50],
                "fair_prob": round(fair_prob, 4),
                "mid": round(mid, 4),
                "new_conf": confidence,
            })

        # Learning feedback: dampen confidence based on past similar losses
        confidence = await self._apply_learning_feedback(question, confidence, blend_used)

        return SignalResult(
            market_condition_id=market.condition_id,
            fair_probability=round(fair_prob, 4),
            confidence=round(confidence, 4),
            provider_name=self.name,
            metadata={
                "consensus_prob": consensus_prob,
                "consensus_conf": round(consensus_conf, 4),
                "polling_avg": polling_avg,
                "news_adjustment": round(news_adj, 4),
                "market_midpoint": mid,
                "blend_used": blend_used,
                "sources_used": (
                    ["metaculus_or_manifold", "polling", "news"]
                    if consensus_conf > 0.55
                    else ["news", "brave"]
                ),
            }
        )

register(PoliticsSignal())
