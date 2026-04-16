import asyncio
import uvicorn
from db.engine import init_db
from scheduler.runner import start_scheduler, run_full_cycle
from config.settings import settings
from observability.logger import get_logger
from dashboard.app import app as dashboard_app

log = get_logger("main")

async def main():
    if settings.live_trading:
        log.info("LIVE_TRADING_ENABLED — real orders will be placed")
    else:
        log.info("PAPER_TRADING_MODE — no real orders will be placed")

    await init_db()
    log.info("database_initialized")

    scheduler = start_scheduler()
    log.info("scheduler_started", extra={"poll_interval": settings.market_poll_interval})

    # Start dashboard immediately — first cycle runs in background
    config = uvicorn.Config(dashboard_app, host="127.0.0.1", port=8080, log_level="warning")
    server = uvicorn.Server(config)
    log.info("dashboard_started", extra={"url": "http://localhost:8080"})

    # Kick off first cycle without blocking the dashboard
    asyncio.create_task(run_full_cycle())

    try:
        await server.serve()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("scheduler_stopped")

if __name__ == "__main__":
    asyncio.run(main())
