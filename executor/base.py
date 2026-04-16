from typing import Protocol, runtime_checkable

@runtime_checkable
class TradeExecutor(Protocol):
    async def execute(
        self,
        proposal: "TradeProposal",
        risk_decision: "RiskDecision",
        signal_id: int,
        reasoning_trace: dict,
    ): ...

    async def close_position(self, trade_id: int, exit_price: float): ...
