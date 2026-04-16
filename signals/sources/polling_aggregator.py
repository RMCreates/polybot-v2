from __future__ import annotations
import httpx
from typing import List, Optional
from observability.logger import get_logger

log = get_logger(__name__)

FTE_PRESIDENTIAL_URL = (
    "https://projects.fivethirtyeight.com/polls/data/presidential_general_averages.csv"
)

async def fetch_fte_polling_average(keywords: List[str]) -> Optional[float]:
    """
    Fetches FiveThirtyEight presidential polling CSV and searches for a row
    matching any of the given keywords in the candidate/race column.
    Returns percentage as probability (0–1) or None if no match.
    """
    if not keywords:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(FTE_PRESIDENTIAL_URL)
            if resp.status_code != 200:
                return None
        lines = resp.text.splitlines()
        if len(lines) < 2:
            return None
        # CSV: date, state, candidate, pct_estimate, ...
        for line in lines[1:]:
            parts = line.split(",")
            if len(parts) < 3:
                continue
            row_text = line.lower()
            if any(kw.lower() in row_text for kw in keywords):
                try:
                    return float(parts[2].strip()) / 100.0
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        log.warning("fte_polling_failed", extra={"error": str(e)})
    return None
