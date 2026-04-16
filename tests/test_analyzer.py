import pytest
from unittest.mock import MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import Base, Market, OrderBookSnapshot, Signal

@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()

async def test_null_signal_returns_midpoint():
    import signals.plugins.null_signal  # trigger registration
    from signals.registry import get
    from db.models import Market, OrderBookSnapshot

    provider = get("null_signal")
    market = MagicMock(spec=Market)
    market.condition_id = "0xtest"

    snapshot = MagicMock(spec=OrderBookSnapshot)
    snapshot.midpoint = 0.62

    result = await provider.compute_signal(market, snapshot, [])
    assert result.fair_probability == pytest.approx(0.62)
    assert result.confidence == 0.0
    assert result.provider_name == "null_signal"

async def test_null_signal_is_applicable_to_all():
    import signals.plugins.null_signal
    from signals.registry import get
    provider = get("null_signal")
    market = MagicMock()
    assert provider.is_applicable(market) is True

async def test_compute_edge_positive():
    from analyzer.edge_analyzer import compute_edge
    edge = compute_edge(fair_prob=0.65, market_mid=0.60)
    assert edge == pytest.approx(0.05, abs=1e-6)

async def test_compute_edge_zero_for_null():
    from analyzer.edge_analyzer import compute_edge
    assert compute_edge(fair_prob=0.50, market_mid=0.50) == pytest.approx(0.0)

async def test_compute_edge_negative():
    from analyzer.edge_analyzer import compute_edge
    edge = compute_edge(fair_prob=0.40, market_mid=0.55)
    assert edge == pytest.approx(-0.15, abs=1e-6)

async def test_run_analysis_cycle_null_signal(session):
    from analyzer.edge_analyzer import run_analysis_cycle
    import signals.plugins.null_signal  # register

    # Insert market + orderbook snapshot
    market = Market(
        condition_id="0xanalysis1", question_id="0xq_a1",
        question="Analysis test?", yes_token_id="ty_a1", no_token_id="tn_a1"
    )
    session.add(market)
    await session.commit()

    snap = OrderBookSnapshot(
        market_id=market.id, token_id="ty_a1",
        bids=[], asks=[], best_bid=0.0, best_ask=1.0,
        spread=0.0, midpoint=0.55
    )
    session.add(snap)
    await session.commit()

    # Patch settings to use null_signal
    import config.settings as cs
    original = cs.settings.signal_provider
    cs.settings.__dict__["signal_provider"] = "null_signal"

    signals = await run_analysis_cycle(session, [market])

    cs.settings.__dict__["signal_provider"] = original

    assert len(signals) == 1
    assert signals[0].edge == pytest.approx(0.0, abs=1e-6)
    assert signals[0].fair_probability == pytest.approx(0.55)
    assert signals[0].provider_name == "null_signal"

async def test_registry_raises_for_unknown_provider():
    from signals.registry import get
    with pytest.raises(KeyError, match="No signal provider registered"):
        get("nonexistent_provider")
