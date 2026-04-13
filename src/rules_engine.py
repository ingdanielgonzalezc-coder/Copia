"""
Rules engine — deterministic pre-filter and post-validator.

Per v3.1 Delta 4 + Patch refinements, hard rules are NEVER delegated to LLMs.
This module is the single source of truth for what actions are legal.

Two-stage enforcement:
  1. PRE-FILTER: compute_allowed_actions() restricts the action space
     BEFORE the debate runs. Bull and Bear receive this list and can only
     suggest actions within it.
  2. POST-VALIDATOR: validate_verdict() catches any LLM hallucination that
     produced an illegal verdict and downgrades it to HOLD.

Both functions are pure: same input → same output, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_ROOT / "config" / "rules.yaml"


class Phase(str, Enum):
    INITIAL = "INITIAL"
    INTRADAY = "INTRADAY"
    EOD = "EOD"
    DISCOVERY = "DISCOVERY"


# All possible actions across all phases
ALL_ACTIONS = {
    "BUY_NEW", "BUY", "ADD",
    "HOLD",
    "TRIM", "SELL",
    "AVOID_NEW",
    "ABSTAIN",
}

# Actions valid per phase (before applying rule restrictions)
PHASE_ACTIONS: dict[Phase, set[str]] = {
    Phase.INITIAL: {"BUY_NEW", "ADD", "HOLD", "AVOID_NEW", "ABSTAIN"},
    Phase.INTRADAY: {"HOLD", "BUY", "SELL", "TRIM", "ABSTAIN"},
    Phase.EOD: {"HOLD", "ADD", "TRIM", "SELL", "BUY_NEW", "AVOID_NEW", "ABSTAIN"},
    Phase.DISCOVERY: {"BUY_NEW", "AVOID_NEW", "HOLD", "ABSTAIN"},
}


@dataclass
class Position:
    """Current position state for a ticker."""

    ticker: str
    shares: float = 0.0
    cost_basis: float | None = None
    allocation_pct: float = 0.0
    sector: str | None = None
    unrealized_pnl_pct: float | None = None
    last_debate_at: str | None = None
    flag_review: bool = False

    @property
    def has_position(self) -> bool:
        return self.shares > 0


@dataclass
class PortfolioState:
    """Portfolio-level state for circuit-breaker evaluation."""

    total_open_positions: int = 0
    daily_pnl_pct: float = 0.0
    drawdown_from_peak_pct: float = 0.0
    defensive_mode: bool = False
    paused: bool = False
    sector_allocations: dict[str, float] = field(default_factory=dict)


@dataclass
class FilterResult:
    """Output of compute_allowed_actions()."""

    allowed_actions: set[str]
    skip_debate: bool
    skip_reason: str | None
    restrictions_applied: list[str]


# =============================================================================
# Rules loading
# =============================================================================

def load_rules(path: Path | None = None) -> dict[str, Any]:
    """Load rules.yaml. Cached load is fine — rules don't change at runtime."""
    rules_path = path or RULES_PATH
    if not rules_path.exists():
        raise FileNotFoundError(f"rules.yaml not found at {rules_path}")
    with open(rules_path) as f:
        return yaml.safe_load(f)


# =============================================================================
# Pre-filter: compute allowed actions
# =============================================================================

