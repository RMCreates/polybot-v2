from dataclasses import dataclass, field
from config.settings import settings
from observability.logger import get_logger

log = get_logger(__name__)

BOOTSTRAP_THRESHOLD = 30  # resolved trades needed before normal rules apply

@dataclass
class TradeProposal:
    market_condition_id: str
    side: str                    # "BUY_YES" | "BUY_NO"
    token_id: str
    edge: float
    fair_probability: float
    market_midpoint: float
    signal_confidence: float
    proposed_size_usd: float
    spread: float = 0.0                  # NEW — orderbook spread
    market_liquidity_usd: float = 0.0    # NEW — market liquidity
    market_category: str = ""            # NEW — sports/politics/crypto/etc

@dataclass
class RiskDecision:
    approved: bool
    final_size_usd: float
    rejection_reasons: list[str] = field(default_factory=list)
    applied_rules: list[str] = field(default_factory=list)

class DefaultRiskEngine:
    def evaluate(
        self,
        proposal: TradeProposal,
        current_exposure_usd: float,
        open_positions: list[dict],
        resolved_trade_count: int = 0,
        category_open_count: int = 0,      # NEW
    ) -> RiskDecision:
        rejections = []
        applied = []

        bootstrap = resolved_trade_count < BOOTSTRAP_THRESHOLD
        if bootstrap:
            log.info("bootstrap_mode_active", extra={
                "resolved_trades": resolved_trade_count,
                "threshold": BOOTSTRAP_THRESHOLD,
            })

        # R1: minimum edge threshold (relaxed in bootstrap)
        min_edge = 0.005 if bootstrap else settings.min_edge_threshold
        applied.append(f"R1_edge({'bootstrap' if bootstrap else 'normal'})")
        if proposal.edge < min_edge:
            rejections.append(
                f"Edge {proposal.edge:.4f} below minimum {min_edge}"
            )

        # R2: spread check (wide spread = bad fills)
        applied.append("R2_spread")
        max_spread = 0.10 if bootstrap else settings.max_spread_pct
        if proposal.spread > max_spread:
            rejections.append(
                f"Spread {proposal.spread:.3f} exceeds max {max_spread}"
            )

        # R3: liquidity floor (illiquid = unreliable prices)
        applied.append("R3_liquidity")
        min_liq = 200.0 if bootstrap else settings.min_liquidity_usd
        if proposal.market_liquidity_usd > 0 and proposal.market_liquidity_usd < min_liq:
            rejections.append(
                f"Liquidity ${proposal.market_liquidity_usd:.0f} below minimum ${min_liq:.0f}"
            )

        # R5: total exposure cap (always enforced)
        applied.append("R5_total_exposure")
        if current_exposure_usd + proposal.proposed_size_usd > settings.max_total_exposure_usd:
            rejections.append(
                f"Total exposure would exceed cap: "
                f"${current_exposure_usd:.2f} + ${proposal.proposed_size_usd:.2f} "
                f"> ${settings.max_total_exposure_usd:.2f}"
            )

        # R6: signal confidence minimum (skipped in bootstrap)
        if not bootstrap:
            applied.append("R6_confidence")
            if proposal.signal_confidence < settings.min_signal_confidence:
                rejections.append(
                    f"Signal confidence {proposal.signal_confidence:.2f} "
                    f"below minimum {settings.min_signal_confidence}"
                )
        else:
            applied.append("R6_confidence_skipped_bootstrap")

        # R7: correlated position limit (avoid concentration in one category)
        max_correlated = 8 if bootstrap else 5
        applied.append("R7_correlation")
        if proposal.market_category and category_open_count >= max_correlated:
            rejections.append(
                f"{proposal.market_category} has {category_open_count} open positions "
                f"(max {max_correlated})"
            )

        # R4: tiered position size cap (bootstrap gets $15 standard cap)
        applied.append("R4_size_cap")
        is_favorable = (
            proposal.edge >= settings.favorable_edge_threshold
            and proposal.signal_confidence >= settings.favorable_confidence_threshold
        )
        if bootstrap:
            size_cap = 15.0 if not is_favorable else settings.max_favorable_position_usd
        else:
            size_cap = (
                settings.max_favorable_position_usd if is_favorable
                else settings.max_single_position_usd
            )
        final_size = min(proposal.proposed_size_usd, size_cap)
        applied.append(f"R4_tier={'favorable' if is_favorable else ('bootstrap_standard' if bootstrap else 'standard')}")

        approved = len(rejections) == 0
        return RiskDecision(
            approved=approved,
            final_size_usd=final_size if approved else 0.0,
            rejection_reasons=rejections,
            applied_rules=applied,
        )
