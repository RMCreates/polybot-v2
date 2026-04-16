from typing import Protocol, runtime_checkable
from db.models import Market, PriceSnapshot

@runtime_checkable
class MarketCollector(Protocol):
    async def fetch_and_upsert_markets(self) -> list[Market]: ...
    async def snapshot_prices(self, markets: list[Market]) -> list[PriceSnapshot]: ...
