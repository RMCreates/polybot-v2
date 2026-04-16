import asyncio
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Market, OrderBookSnapshot
from config.settings import settings


async def _get_with_retry(client: httpx.AsyncClient, url: str, params=None, max_retries: int = 3):
    """Retry GET with exponential backoff: 1s, 2s, 4s. Handles 429 rate limits."""
    for attempt in range(max_retries):
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                wait = 2 ** attempt
                await asyncio.sleep(wait)
                continue
            return resp
        except Exception:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(2 ** attempt)
    return None


class CLOBCollector:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.base_url = settings.clob_api_url

    async def snapshot_orderbooks(self, markets: list[Market]) -> list[OrderBookSnapshot]:
        snapshots = []
        async with httpx.AsyncClient(timeout=30) as client:
            for market in markets:
                if not market.yes_token_id:
                    continue
                try:
                    resp = await _get_with_retry(
                        client,
                        f"{self.base_url}/book",
                        params={"token_id": market.yes_token_id}
                    )
                    if resp is None or resp.status_code != 200:
                        continue
                    data = resp.json()
                    bids = data.get("bids") or []
                    asks = data.get("asks") or []
                    best_bid = float(bids[0]["price"]) if bids else 0.0
                    best_ask = float(asks[0]["price"]) if asks else 1.0
                    spread = float(data.get("spread", best_ask - best_bid))
                    midpoint = float(data.get("midpoint", (best_bid + best_ask) / 2))
                    snap = OrderBookSnapshot(
                        market_id=market.id,
                        token_id=market.yes_token_id,
                        bids=bids,
                        asks=asks,
                        best_bid=best_bid,
                        best_ask=best_ask,
                        spread=spread,
                        midpoint=midpoint,
                    )
                    self.session.add(snap)
                    snapshots.append(snap)
                except Exception:
                    continue
        await self.session.commit()
        return snapshots
