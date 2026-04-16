import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import Base, Market, PriceSnapshot

MOCK_MARKETS_RESPONSE = [
    {
        "conditionId": "0xabc123",
        "questionId": "0xdef456",
        "question": "Will inflation drop below 3% in 2025?",
        "clobTokenIds": ["tok_yes_1", "tok_no_1"],
        "outcomePrices": ["0.62", "0.38"],
        "volume": "500000.00",
        "liquidity": "25000.00",
        "active": True,
    },
    {
        "conditionId": "0xbbb999",
        "questionId": "0xccc111",
        "question": "Second market?",
        "clobTokenIds": ["tok_yes_2", "tok_no_2"],
        "outcomePrices": ["0.40", "0.60"],
        "volume": "100000.00",
        "liquidity": "5000.00",
        "active": True,
    }
]

@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()

async def test_fetch_and_upsert_markets(session):
    from collectors.gamma_collector import GammaCollector
    collector = GammaCollector(session)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = MOCK_MARKETS_RESPONSE
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        markets = await collector.fetch_and_upsert_markets()

    assert len(markets) == 2
    assert markets[0].condition_id == "0xabc123"
    assert markets[0].yes_token_id == "tok_yes_1"
    assert markets[0].volume_usd == 500000.0
    assert markets[1].condition_id == "0xbbb999"

async def test_upsert_updates_existing_market(session):
    from collectors.gamma_collector import GammaCollector

    # Pre-insert a market
    existing = Market(
        condition_id="0xabc123",
        question_id="0xdef456",
        question="Will inflation drop below 3% in 2025?",
        yes_token_id="tok_yes_1",
        no_token_id="tok_no_1",
        volume_usd=100.0,
        liquidity_usd=50.0,
    )
    session.add(existing)
    await session.commit()

    collector = GammaCollector(session)

    updated_response = [MOCK_MARKETS_RESPONSE[0].copy()]
    updated_response[0]["volume"] = "999999.00"

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = updated_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        markets = await collector.fetch_and_upsert_markets()

    # Should still be 1 market (upsert, not duplicate)
    from sqlalchemy import select, func
    count = (await session.execute(select(func.count(Market.id)))).scalar()
    assert count == 1
    assert markets[0].volume_usd == 999999.0

async def test_skips_market_without_condition_id(session):
    from collectors.gamma_collector import GammaCollector
    collector = GammaCollector(session)

    bad_response = [{"conditionId": "", "question": "bad market"}]
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = bad_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=bad_response)
        mock_client_class.return_value = mock_client

        mock_response2 = MagicMock()
        mock_response2.status_code = 200
        mock_response2.json.return_value = bad_response
        mock_response2.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response2)

        markets = await collector.fetch_and_upsert_markets()

    assert len(markets) == 0

async def test_gamma_collector_implements_protocol(session):
    from collectors.gamma_collector import GammaCollector
    from collectors.base import MarketCollector
    collector = GammaCollector(session)
    assert isinstance(collector, MarketCollector)


# --- CLOB Collector Tests ---

async def test_clob_snapshot_orderbook(session):
    from collectors.clob_collector import CLOBCollector

    # Pre-insert a market
    market = Market(
        condition_id="0xclob1", question_id="0xq_clob", question="CLOB test?",
        yes_token_id="tok_yes_clob", no_token_id="tok_no_clob"
    )
    session.add(market)
    await session.commit()

    mock_ob_response = {
        "bids": [{"price": "0.61", "size": "500"}, {"price": "0.60", "size": "200"}],
        "asks": [{"price": "0.63", "size": "300"}],
        "spread": "0.02",
        "midpoint": "0.62"
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_ob_response

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        collector = CLOBCollector(session)
        snapshots = await collector.snapshot_orderbooks([market])

    assert len(snapshots) == 1
    assert snapshots[0].midpoint == 0.62
    assert snapshots[0].spread == 0.02
    assert snapshots[0].best_bid == 0.61
    assert snapshots[0].best_ask == 0.63
    assert snapshots[0].token_id == "tok_yes_clob"
    assert len(snapshots[0].bids) == 2

async def test_clob_skips_market_without_token(session):
    from collectors.clob_collector import CLOBCollector

    market = Market(
        condition_id="0xnotok", question_id="0xq_nt", question="No token?",
        yes_token_id="", no_token_id=""
    )
    session.add(market)
    await session.commit()

    collector = CLOBCollector(session)
    snapshots = await collector.snapshot_orderbooks([market])
    assert len(snapshots) == 0

async def test_clob_handles_non_200_gracefully(session):
    from collectors.clob_collector import CLOBCollector

    market = Market(
        condition_id="0xerr1", question_id="0xq_err", question="Error test?",
        yes_token_id="tok_err", no_token_id="tok_err_no"
    )
    session.add(market)
    await session.commit()

    mock_response = MagicMock()
    mock_response.status_code = 429  # rate limited

    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        collector = CLOBCollector(session)
        snapshots = await collector.snapshot_orderbooks([market])

    assert len(snapshots) == 0  # gracefully skipped, no crash
