"""
Tests for the rules engine — pre-filter and post-validator.

These tests are critical: they verify that hard rules cannot be bypassed
by LLM hallucinations. Run them before every code change to rules_engine.

    uv run pytest tests/test_rules_engine.py -v
"""

from __future__ import annotations

import pytest

from src.rules_engine import (
    Phase,
    Position,
    PortfolioState,
    compute_allowed_actions,
    compute_default_stop_pct,
    validate_verdict,
)


# Minimal rules dict matching config/rules.yaml structure
@pytest.fixture
def rules():
    return {
        "position_sizing": {
            "max_allocation_per_ticker_pct": 10.0,
            "max_open_positions": 12,
        },
        "sector_limits": {
            "max_allocation_per_sector_pct": 25.0,
        },
        "stop_loss": {
            "atr_multiplier_normal": 1.5,
            "atr_multiplier_volatile": 2.0,
            "hard_max_pct": 12.0,
            "hard_min_pct": 4.0,
        },
        "blackouts": {
            "enabled": True,
            "blackout_actions": ["BUY_NEW", "BUY", "ADD"],
            "restricted_tickers": ["GME"],
        },
    }


@pytest.fixture
def empty_portfolio():
    return PortfolioState(
        total_open_positions=3,
        daily_pnl_pct=0.0,
        drawdown_from_peak_pct=0.0,
        defensive_mode=False,
        paused=False,
        sector_allocations={"Technology": 15.0},
    )


# =============================================================================
# Pre-filter: phase basics
# =============================================================================

class TestPhaseBaselineActions:
    def test_initial_phase_actions(self, rules, empty_portfolio):
        position = Position(ticker="NVDA", shares=0, allocation_pct=0)
        result = compute_allowed_actions(Phase.INITIAL, position, empty_portfolio, rules)
        # No position → BUY_NEW + AVOID_NEW + ABSTAIN
        assert "BUY_NEW" in result.allowed_actions
        assert "AVOID_NEW" in result.allowed_actions
        assert "TRIM" not in result.allowed_actions
        assert "SELL" not in result.allowed_actions

    def test_intraday_phase_with_position(self, rules, empty_portfolio):
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = compute_allowed_actions(Phase.INTRADAY, position, empty_portfolio, rules)
        assert "HOLD" in result.allowed_actions
        assert "SELL" in result.allowed_actions
        assert "TRIM" in result.allowed_actions
        assert "BUY" in result.allowed_actions

    def test_eod_phase_full_options(self, rules, empty_portfolio):
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = compute_allowed_actions(Phase.EOD, position, empty_portfolio, rules)
        assert "HOLD" in result.allowed_actions
        assert "ADD" in result.allowed_actions
        assert "TRIM" in result.allowed_actions


# =============================================================================
# Pre-filter: max allocation
# =============================================================================

class TestMaxAllocation:
    def test_at_max_blocks_buy(self, rules, empty_portfolio):
        position = Position(ticker="NVDA", shares=100, allocation_pct=10.0)
        result = compute_allowed_actions(Phase.EOD, position, empty_portfolio, rules)
        assert "ADD" not in result.allowed_actions
        assert "BUY_NEW" not in result.allowed_actions
        assert "HOLD" in result.allowed_actions
        assert "TRIM" in result.allowed_actions  # can still reduce
        assert any("max_allocation_reached" in r for r in result.restrictions_applied)

    def test_under_max_allows_buy(self, rules, empty_portfolio):
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = compute_allowed_actions(Phase.EOD, position, empty_portfolio, rules)
        assert "ADD" in result.allowed_actions

    def test_just_under_threshold_blocks_buy(self, rules, empty_portfolio):
        # Headroom = 0.3% which is below the 0.5% safety margin
        position = Position(ticker="NVDA", shares=100, allocation_pct=9.7)
        result = compute_allowed_actions(Phase.EOD, position, empty_portfolio, rules)
        assert "ADD" not in result.allowed_actions


# =============================================================================
# Pre-filter: position-based restrictions
# =============================================================================

class TestPositionBased:
    def test_no_position_cannot_sell(self, rules, empty_portfolio):
        position = Position(ticker="TSLA", shares=0, allocation_pct=0)
        result = compute_allowed_actions(Phase.EOD, position, empty_portfolio, rules)
        assert "SELL" not in result.allowed_actions
        assert "TRIM" not in result.allowed_actions

    def test_with_position_can_sell(self, rules, empty_portfolio):
        position = Position(ticker="TSLA", shares=50, allocation_pct=5.0)
        result = compute_allowed_actions(Phase.EOD, position, empty_portfolio, rules)
        assert "SELL" in result.allowed_actions
        assert "TRIM" in result.allowed_actions


# =============================================================================
# Pre-filter: blackouts and restricted tickers
# =============================================================================

