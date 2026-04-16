import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import Base, Market, PriceSnapshot, OrderBookSnapshot, Signal, Trade, PerformanceLog

@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()

async def test_market_creation(session):
    market = Market(
        condition_id="0xabc123",
        question_id="0xdef456",
        question="Will inflation drop below 3%?",
        yes_token_id="tok_yes",
        no_token_id="tok_no",
    )
    session.add(market)
    await session.commit()
    result = await session.get(Market, 1)
    assert result.condition_id == "0xabc123"
    assert result.is_active is True
    assert result.volume_usd == 0.0

async def test_price_snapshot(session):
    market = Market(condition_id="0xprice", question_id="0xq", question="Test?", yes_token_id="t1", no_token_id="t2")
    session.add(market)
    await session.commit()
    snap = PriceSnapshot(market_id=market.id, yes_price=0.62, no_price=0.38)
    session.add(snap)
    await session.commit()
    result = await session.get(PriceSnapshot, 1)
    assert result.yes_price == 0.62
    assert result.no_price == 0.38

async def test_orderbook_snapshot(session):
    market = Market(condition_id="0xob", question_id="0xq2", question="OB test?", yes_token_id="ty", no_token_id="tn")
    session.add(market)
    await session.commit()
    snap = OrderBookSnapshot(
        market_id=market.id,
        token_id="ty",
        bids=[{"price": "0.61", "size": "100"}],
        asks=[{"price": "0.63", "size": "80"}],
        best_bid=0.61,
        best_ask=0.63,
        spread=0.02,
        midpoint=0.62,
    )
    session.add(snap)
    await session.commit()
    result = await session.get(OrderBookSnapshot, 1)
    assert result.midpoint == 0.62
    assert result.spread == 0.02
    assert len(result.bids) == 1

async def test_signal_and_trade(session):
    market = Market(condition_id="0xtrade", question_id="0xq3", question="Trade test?", yes_token_id="ty2", no_token_id="tn2")
    session.add(market)
    await session.commit()
    signal = Signal(
        market_id=market.id,
        provider_name="test_provider",
        fair_probability=0.65,
        confidence=0.7,
        market_midpoint=0.60,
        edge=0.05,
    )
    session.add(signal)
    await session.commit()
    trade = Trade(
        signal_id=signal.id,
        market_id=market.id,
        side="BUY_YES",
        token_id="ty2",
        size_usd=10.0,
        entry_price=0.60,
        reasoning_trace={"edge": 0.05, "risk_approved": True},
    )
    session.add(trade)
    await session.commit()
    result = await session.get(Trade, 1)
    assert result.side == "BUY_YES"
    assert result.status == "OPEN"
    assert result.is_paper is True
    assert result.reasoning_trace["edge"] == 0.05

async def test_all_tables_created(session):
    from sqlalchemy import text
    result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
    tables = {row[0] for row in result}
    assert "markets" in tables
    assert "price_snapshots" in tables
    assert "orderbook_snapshots" in tables
    assert "signals" in tables
    assert "trades" in tables
    assert "performance_logs" in tables
