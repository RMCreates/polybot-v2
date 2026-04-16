from __future__ import annotations
import httpx
from config.settings import settings
from observability.logger import get_logger

log = get_logger(__name__)

POSITIVE_WORDS = {"win", "lead", "ahead", "surge", "victory", "leads", "gain", "favored"}
NEGATIVE_WORDS = {"lose", "trail", "drop", "behind", "defeat", "loses", "fall", "trailing"}


async def search_political_news(question: str) -> float:
    """
    Returns a sentiment score [-0.1, +0.1] based on recent news headlines.
    Only fires when:
      - consensus_conf < 0.55 (no strong external match available)
      - brave_api_key is set in .env

    Estimates 5-15 API calls/day maximum at 30-min cycle intervals.
    Returns 0.0 silently if API key missing or request fails.
    """
    api_key = settings.brave_api_key.get_secret_value()
    if not api_key:
        return 0.0

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/news/search",
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                params={"q": question[:100], "count": 5, "freshness": "pd"},
            )
            if resp.status_code != 200:
                log.warning("brave_search_failed", extra={
                    "status": resp.status_code,
                    "question": question[:50],
                })
                return 0.0

        results = resp.json().get("results", [])
        if not results:
            return 0.0

        pos = sum(
            1 for r in results
            if any(w in r.get("title", "").lower() for w in POSITIVE_WORDS)
        )
        neg = sum(
            1 for r in results
            if any(w in r.get("title", "").lower() for w in NEGATIVE_WORDS)
        )
        # Scale to [-0.1, +0.1] — Brave alone shouldn't dominate the signal
        score = (pos - neg) / max(len(results), 1) * 0.05
        return max(-0.1, min(0.1, score))

    except Exception as e:
        log.warning("brave_search_exception", extra={"error": str(e), "question": question[:50]})
        return 0.0