def compute_allowed_actions(
    phase: Phase | str,
    position: Position,
    portfolio: PortfolioState,
    rules: dict[str, Any],
    blackout_active: bool = False,
) -> FilterResult:
    """
    Compute which actions are legal for this debate.

    Returns a FilterResult with the allowed action set, a skip flag if no
    informative debate is possible, and a list of which restrictions were
    applied (for audit logging).
    """
    if isinstance(phase, str):
        phase = Phase(phase)

    allowed = set(PHASE_ACTIONS[phase])
    restrictions: list[str] = []

    # -------------------------------------------------------------------------
    # Circuit breakers — pause overrides everything
    # -------------------------------------------------------------------------
    if portfolio.paused:
        return FilterResult(
            allowed_actions={"HOLD", "ABSTAIN"},
            skip_debate=True,
            skip_reason="portfolio_paused_manual_review_required",
            restrictions_applied=["portfolio_paused"],
        )

    if portfolio.defensive_mode:
        # Defensive mode: no new entries, only risk reduction
        allowed -= {"BUY_NEW", "BUY", "ADD"}
        restrictions.append("defensive_mode_active")

    # -------------------------------------------------------------------------
    # Position-based restrictions
    # -------------------------------------------------------------------------
    if not position.has_position:
        # Cannot sell what you don't own
        allowed -= {"TRIM", "SELL"}
        # In INITIAL/DISCOVERY HOLD doesn't make sense (you have nothing to hold)
        if phase in (Phase.INITIAL, Phase.DISCOVERY):
            allowed -= {"HOLD"}
            allowed |= {"AVOID_NEW", "ABSTAIN"}
            if phase == Phase.INITIAL:
                allowed |= {"BUY_NEW"}

    # -------------------------------------------------------------------------
    # Allocation limits per ticker
    # -------------------------------------------------------------------------
    pos_rules = rules.get("position_sizing", {})
    max_alloc = pos_rules.get("max_allocation_per_ticker_pct", 100.0)
    headroom = max_alloc - position.allocation_pct

    if headroom <= 0.5:  # less than 0.5% headroom
        allowed -= {"BUY_NEW", "BUY", "ADD"}
        restrictions.append(f"max_allocation_reached_{position.allocation_pct:.1f}pct")

    # -------------------------------------------------------------------------
    # Max open positions
    # -------------------------------------------------------------------------
    max_positions = pos_rules.get("max_open_positions", 100)
    if (
        portfolio.total_open_positions >= max_positions
        and not position.has_position
    ):
        allowed -= {"BUY_NEW"}
        restrictions.append(f"max_open_positions_reached_{max_positions}")

    # -------------------------------------------------------------------------
    # Sector concentration
    # -------------------------------------------------------------------------
    sector_rules = rules.get("sector_limits", {})
    max_sector_alloc = sector_rules.get("max_allocation_per_sector_pct", 100.0)
    if position.sector:
        current_sector_alloc = portfolio.sector_allocations.get(position.sector, 0.0)
        sector_headroom = max_sector_alloc - current_sector_alloc
        if sector_headroom <= 0.5 and not position.has_position:
            allowed -= {"BUY_NEW", "BUY", "ADD"}
            restrictions.append(
                f"sector_max_allocation_reached_{position.sector}_{current_sector_alloc:.1f}pct"
            )

    # -------------------------------------------------------------------------
    # Earnings / restricted ticker blackouts
    # -------------------------------------------------------------------------
    blackout_rules = rules.get("blackouts", {})
    if blackout_rules.get("enabled", True):
        restricted = blackout_rules.get("restricted_tickers", []) or []
        if position.ticker in restricted:
            blocked_actions = set(blackout_rules.get("blackout_actions", []))
            allowed -= blocked_actions
            restrictions.append(f"ticker_in_restricted_list")

        if blackout_active:
            blocked_actions = set(blackout_rules.get("blackout_actions", []))
            allowed -= blocked_actions
            restrictions.append("earnings_blackout_active")

    # -------------------------------------------------------------------------
    # Decide if the debate is worth running
    # -------------------------------------------------------------------------
    actionable = allowed - {"HOLD", "ABSTAIN", "AVOID_NEW"}

    skip_debate = False
    skip_reason = None
    if not actionable and phase != Phase.INITIAL and phase != Phase.DISCOVERY:
        # Nothing actionable left; HOLD by default
        skip_debate = True
        skip_reason = "no_actionable_options_remaining"

    return FilterResult(
        allowed_actions=allowed,
        skip_debate=skip_debate,
        skip_reason=skip_reason,
        restrictions_applied=restrictions,
    )


# =============================================================================
# Post-validator: catch illegal verdicts
# =============================================================================

@dataclass
class ValidationResult:
    """Output of validate_verdict()."""

    is_valid: bool
    final_verdict: dict[str, Any]
    violations: list[dict[str, Any]]
    was_downgraded: bool


def _parse_sizing(sizing_str: str | None) -> float | None:
    """Parse '+5%' or '-10%' or '0%' into float. Returns None if not parseable."""
    if sizing_str is None or sizing_str == "null":
        return None
    s = sizing_str.strip().rstrip("%").replace("+", "")
    try:
        return float(s)
    except ValueError:
        return None


