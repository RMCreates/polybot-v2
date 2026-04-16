from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Trade
from risk.risk_engine import TradeProposal, RiskDecision
from datetime import datetime, timezone
from observability.logger import get_logger

log = get_logger(__name__)

class PaperExecutor:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def execute(
        self,
        proposal: TradeProposal,
        risk_decision: RiskDecision,
        signal_id: int,
        reasoning_trace: dict,
    ) -> Trade:
        trade = Trade(
            signal_id=signal_id,
            market_id=reasoning_trace.get("market_id"),
            is_paper=True,
            side=proposal.side,
            token_id=proposal.token_id,
            size_usd=risk_decision.final_size_usd,
            entry_price=proposal.market_midpoint,
            status="OPEN",
            reasoning_trace=reasoning_trace,
        )
        self.session.add(trade)
        await self.session.commit()
        log.info(
            "paper_trade_placed",
            extra={
                "trade_id": trade.id,
                "side": proposal.side,
                "size_usd": risk_decision.final_size_usd,
                "entry_price": proposal.market_midpoint,
            }
        )
        return trade

    async def close_position(self, trade_id: int, exit_price: float) -> Trade:
        trade = await self.session.get(Trade, trade_id)
        if not trade:
            raise ValueError(f"Trade {trade_id} not found")
        direction = 1 if trade.side == "BUY_YES" else -1
        trade.exit_price = exit_price
        trade.pnl_usd = round(
            (exit_price - trade.entry_price) * trade.size_usd * direction, 4
        )
        trade.status = "CLOSED"
        trade.closed_at = datetime.now(timezone.utc)
        await self.session.commit()
        return trade