class TestBlackouts:
    def test_restricted_ticker_blocks_entry(self, rules, empty_portfolio):
        position = Position(ticker="GME", shares=0, allocation_pct=0)
        result = compute_allowed_actions(
            Phase.INITIAL, position, empty_portfolio, rules
        )
        assert "BUY_NEW" not in result.allowed_actions
        assert any("restricted" in r for r in result.restrictions_applied)

    def test_earnings_blackout_blocks_entry(self, rules, empty_portfolio):
        position = Position(ticker="AAPL", shares=100, allocation_pct=8.0)
        result = compute_allowed_actions(
            Phase.EOD, position, empty_portfolio, rules, blackout_active=True
        )
        assert "ADD" not in result.allowed_actions
        assert "BUY_NEW" not in result.allowed_actions
        # Risk reduction still allowed during blackout
        assert "TRIM" in result.allowed_actions
        assert "SELL" in result.allowed_actions


# =============================================================================
# Pre-filter: circuit breakers
# =============================================================================

class TestCircuitBreakers:
    def test_paused_portfolio_skips_debate(self, rules):
        portfolio = PortfolioState(paused=True)
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = compute_allowed_actions(Phase.EOD, position, portfolio, rules)
        assert result.skip_debate is True
        assert result.allowed_actions == {"HOLD", "ABSTAIN"}

    def test_defensive_mode_blocks_new_entries(self, rules):
        portfolio = PortfolioState(
            total_open_positions=5, defensive_mode=True
        )
        position = Position(ticker="NVDA", shares=100, allocation_pct=5.0)
        result = compute_allowed_actions(Phase.EOD, position, portfolio, rules)
        assert "ADD" not in result.allowed_actions
        assert "BUY_NEW" not in result.allowed_actions
        assert "TRIM" in result.allowed_actions


# =============================================================================
# Post-validator: catch illegal verdicts
# =============================================================================

class TestPostValidator:
    def test_legal_verdict_passes(self, rules):
        verdict = {"verdict": "HOLD", "suggested_sizing": "0%"}
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = validate_verdict(
            verdict, allowed_actions={"HOLD", "TRIM"}, position=position, rules=rules
        )
        assert result.is_valid is True
        assert result.was_downgraded is False
        assert result.final_verdict["verdict"] == "HOLD"

    def test_illegal_verdict_downgraded(self, rules):
        verdict = {"verdict": "BUY_NEW", "suggested_sizing": "+5%"}
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = validate_verdict(
            verdict, allowed_actions={"HOLD", "TRIM"}, position=position, rules=rules
        )
        assert result.is_valid is False
        assert result.was_downgraded is True
        assert result.final_verdict["verdict"] == "HOLD"
        assert any(v["type"] == "verdict_not_in_allowed_actions" for v in result.violations)

    def test_sizing_exceeds_max_downgraded(self, rules):
        verdict = {"verdict": "ADD", "suggested_sizing": "+5%"}
        # Position at 8%, +5% would be 13%, max is 10%
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = validate_verdict(
            verdict, allowed_actions={"HOLD", "ADD"}, position=position, rules=rules
        )
        assert result.is_valid is False
        assert result.was_downgraded is True
        assert any(v["type"] == "sizing_exceeds_max_allocation" for v in result.violations)

    def test_stop_loss_above_hard_max_capped(self, rules):
        verdict = {
            "verdict": "HOLD",
            "suggested_sizing": "0%",
            "final_stop_loss_pct": 20.0,
        }
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = validate_verdict(
            verdict, allowed_actions={"HOLD"}, position=position, rules=rules
        )
        assert result.final_verdict["final_stop_loss_pct"] == 12.0
        assert result.final_verdict["stop_loss_basis"] == "rule_fallback_capped_max"

    def test_stop_loss_below_hard_min_capped(self, rules):
        verdict = {
            "verdict": "HOLD",
            "suggested_sizing": "0%",
            "final_stop_loss_pct": 2.0,
        }
        position = Position(ticker="NVDA", shares=100, allocation_pct=8.0)
        result = validate_verdict(
            verdict, allowed_actions={"HOLD"}, position=position, rules=rules
        )
        assert result.final_verdict["final_stop_loss_pct"] == 4.0


# =============================================================================
# Default stop computation
# =============================================================================

class TestDefaultStopComputation:
    def test_normal_regime_uses_1_5x_atr(self, rules):
        # ATR 3% × 1.5 = 4.5%
        stop = compute_default_stop_pct(atr_pct=3.0, regime="BULL", rules=rules)
        assert stop == 4.5

    def test_volatile_regime_uses_2x_atr(self, rules):
        # ATR 3% × 2.0 = 6%
        stop = compute_default_stop_pct(atr_pct=3.0, regime="HIGH_VOLATILITY", rules=rules)
        assert stop == 6.0

    def test_capped_to_hard_max(self, rules):
        # ATR 8% × 2 = 16%, capped to 12
        stop = compute_default_stop_pct(atr_pct=8.0, regime="BEAR", rules=rules)
        assert stop == 12.0

    def test_floored_to_hard_min(self, rules):
        # ATR 1% × 1.5 = 1.5%, floored to 4
        stop = compute_default_stop_pct(atr_pct=1.0, regime="BULL", rules=rules)
        assert stop == 4.0
