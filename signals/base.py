from typing import Protocol, runtime_checkable
from dataclasses import dataclass, field

@dataclass
class SignalResult:
    market_condition_id: str
    fair_probability: float      # 0–1 estimated probability of YES resolution
    confidence: float            # 0–1 how reliable the signal is
    provider_name: str
    metadata: dict = field(default_factory=dict)

@runtime_checkable
class SignalProvider(Protocol):
    name: str

    async def compute_signal(
        self,
        market,
        orderbook_snapshot,
        price_history: list,
    ) -> SignalResult: ...

    def is_applicable(self, market) -> bool: ...
