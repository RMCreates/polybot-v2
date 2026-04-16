import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from db.models import Base

@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as s:
        yield s
    await engine.dispose()

async def test_get_system_status_empty_db(session):
    from dashboard.api import get_system_status
    status = await get_system_status(session)
    assert status["markets_tracked"] == 0
    assert status["open_positions"] == 0
    assert status["total_pnl_usd"] == 0.0
    assert "avg_edge_today" in status

async def test_get_trades_empty_db(session):
    from dashboard.api import get_trades_with_signals
    trades = await get_trades_with_signals(session)
    assert trades == []

async def test_dashboard_app_imports():
    from dashboard.app import app
    assert app.title == "Polymarket Bot Dashboard"
