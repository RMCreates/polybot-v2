"""
Kalshi consensus signal source.

Fetches open Kalshi markets and fuzzy-matches against a Polymarket question
to find an external probability estimate.

Public API — no auth required.
Cache TTL controlled by settings.kalshi_cache_ttl_seconds (default 300s).
"""
from __future__ import annotations

import time
from difflib import SequenceMatcher
from typing import Optional, Tuple

import httpx

from config.settings import settings
from observability.logger import get_logger

log = get_logger(__name__)

# Module-level cache — survives across calls within a process
_kalshi_cache: dict = {"markets": [], "fetched_at": 0.0}


async def get_kalshi_markets() -> list[dict]:
    """
    Fetch open markets from Kalshi API with module-level TTL cache.
    Returns a list of market dicts (may be empty on failure).
    """
    now = time.monotonic()
    if now - _kalshi_cache["fetched_at"] < settings.kalshi_cache_ttl_seconds:
        return _kalshi_cache["markets"]

    url = f"{settings.kalshi_api_url}/markets"
    params = {"status": "open", "limit": 200}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                log.warning(
                    "kalshi_fetch_failed",
                    extra={"status": resp.status_code, "url": url},
                )
                return _kalshi_cache["markets"]  # return stale cache rather than empty

            data = resp.json()
            markets = data.get("markets", [])
            if not isinstance(markets, list):
                markets = []

            _kalshi_cache["markets"] = markets
            _kalshi_cache["fetched_at"] = now
            log.info("kalshi_markets_fetched", extra={"count": len(markets)})
            return markets

    except Exception as e:
        log.warning("kalshi_fetch_exception", extra={"error": str(e)})
        return _kalshi_cache["markets"]  # return stale cache on failure


async def find_kalshi_probability(
    question: str,
) -> Optional[Tuple[float, float, dict]]:
    """
    Fuzzy-match a Polymarket question against open Kalshi markets.

    Returns (probability, similarity, metadata) where:
      - probability = (yes_bid + yes_ask) / 200.0   (Kalshi uses cents 0-100)
      - similarity  = SequenceMatcher ratio (0.0 – 1.0)
      - metadata    = dict with kalshi_ticker, kalshi_title, yes_bid, yes_ask, similarity

    Returns None if no market matches above settings.kalshi_similarity_threshold.
    """
    markets = await get_kalshi_markets()
    if not markets:
        return None

    best_match = None
    best_sim = 0.0

    q_lower = question.lower()

    for market in markets:
        title = market.get("title", "")
        if not title:
            continue

        sim = SequenceMatcher(None, q_lower, title.lower()).ratio()
        if sim > best_sim:
            best_sim = sim
            best_match = market

    if best_match is None or best_sim < settings.kalshi_similarity_threshold:
        return None

    # New Kalshi API uses last_price_dollars (e.g. 0.55 = 55% probability)
    # Fallback to legacy yes_bid/yes_ask cents fields if present
    yes_bid = best_match.get("yes_bid")
    yes_ask = best_match.get("yes_ask")
    last_price = best_match.get("last_price_dollars")

    if yes_bid is not None and yes_ask is not None and (float(yes_bid) + float(yes_ask)) > 0:
        probability = (float(yes_bid) + float(yes_ask)) / 200.0
    elif last_price is not None and float(last_price) > 0:
        probability = float(last_price)
    else:
        return None  # No usable price data

    # Guard against degenerate prices
    if probability <= 0.01 or probability >= 0.99:
        return None

    metadata = {
        "kalshi_ticker": best_match.get("ticker", ""),
        "kalshi_title": best_match.get("title", ""),
        "kalshi_yes_bid": yes_bid,
        "kalshi_yes_ask": yes_ask,
        "kalshi_last_price": last_price,
        "similarity": round(best_sim, 4),
    }

    log.info(
        "kalshi_match_found",
        extra={
            "ticker": metadata["kalshi_ticker"],
            "similarity": metadata["similarity"],
            "probability": round(probability, 4),
        },
    )

    return probability, best_sim, metadata
