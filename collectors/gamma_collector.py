import asyncio
import json as _json
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Market, PriceSnapshot
from config.settings import settings
from datetime import datetime, timezone
from typing import Optional


def _parse_date(raw) -> Optional[datetime]:
    """Parse Gamma API endDate string into a naive UTC datetime."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _parse_token_ids(raw) -> list:
    """Gamma API returns clobTokenIds as a JSON string — parse it."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []

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

class GammaCollector:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.base_url = settings.gamma_api_url

    async def fetch_and_upsert_markets(self) -> list[Market]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await _get_with_retry(
                client,
                f"{self.base_url}/markets",
                params={"closed": "false", "limit": 200}
            )
            if resp is None or resp.status_code != 200:
                return []
            raw = resp.json()

        markets = []
        for item in raw:
            cid = item.get("conditionId", "")
            if not cid:
                continue
            existing = (await self.session.execute(
                select(Market).where(Market.condition_id == cid)
            )).scalar_one_or_none()

            token_ids = _parse_token_ids(item.get("clobTokenIds"))
            if existing:
                existing.volume_usd = float(item.get("volume", 0) or 0)
                existing.liquidity_usd = float(item.get("liquidity", 0) or 0)
                existing.updated_at = datetime.now(timezone.utc)
                # Patch corrupted token IDs from old bug
                if existing.yes_token_id in ("", "[") and len(token_ids) >= 2:
                    existing.yes_token_id = token_ids[0]
                    existing.no_token_id = token_ids[1]
                # Populate closes_at if not yet set
                if existing.closes_at is None:
                    existing.closes_at = _parse_date(item.get("endDate"))
                markets.append(existing)
            else:
                market = Market(
                    condition_id=cid,
                    question_id=item.get("questionId", ""),
                    question=item.get("question", ""),
                    yes_token_id=token_ids[0] if len(token_ids) > 0 else "",
                    no_token_id=token_ids[1] if len(token_ids) > 1 else "",
                    volume_usd=float(item.get("volume", 0) or 0),
                    liquidity_usd=float(item.get("liquidity", 0) or 0),
                    is_active=item.get("active", True),
                    closes_at=_parse_date(item.get("endDate")),
                )
                self.session.add(market)
                markets.append(market)

        await self.session.commit()
        return markets

    async def snapshot_prices(self, markets: list[Market]) -> list[PriceSnapshot]:
        snapshots = []
        async with httpx.AsyncClient(timeout=30) as client:
            for market in markets:
                try:
                    resp = await _get_with_retry(client, f"{self.base_url}/markets/{market.condition_id}")
                    if resp is None or resp.status_code != 200:
                        continue
                    data = resp.json()
                    prices = data.get("outcomePrices") or ["0.5", "0.5"]
                    if len(prices) < 2:
                        continue
                    snap = PriceSnapshot(
                        market_id=market.id,
                        yes_price=float(prices[0]),
                        no_price=float(prices[1]),
                    )
                    self.session.add(snap)
                    snapshots.append(snap)
                except Exception:
                    continue
        await self.session.commit()
        return snapshots
