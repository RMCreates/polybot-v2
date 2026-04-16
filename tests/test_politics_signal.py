import pytest
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.fixture
def political_market():
    m = MagicMock()
    m.condition_id = "0xpol1"
    m.question = "Will the Democratic candidate win the 2026 Senate race in Pennsylvania?"
    m.liquidity_usd = 50000.0
    return m

@pytest.fixture
def non_political_market():
    m = MagicMock()
    m.condition_id = "0xcrypto1"
    m.question = "Will BTC hit $100k in 2025?"
    return m

@pytest.fixture
def snapshot():
    s = MagicMock()
    s.midpoint = 0.55
    return s

def test_is_applicable_political(political_market):
    import signals.plugins.politics_signal
    from signals.registry import get
    plugin = get("politics_signal")
    assert plugin.is_applicable(political_market) is True

def test_is_applicable_non_political(non_political_market):
    import signals.plugins.politics_signal
    from signals.registry import get
    plugin = get("politics_signal")
    assert plugin.is_applicable(non_political_market) is False

@pytest.mark.asyncio
async def test_compute_signal_with_strong_consensus(political_market, snapshot):
    import signals.plugins.politics_signal
    from signals.registry import get
    plugin = get("politics_signal")

    with patch.object(plugin, "_fetch_consensus_probability", new=AsyncMock(return_value=(0.62, 0.75))):
        with patch.object(plugin, "_fetch_polling_average", new=AsyncMock(return_value=0.60)):
            with patch.object(plugin, "_fetch_news_sentiment", new=AsyncMock(return_value=0.0)):
                result = await plugin.compute_signal(political_market, snapshot, [])

    assert 0.0 < result.fair_probability < 1.0
    assert result.confidence > 0.5  # strong match → high confidence
    assert result.provider_name == "politics_signal"
    assert result.metadata["blend_used"] == "weighted"

@pytest.mark.asyncio
async def test_compute_signal_falls_back_when_no_match(political_market, snapshot):
    import signals.plugins.politics_signal
    from signals.registry import get
    plugin = get("politics_signal")

    with patch.object(plugin, "_fetch_consensus_probability", new=AsyncMock(return_value=(0.5, 0.0))):
        with patch.object(plugin, "_fetch_polling_average", new=AsyncMock(return_value=None)):
            with patch.object(plugin, "_fetch_news_sentiment", new=AsyncMock(return_value=0.0)):
                result = await plugin.compute_signal(political_market, snapshot, [])

    assert result.confidence == pytest.approx(0.10)
    assert result.metadata["blend_used"] == "fallback"

@pytest.mark.asyncio
async def test_fair_probability_stays_in_bounds(political_market, snapshot):
    import signals.plugins.politics_signal
    from signals.registry import get
    plugin = get("politics_signal")

    # Even extreme inputs must produce valid probability
    with patch.object(plugin, "_fetch_consensus_probability", new=AsyncMock(return_value=(0.999, 0.99))):
        with patch.object(plugin, "_fetch_polling_average", new=AsyncMock(return_value=0.999)):
            with patch.object(plugin, "_fetch_news_sentiment", new=AsyncMock(return_value=0.1)):
                result = await plugin.compute_signal(political_market, snapshot, [])

    assert 0.01 <= result.fair_probability <= 0.99

@pytest.mark.asyncio
async def test_similarity_matching():
    from signals.sources.consensus_markets import _similarity
    # Identical strings → 1.0
    assert _similarity("test question", "test question") == pytest.approx(1.0)
    # Empty vs something → 0.0
    assert _similarity("", "anything") == pytest.approx(0.0)
    # Reasonable similarity
    score = _similarity("Will Biden win the 2024 election", "Will Biden win 2024")
    assert score > 0.7

@pytest.mark.asyncio
async def test_politics_plugin_registered():
    import signals.plugins.politics_signal
    from signals.registry import list_providers
    providers = list_providers()
    assert "politics_signal" in providers
    assert "null_signal" in providers  # null still there too
