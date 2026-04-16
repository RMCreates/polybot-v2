from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from db.engine import AsyncSessionLocal
from dashboard.api import (
    get_trades_with_signals,
    get_system_status,
    get_performance_data,
    close_paper_trade,
    check_source_health,
    get_learning_summary,
    get_session_stats,
    generate_daily_recommendations,
)

app = FastAPI(title="Polymarket Bot Dashboard", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    async with AsyncSessionLocal() as session:
        trades = await get_trades_with_signals(session)
    return templates.TemplateResponse("trades.html", {"request": request, "trades": trades})


@app.get("/trades", response_class=HTMLResponse)
async def trades_page(request: Request):
    async with AsyncSessionLocal() as session:
        trades = await get_trades_with_signals(session)
    return templates.TemplateResponse("trades.html", {"request": request, "trades": trades})


@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    async with AsyncSessionLocal() as session:
        status = await get_system_status(session)
    from scheduler.runner import get_last_cycle, is_paused
    return templates.TemplateResponse("system.html", {
        "request": request,
        "status": status,
        "last_cycle": get_last_cycle(),
        "bot_paused": is_paused(),
    })


@app.get("/learning", response_class=HTMLResponse)
async def learning_page(request: Request):
    async with AsyncSessionLocal() as session:
        summary = await get_learning_summary(session)
    return templates.TemplateResponse("learning.html", {"request": request, "summary": summary})


@app.get("/daily", response_class=HTMLResponse)
async def daily_page(request: Request):
    """Today's Recommendations — Polymarket vs Kalshi edge comparison for manual trading."""
    from scheduler.runner import get_last_cycle
    last = get_last_cycle()
    recs = last.get("daily_recommendations", [])
    # Fallback: if the scheduler hasn't cached recs yet, generate on demand
    if not recs:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as sa_select
            from db.models import Market as MarketModel
            all_active = list((await session.execute(
                sa_select(MarketModel).where(MarketModel.is_active == True)
            )).scalars())
            recs = await generate_daily_recommendations(session, all_active)
    return templates.TemplateResponse(
        "daily.html",
        {"request": request, "recommendations": recs},
    )


@app.get("/api/daily-recommendations")
async def api_daily_recommendations():
    from scheduler.runner import get_last_cycle
    last = get_last_cycle()
    recs = last.get("daily_recommendations", [])
    if not recs:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select as sa_select
            from db.models import Market as MarketModel
            all_active = list((await session.execute(
                sa_select(MarketModel).where(MarketModel.is_active == True)
            )).scalars())
            recs = await generate_daily_recommendations(session, all_active)
    return {"recommendations": recs}


# ── JSON API endpoints ────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    async with AsyncSessionLocal() as session:
        return await get_system_status(session)


@app.get("/api/trades")
async def api_trades():
    async with AsyncSessionLocal() as session:
        return await get_trades_with_signals(session, limit=50)


@app.get("/api/performance")
async def api_performance():
    async with AsyncSessionLocal() as session:
        return await get_performance_data(session)


@app.post("/api/trades/{trade_id}/close")
async def api_close_trade(trade_id: int):
    async with AsyncSessionLocal() as session:
        return await close_paper_trade(session, trade_id)


@app.get("/api/sources/health")
async def api_sources_health():
    return await check_source_health()


@app.post("/api/bot/pause")
async def api_pause_bot():
    from scheduler.runner import set_paused
    set_paused(True)
    return {"status": "paused"}


@app.post("/api/bot/resume")
async def api_resume_bot():
    from scheduler.runner import set_paused
    set_paused(False)
    return {"status": "running"}


@app.get("/api/bot/status")
async def api_bot_status():
    from scheduler.runner import is_paused, get_last_cycle
    return {"paused": is_paused(), "last_cycle": get_last_cycle()}


@app.get("/api/learning")
async def api_learning():
    async with AsyncSessionLocal() as session:
        return await get_learning_summary(session)


@app.get("/api/sessions")
async def api_sessions():
    async with AsyncSessionLocal() as session:
        return await get_session_stats(session)


# /paper-trades is the canonical URL for the paper trading view.
# /trades will become live trades when live_trading=True.
@app.get("/paper-trades", response_class=HTMLResponse)
async def paper_trades_page(request: Request):
    async with AsyncSessionLocal() as session:
        trades = await get_trades_with_signals(session)
    return templates.TemplateResponse("trades.html", {"request": request, "trades": trades})
