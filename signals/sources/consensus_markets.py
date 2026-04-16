from __future__ import annotations
import httpx
from difflib import SequenceMatcher
from typing import Optional, Tuple
from observability.logger import get_logger

log = get_logger(__name__)

METACULUS_API = "https://www.metaculus.com/api/questions/"  # v3 — v2 requires auth
MANIFOLD_API = "https://api.manifold.markets/v0/markets"
_HEADERS = {"User-Agent": "polymarket-research-bot/1.0", "Accept": "application/json"}

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

async def find_metaculus_probability(question: str, limit: int = 10) -> Optional[Tuple[float, float]]:
    """Returns (probability, similarity_confidence) or None if no good match found."""
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            resp = await client.get(
                METACULUS_API,
                params={"search": question[:100], "limit": limit},
                headers=_HEADERS,
            )
            if resp.status_code != 200:
                log.warning("metaculus_fetch_failed", extra={"status": resp.status_code})
                return None
            data = resp.json()
        for item in data.get("results", []):
            title = item.get("title", "") or item.get("question", {}).get("title", "")
            sim = _similarity(question, title)
            # v3: community_weighting is the aggregated probability (0-1)
            prob = item.get("community_weighting")
            if prob is None:
                # fallback: try v2-style nested field
                community = item.get("community_prediction") or {}
                prob = (community.get("full") or {}).get("q2")
            if sim > 0.40 and prob is not None:
                log.info("metaculus_match_found", extra={"sim": round(sim, 3), "prob": prob})
                return (float(prob), float(sim))
    except Exception as e:
        log.warning("metaculus_fetch_failed", extra={"error": str(e)})
    return None

async def find_manifold_probability(question: str, limit: int = 10) -> Optional[Tuple[float, float]]:
    """Returns (probability, similarity_confidence) or None."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                MANIFOLD_API,
                params={"term": question[:80], "limit": limit}
            )
            if resp.status_code != 200:
                return None
            markets = resp.json()
        for m in markets:
            sim = _similarity(question, m.get("question", ""))
            prob = m.get("probability")
            if sim > 0.40 and prob is not None:
                log.info("manifold_match_found", extra={"sim": round(sim, 3), "prob": prob})
                return (float(prob), float(sim))
    except Exception as e:
        log.warning("manifold_fetch_failed", extra={"error": str(e)})
    return None
