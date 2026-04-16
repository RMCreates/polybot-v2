from __future__ import annotations
import httpx
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Trade, Market
from config.settings import settings
from observability.logger import get_logger

log = get_logger(__name__)


class DataCollector:
    """Checks Gamma API for resolved markets and closes matching open paper trades."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.last_resolved: list[Trade] = []  # populated after each call, used by scheduler for learning hooks

    async def check_and_resolve_trades(self) -> None:
        """For each OPEN paper trade, check if its market has resolved on Polymarket."""
        self.last_resolved = []

        open_trades = list((await self.session.execute(
            select(Trade).where(Trade.status == "OPEN", Trade.is_paper == True)
        )).scalars())

        if not open_trades:
            return

        async with httpx.AsyncClient(timeout=10) as client:
            for trade in open_trades:
                market = await self.session.get(Market, trade.market_id)
                if not market:
                    continue
                try:
                    resp = await client.get(
                        f"{settings.gamma_api_url}/markets/{market.condition_id}"
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()

                    # Market is still active — nothing to resolve
                    if data.get("active", True):
                        continue

                    prices = data.get("outcomePrices") or []
                    if len(prices) < 2:
                        continue

                    yes_final = float(prices[0])
                    # Exit price from the perspective of the trade direction
                    if "YES" in trade.side:
                        exit_price = yes_final
                        direction = 1
                    else:
                        exit_price = 1.0 - yes_final
                        direction = -1

                    trade.exit_price = round(exit_price, 4)
                    trade.pnl_usd = round(
                        (exit_price - trade.entry_price) * trade.size_usd * direction, 4
                    )
                    trade.status = "RESOLVED"
                    trade.closed_at = datetime.now(timezone.utc)

                    self.last_resolved.append(trade)
                    log.info("trade_resolved", extra={
                        "trade_id": trade.id,
                        "market": market.question[:60],
                        "pnl": trade.pnl_usd,
                        "exit_price": trade.exit_price,
                    })
                except Exception as e:
                    log.warning("resolve_check_failed", extra={
                        "trade_id": trade.id,
                        "error": str(e),
                    })

        if self.last_resolved:
            await self.session.commit()
            log.info("resolution_cycle_complete", extra={"resolved_count": len(self.last_resolved)})
