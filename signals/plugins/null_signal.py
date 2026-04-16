from signals.base import SignalProvider, SignalResult
from signals.registry import register

class NullSignal:
    """Baseline signal: fair_probability = market midpoint, confidence = 0.
    Used to prove the pipeline works before any real signal exists.
    Edge will always be 0 with this plugin.
    """
    name = "null_signal"

    async def compute_signal(self, market, orderbook_snapshot, price_history) -> SignalResult:
        mid = orderbook_snapshot.midpoint if orderbook_snapshot is not None else 0.5
        return SignalResult(
            market_condition_id=market.condition_id,
            fair_probability=mid,
            confidence=0.0,
            provider_name=self.name,
        )

    def is_applicable(self, market) -> bool:
        return True

register(NullSignal())
