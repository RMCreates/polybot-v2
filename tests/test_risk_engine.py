import pytest
from risk.risk_engine import DefaultRiskEngine, TradeProposal, RiskDecision

def make_proposal(**overrides):
    defaults = dict(
        market_condition_id="0xtest",
        side="BUY_YES",
        token_id="tok_yes",
        edge=0.05,
        fair_probability=0.65,
        market_midpoint=0.60,
        signal_confidence=0.5,
        proposed_size_usd=8.0,
    )
    defaults.update(overrides)
    return TradeProposal(**defaults)

def test_approves_valid_trade():
    engine = DefaultRiskEngine()
    decision = engine.evaluate(make_proposal(), current_exposure_usd=0.0, open_positions=[])
    assert decision.approved is True
    assert decision.final_size_usd > 0
    assert len(decision.rejection_reasons) == 0

def test_rejects_low_edge():
    engine = DefaultRiskEngine()
    decision = engine.evaluate(make_proposal(edge=0.01), current_exposure_usd=0.0, open_positions=[])
    assert decision.approved is False
    assert any("Edge" in r for r in decision.rejection_reasons)
    assert decision.final_size_usd == 0.0

def test_rejects_when_exposure_exceeded():
    engine = DefaultRiskEngine()
    decision = engine.evaluate(make_proposal(proposed_size_usd=30.0), current_exposure_usd=90.0, open_positions=[])
    assert decision.approved is False
    assert any("exposure" in r.lower() for r in decision.rejection_reasons)

def test_rejects_low_confidence():
    engine = DefaultRiskEngine()
    decision = engine.evaluate(make_proposal(signal_confidence=0.05), current_exposure_usd=0.0, open_positions=[])
    assert decision.approved is False
    assert any("confidence" in r.lower() for r in decision.rejection_reasons)

def test_size_reduced_to_standard_cap():
    engine = DefaultRiskEngine()
    # Proposed size $25, not high-conviction → capped at $10
    decision = engine.evaluate(make_proposal(proposed_size_usd=25.0), current_exposure_usd=0.0, open_positions=[])
    assert decision.approved is True
    assert decision.final_size_usd == 10.0

def test_favorable_trade_gets_higher_cap():
    engine = DefaultRiskEngine()
    # edge=0.09 (>0.08) AND confidence=0.75 (>0.70) → cap = $20
    decision = engine.evaluate(
        make_proposal(edge=0.09, signal_confidence=0.75, proposed_size_usd=18.0),
        current_exposure_usd=0.0,
        open_positions=[]
    )
    assert decision.approved is True
    assert decision.final_size_usd == 18.0  # within $20 cap

def test_reasoning_trace_contains_all_rules():
    engine = DefaultRiskEngine()
    decision = engine.evaluate(make_proposal(), current_exposure_usd=0.0, open_positions=[])
    # All 4 applied rules must be present
    assert "R1_edge" in decision.applied_rules
    assert "R5_total_exposure" in decision.applied_rules
    assert "R6_confidence" in decision.applied_rules
    assert "R4_size_cap" in decision.applied_rules
