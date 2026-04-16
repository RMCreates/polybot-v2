import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import Base, Market, Signal, Trade

@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()

async def test_paper_trade_is_executed_for_valid_signal(session):
    from paper_trader.paper_trader import run_paper_trading_cycle

    market = Market(
        condition_id="0xpt1", question_id="0xq_pt1", question="Paper trade test?",
        yes_token_id="ty_pt1", no_token_id="tn_pt1", liquidity_usd=5000.0
    )
    session.add(market)
    await session.commit()

    # High-confidence signal with enough edge to pass risk
    signal = Signal(
        market_id=market.id,
        provider_name="test",
        fair_probability=0.70,
        confidence=0.5,
        market_midpoint=0.60,
        edge=0.10,   # 10% edge — above 3% threshold
        metadata_json={},
    )
    session.add(signal)
    await session.commit()

    trades = await run_paper_trading_cycle(session, [signal], [market])
    assert len(trades) == 1
    assert trades[0].is_paper is True
    assert trades[0].side == "BUY_YES"
    assert trades[0].status == "OPEN"
    assert "risk_approved" in trades[0].reasoning_trace
    assert trades[0].reasoning_trace["risk_approved"] is True

async def test_paper_trade_rejected_for_low_edge(session):
    from paper_trader.paper_trader import run_paper_trading_cycle

    market = Market(
        condition_id="0xpt2", question_id="0xq_pt2", question="Low edge test?",
        yes_token_id="ty_pt2", no_token_id="tn_pt2"
    )
    session.add(market)
    await session.commit()

    # Edge too low (0.01 < 0.03 threshold)
    signal = Signal(
        market_id=market.id,
        provider_name="test",
        fair_probability=0.51,
        confidence=0.5,
        market_midpoint=0.50,
        edge=0.01,
        metadata_json={},
    )
    session.add(signal)
    await session.commit()

    trades = await run_paper_trading_cycle(session, [signal], [market])
    assert len(trades) == 0  # rejected

async def test_reasoning_trace_fully_populated(session):
    from paper_trader.paper_trader import run_paper_trading_cycle

    market = Market(
        condition_id="0xpt3", question_id="0xq_pt3", question="Trace test?",
        yes_token_id="ty_pt3", no_token_id="tn_pt3"
    )
    session.add(market)
    await session.commit()

    signal = Signal(
        market_id=market.id,
        provider_name="test",
        fair_probability=0.68,
        confidence=0.5,
        market_midpoint=0.60,
        edge=0.08,
        metadata_json={},
    )
    session.add(signal)
    await session.commit()

    trades = await run_paper_trading_cycle(session, [signal], [market])
    assert len(trades) == 1
    trace = trades[0].reasoning_trace
    # Must contain all key fields for full observability
    required_keys = [
        "signal_id", "market_id", "fair_probability", "market_midpoint",
        "edge", "confidence", "risk_approved", "risk_rules_applied",
        "proposed_size_usd", "final_size_usd", "is_favorable_trade"
    ]
    for key in required_keys:
        assert key in trace, f"Missing key in reasoning_trace: {key}"

async def test_close_position_calculates_pnl(session):
    from executor.paper_executor import PaperExecutor
    from risk.risk_engine import TradeProposal, RiskDecision

    market = Market(
        condition_id="0xclose1", question_id="0xq_close", question="Close test?",
        yes_token_id="ty_close", no_token_id="tn_close"
    )
    session.add(market)
    await session.commit()

    proposal = TradeProposal(
        market_condition_id="0xclose1", side="BUY_YES", token_id="ty_close",
        edge=0.10, fair_probability=0.70, market_midpoint=0.60,
        signal_confidence=0.5, proposed_size_usd=10.0
    )
    decision = RiskDecision(approved=True, final_size_usd=10.0)
    trace = {"market_id": market.id, "risk_approved": True}

    executor = PaperExecutor(session)
    trade = await executor.execute(proposal, decision, signal_id=None, reasoning_trace=trace)
    assert trade.status == "OPEN"

    # Close at 0.80 (YES resolved) — should profit
    closed = await executor.close_position(trade.id, exit_price=1.0)
    assert closed.status == "CLOSED"
    assert closed.pnl_usd == pytest.approx(4.0, abs=0.01)  # (1.0 - 0.60) * 10 = 4.0
    assert closed.exit_price == 1.0