def validate_verdict(
    verdict: dict[str, Any],
    allowed_actions: set[str],
    position: Position,
    rules: dict[str, Any],
) -> ValidationResult:
    """
    Validate a Judge verdict against hard rules.

    If the verdict violates rules, it is downgraded to a safe HOLD with the
    violation logged. Returns the final (possibly modified) verdict plus a
    list of any violations caught.
    """
    violations: list[dict[str, Any]] = []
    final = dict(verdict)  # shallow copy
    was_downgraded = False

    # Check 1: verdict must be in allowed_actions
    proposed_action = verdict.get("verdict")
    if proposed_action not in allowed_actions:
        violations.append({
            "type": "verdict_not_in_allowed_actions",
            "original_verdict": proposed_action,
            "allowed_actions": sorted(allowed_actions),
            "rule_breached": "phase_or_pre_filter_restriction",
        })
        was_downgraded = True
        final = _downgrade_to_hold(final, "verdict_not_in_allowed")

    # Check 2: sizing must respect max allocation
    sizing = _parse_sizing(verdict.get("suggested_sizing"))
    if sizing is not None and sizing != 0:
        new_alloc = position.allocation_pct + sizing
        max_alloc = rules.get("position_sizing", {}).get(
            "max_allocation_per_ticker_pct", 100.0
        )
        if new_alloc > max_alloc:
            violations.append({
                "type": "sizing_exceeds_max_allocation",
                "original_sizing": verdict.get("suggested_sizing"),
                "current_allocation": position.allocation_pct,
                "would_become": new_alloc,
                "max_allowed": max_alloc,
                "rule_breached": "max_allocation_per_ticker_pct",
            })
            was_downgraded = True
            final = _downgrade_to_hold(final, "sizing_exceeds_max")

        if new_alloc < 0:
            violations.append({
                "type": "sizing_produces_negative_allocation",
                "original_sizing": verdict.get("suggested_sizing"),
                "current_allocation": position.allocation_pct,
                "would_become": new_alloc,
                "rule_breached": "non_negative_allocation",
            })
            was_downgraded = True
            final = _downgrade_to_hold(final, "negative_allocation")

    # Check 3: stop loss within hard min/max bounds
    stop_pct = verdict.get("final_stop_loss_pct")
    if stop_pct is not None:
        stop_rules = rules.get("stop_loss", {})
        hard_max = stop_rules.get("hard_max_pct", 100.0)
        hard_min = stop_rules.get("hard_min_pct", 0.0)
        if stop_pct > hard_max:
            violations.append({
                "type": "stop_loss_exceeds_hard_max",
                "original_stop_pct": stop_pct,
                "hard_max_pct": hard_max,
                "rule_breached": "stop_loss.hard_max_pct",
            })
            final["final_stop_loss_pct"] = hard_max
            final["stop_loss_basis"] = "rule_fallback_capped_max"
        if stop_pct < hard_min:
            violations.append({
                "type": "stop_loss_below_hard_min",
                "original_stop_pct": stop_pct,
                "hard_min_pct": hard_min,
                "rule_breached": "stop_loss.hard_min_pct",
            })
            final["final_stop_loss_pct"] = hard_min
            final["stop_loss_basis"] = "rule_fallback_capped_min"

    return ValidationResult(
        is_valid=(len(violations) == 0),
        final_verdict=final,
        violations=violations,
        was_downgraded=was_downgraded,
    )


def _downgrade_to_hold(verdict: dict[str, Any], reason: str) -> dict[str, Any]:
    """Force a verdict to HOLD with metadata."""
    downgraded = dict(verdict)
    downgraded["verdict"] = "HOLD"
    downgraded["suggested_sizing"] = None
    downgraded["downgraded"] = True
    downgraded["downgrade_reason"] = reason
    return downgraded


# =============================================================================
# Helper: compute default stop-loss from ATR
# =============================================================================

def compute_default_stop_pct(
    atr_pct: float,
    regime: str,
    rules: dict[str, Any],
) -> float:
    """
    Compute the default ATR-based stop loss percentage.

    Uses 2.0x ATR for HIGH_VOLATILITY/BEAR regimes and 1.5x ATR for
    BULL/NEUTRAL regimes, then clamps to hard min/max bounds.
    """
    stop_rules = rules.get("stop_loss", {})

    if regime in ("HIGH_VOLATILITY", "BEAR"):
        multiplier = stop_rules.get("atr_multiplier_volatile", 2.0)
    else:
        multiplier = stop_rules.get("atr_multiplier_normal", 1.5)

    stop_pct = atr_pct * multiplier

    hard_max = stop_rules.get("hard_max_pct", 12.0)
    hard_min = stop_rules.get("hard_min_pct", 4.0)
    stop_pct = max(hard_min, min(hard_max, stop_pct))

    return round(stop_pct, 1)
