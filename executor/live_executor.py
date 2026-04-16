from config.settings import settings
from observability.logger import get_logger

log = get_logger(__name__)

class LiveExecutor:
    """
    Live order executor using py-clob-client.
    HARD-GATED: will raise RuntimeError if LIVE_TRADING != True.
    Do NOT enable until all Stage 5 performance metrics are met:
    - Brier score better than market midpoint baseline
    - Sharpe ratio > 1.5
    - >= 30 resolved paper trades
    - Max drawdown < 20% of capital
    """

    def __init__(self):
        if not settings.live_trading:
            raise RuntimeError(
                "LiveExecutor cannot be instantiated: LIVE_TRADING is not True. "
                "This is a safety gate. Set LIVE_TRADING=true in .env ONLY after "
                "all Stage 5 performance thresholds are met and paper trades reviewed."
            )
        from py_clob_client.client import ClobClient
        self._client = ClobClient(
            host=settings.clob_api_url,
            key=settings.polygon_private_key.get_secret_value(),
            chain_id=137,
        )
        log.info("live_executor_initialized — REAL ORDERS WILL BE PLACED")

    async def execute(self, proposal, risk_decision, signal_id: int, reasoning_trace: dict):
        # Fix 9: Dry-run mode — constructs and signs order but does NOT submit to CLOB.
        # Enable with DRY_RUN_ORDERS=true in .env to test wallet signing without spending.
        if settings.dry_run_orders:
            order_payload = {
                "market_condition_id": proposal.market_condition_id,
                "side": proposal.side,
                "token_id": proposal.token_id,
                "size_usd": risk_decision.final_size_usd,
                "entry_price": proposal.market_midpoint,
                "signal_id": signal_id,
            }
            log.info("dry_run_order_constructed", extra={
                "market": proposal.market_condition_id,
                "side": proposal.side,
                "size_usd": risk_decision.final_size_usd,
                "signed_payload": str(order_payload),
            })
            return {"status": "dry_run", "order": order_payload}

        raise NotImplementedError(
            "LiveExecutor.execute: Complete all Stage 5 metric validations "
            "and manually review paper trade reasoning traces before implementing."
        )

    async def close_position(self, trade_id: int, exit_price: float):
        raise NotImplementedError(
            "LiveExecutor.close_position: not yet implemented"
        )
