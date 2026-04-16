"""Smoke tests for the Kalshi consensus source."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from signals.sources import kalshi_consensus


@pytest.fixture(autouse=True)
def clear_cache():
    kalshi_consensus._kalshi_cache = {"markets": [], "fetched_at": 0.0}
    yield
    kalshi_consensus._kalshi_cache = {"markets": [], "fetched_at": 0.0}


@pytest.mark.asyncio
async def test_find_kalshi_probability_returns_none_on_empty():
    with patch.object(kalshi_consensus, "get_kalshi_markets", AsyncMock(return_value=[])):
        result = await kalshi_consensus.find_kalshi_probability("Will it rain tomorrow?")
    assert result is None


@pytest.mark.asyncio
async def test_find_kalshi_probability_matches_above_threshold():
    fake_markets = [
        {"title": "Will it rain tomorrow in NYC?", "ticker": "RAIN-NYC", "yes_bid": 55, "yes_ask": 57},
        {"title": "Totally unrelated basketball game", "ticker": "NBA-XX", "yes_bid": 10, "yes_ask": 15},
    ]
    with patch.object(kalshi_consensus, "get_kalshi_markets", AsyncMock(return_value=fake_markets)):
        result = await kalshi_consensus.find_kalshi_probability("Will it rain tomorrow in NYC?")
    assert result is not None
    prob, sim, meta = result
    assert 0.55 <= prob <= 0.57
    assert sim > 0.9
    assert meta["kalshi_ticker"] == "RAIN-NYC"


@pytest.mark.asyncio
async def test_find_kalshi_probability_rejects_below_threshold():
    fake_markets = [
        {"title": "Something totally different", "ticker": "XYZ", "yes_bid": 40, "yes_ask": 50},
    ]
    with patch.object(kalshi_consensus, "get_kalshi_markets", AsyncMock(return_value=fake_markets)):
        result = await kalshi_consensus.find_kalshi_probability("Will Bitcoin hit 200k?")
    assert result is None


@pytest.mark.asyncio
async def test_get_kalshi_markets_uses_cache():
    import time
    kalshi_consensus._kalshi_cache = {
        "markets": [{"title": "cached"}],
        "fetched_at": time.monotonic(),  # fresh
    }
    result = await kalshi_consensus.get_kalshi_markets()
    assert result == [{"title": "cached"}]


@pytest.mark.asyncio
async def test_get_kalshi_markets_handles_http_failure_gracefully():
    """On HTTP failure, returns the (possibly empty) cached list, never raises."""
    with patch("signals.sources.kalshi_consensus.httpx.AsyncClient") as mock_client:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
        result = await kalshi_consensus.get_kalshi_markets()
    assert result == []
