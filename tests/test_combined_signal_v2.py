"""Smoke tests for the V2 combined signal (Kalshi + Manifold + momentum + OB blend)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from signals.plugins.combined_signal import CombinedSignal


class _FakeMarket:
    def __init__(self, question: str, condition_id: str = "cid1"):
        self.question = question
        self.condition_id = condition_id


class _FakeSnap:
    def __init__(self, midpoint=0.50, bids=None, asks=None):
        self.midpoint = midpoint
        self.bids = bids or [{"size": 100}] * 5
        self.asks = asks or [{"size": 100}] * 5


def _make_price_history(values):
    class S:
        def __init__(self, p):
            self.yes_price = p
    return [S(v) for v in values]


@pytest.mark.asyncio
async def test_dual_consensus_when_kalshi_and_manifold_both_strong():
    """When both Kalshi (sim > 0.45) and Manifold (sim > 0.40) match, blend uses dual_consensus."""
    signal = CombinedSignal()
    market = _FakeMarket("Will Bitcoin hit 200k by year end?")
    snap = _FakeSnap(midpoint=0.45)
    history = _make_price_history([0.45, 0.46, 0.46, 0.47, 0.47, 0.48])

    with patch("signals.plugins.combined_signal.find_kalshi_probability",
               AsyncMock(return_value=(0.60, 0.80, {"kalshi_ticker": "BTC-200K", "kalshi_title": "Bitcoin at 200k"}))), \
         patch("signals.plugins.combined_signal._manifold_signal",
               AsyncMock(return_value=(0.58, 0.70))):
        result = await signal.compute_signal(market, snap, history)

    assert result.metadata["blend_used"] == "dual_consensus"
    assert "kalshi" in result.metadata["sources_used"]
    assert "manifold" in result.metadata["sources_used"]
    assert result.metadata["kalshi_ticker"] == "BTC-200K"


@pytest.mark.asyncio
async def test_kalshi_weighted_when_only_kalshi_strong():
    signal = CombinedSignal()
    market = _FakeMarket("Will X happen?")
    snap = _FakeSnap(midpoint=0.50)
    history = _make_price_history([0.50] * 6)

    with patch("signals.plugins.combined_signal.find_kalshi_probability",
               AsyncMock(return_value=(0.65, 0.75, {"kalshi_ticker": "X-1", "kalshi_title": "Will X"}))), \
         patch("signals.plugins.combined_signal._manifold_signal",
               AsyncMock(return_value=(0.0, 0.0))):
        result = await signal.compute_signal(market, snap, history)

    assert result.metadata["blend_used"] == "kalshi_weighted"
    assert result.metadata["sources_used"] == ["kalshi"] or "kalshi" in result.metadata["sources_used"]


@pytest.mark.asyncio
async def test_manifold_weighted_when_only_manifold_strong():
    signal = CombinedSignal()
    market = _FakeMarket("Manifold only market?")
    snap = _FakeSnap(midpoint=0.50)
    history = _make_price_history([0.50] * 6)

    with patch("signals.plugins.combined_signal.find_kalshi_probability",
               AsyncMock(return_value=None)), \
         patch("signals.plugins.combined_signal._manifold_signal",
               AsyncMock(return_value=(0.62, 0.50))):
        result = await signal.compute_signal(market, snap, history)

    assert result.metadata["blend_used"] == "manifold_weighted"
    assert "manifold" in result.metadata["sources_used"]


@pytest.mark.asyncio
async def test_no_signal_when_nothing_available():
    signal = CombinedSignal()
    market = _FakeMarket("Nothing matches")
    snap = _FakeSnap(midpoint=0.50, bids=[{"size": 1}], asks=[{"size": 1}])  # below 50 total vol
    history = _make_price_history([0.5, 0.5])  # < 5 samples

    with patch("signals.plugins.combined_signal.find_kalshi_probability",
               AsyncMock(return_value=None)), \
         patch("signals.plugins.combined_signal._manifold_signal",
               AsyncMock(return_value=(0.0, 0.0))):
        result = await signal.compute_signal(market, snap, history)

    assert result.metadata["blend_used"] == "no_signal"
    assert result.confidence == 0.05


@pytest.mark.asyncio
async def test_metadata_contains_kalshi_fields():
    signal = CombinedSignal()
    market = _FakeMarket("Q")
    snap = _FakeSnap()
    history = _make_price_history([0.5] * 6)

    with patch("signals.plugins.combined_signal.find_kalshi_probability",
               AsyncMock(return_value=None)), \
         patch("signals.plugins.combined_signal._manifold_signal",
               AsyncMock(return_value=(0.0, 0.0))):
        result = await signal.compute_signal(market, snap, history)

    assert "kalshi_prob" in result.metadata
    assert "kalshi_conf" in result.metadata
    assert "kalshi_ticker" in result.metadata
    assert "kalshi_title" in result.metadata
