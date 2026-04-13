"""
Debate engine — orchestrates Bull → Bear → Judge with escalation and validation.

Main flow:
  1. Pre-filter via rules_engine to compute allowed_actions
  2. If skip_debate, return automatic HOLD
  3. Build agent contexts (Bull, Bear)
  4. Run Bull and Bear in parallel
  5. Handle ABSTAIN cases (one side abstains → other wins by default)
  6. Decide escalation: both confidence > 70 → Opus with extended thinking
  7. Build Judge context (with both responses + ATR-based default stop)
  8. Run Judge
  9. Post-validate verdict, downgrade to HOLD if rule violations
  10. Return complete debate record with metrics

EOD phase upgrades (v3.2):
  - Judge ALWAYS uses Opus with extended thinking (regardless of escalation)
  - Judge has access to web search tool (max 3 searches)
  - Context includes today's activity (news + intraday debates)
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.earnings_calendar import check_blackout
from src.llm_clients import LLMResponse, call_claude, call_grok
from src.rules_engine import (
    Phase,
    Position,
    PortfolioState,
    compute_allowed_actions,
    compute_default_stop_pct,
    load_rules,
    validate_verdict,
)

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"

# Models from env
MODEL_BULL = os.getenv("MODEL_BULL", "grok-4-latest")
MODEL_BEAR = os.getenv("MODEL_BEAR", "claude-sonnet-4-6")
MODEL_JUDGE_DEFAULT = os.getenv("MODEL_JUDGE_DEFAULT", "claude-sonnet-4-6")
MODEL_JUDGE_ESCALATION = os.getenv("MODEL_JUDGE_ESCALATION", "claude-opus-4-6")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v3.2.0")

# Escalation thresholds (per v3.1)
ESCALATION_BOTH_HIGH_CONFIDENCE = 70
ESCALATION_DELTA_THRESHOLD = 20
ESCALATION_MIN_CONFIDENCE = 50

# Web search tool definition for Judge in EOD phase
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 3,
}


# =============================================================================
# Prompt loading
# =============================================================================

def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.txt").read_text()


# =============================================================================
# Context builders
# =============================================================================

def _build_researcher_context(
    phase: Phase,
    snapshot: dict[str, Any],
    position: Position,
    rules: dict[str, Any],
    allowed_actions: set[str],
    news_item: dict[str, Any] | None = None,
    memory_summary: str | None = None,
    today_activity: str | None = None,
) -> str:
    """Build the user message for Bull or Bear agents."""
    sections = []

    sections.append(f"CURRENT_PHASE: {phase.value}")
    sections.append("")

    sections.append("ALLOWED_ACTIONS:")
    sections.append(f"  {sorted(allowed_actions)}")
    sections.append(
        "  (Your suggested_action MUST be in this list. Other actions are "
        "blocked by user rules or phase constraints.)"
    )
    sections.append("")

    sections.append("MACRO CONTEXT:")
    sections.append(json.dumps(snapshot.get("macro_regime", {}), indent=2))
    sections.append("")

    sections.append(f"TICKER SNAPSHOT — {snapshot['ticker']}:")
    snapshot_for_prompt = {
        "ticker": snapshot["ticker"],
        "price": snapshot["price"],
        "change_pct": snapshot["change_pct"],
        "indicators": snapshot["indicators"],
        "fundamentals": snapshot["fundamentals"],
        "sector_benchmarks": snapshot["sector_benchmarks"],
        "signals_summary": snapshot["signals_summary"],
    }
    if snapshot.get("position_metrics"):
        snapshot_for_prompt["position_metrics"] = snapshot["position_metrics"]
    sections.append(json.dumps(snapshot_for_prompt, indent=2, default=str))
    sections.append("")

    if news_item is not None:
        sections.append("NEWS:")
        sections.append(json.dumps(news_item, indent=2))
        sections.append("")

    # Today's activity for EOD phase
    if today_activity is not None:
        sections.append("TODAY'S ACTIVITY:")
        sections.append(
            "  (News processed and intraday debates that ran today for this "
            "ticker. Use this to understand what happened during the trading "
            "day before making your EOD recommendation.)"
        )
        sections.append(today_activity)
        sections.append("")

    sections.append("CURRENT POSITION:")
    sections.append(json.dumps(asdict(position), indent=2))
    sections.append("")

    sections.append("USER RULES (relevant subset):")
    sections.append(json.dumps({
        "position_sizing": rules.get("position_sizing", {}),
        "stop_loss": rules.get("stop_loss", {}),
        "take_profit": rules.get("take_profit", {}),
        "blackouts": rules.get("blackouts", {}),
    }, indent=2))
    sections.append("")

    if memory_summary:
        sections.append("MEMORY:")
        sections.append(memory_summary)
        sections.append("")

    sections.append(
        "Respond with ONLY the JSON object specified in your instructions. "
        "No preamble, no markdown fences, no commentary."
    )

    return "\n".join(sections)


def _build_judge_context(
    phase: Phase,
    snapshot: dict[str, Any],
    position: Position,
    rules: dict[str, Any],
    allowed_actions: set[str],
    bull_response: dict[str, Any],
    bear_response: dict[str, Any],
    default_stop_pct: float,
    escalated: bool,
    news_item: dict[str, Any] | None = None,
    memory_summary: str | None = None,
    today_activity: str | None = None,
    web_search_enabled: bool = False,
) -> str:
    """Build the user message for the Judge."""
    sections = []

    sections.append(f"CURRENT_PHASE: {phase.value}")
    sections.append("")

    sections.append("ALLOWED_ACTIONS:")
    sections.append(f"  {sorted(allowed_actions)}")
    sections.append("  (Your verdict MUST be in this list.)")
    sections.append("")

    sections.append(f"DEFAULT_STOP_LOSS_PCT: {default_stop_pct}")
    sections.append(
        "  (ATR-based default. Override only with strong narrative justification.)"
    )
    sections.append("")

    # Web search instructions (only for EOD with Opus)
    if web_search_enabled:
        ticker = snapshot.get("ticker", "")
        sections.append("=" * 70)
        sections.append("WEB SEARCH AVAILABLE")
        sections.append("=" * 70)
        sections.append(
            "You have access to a web search tool. BEFORE issuing your final "
            "verdict, use it to research:"
        )
        sections.append(
            f"  1. Recent analyst upgrades, downgrades, or price target "
            f"changes for {ticker} (last 7 days)"
        )
        sections.append(
            f"  2. Breaking news or material developments for {ticker} "
            f"from the last 48 hours that may not be in the snapshot"
        )
        sections.append(
            f"  3. Sector-level news or macro events that could affect "
            f"this position"
        )
        sections.append("")
        sections.append(
            "You may search up to 3 times. Focus on information that could "
            "MATERIALLY change the verdict. If search results confirm what's "
            "already in the snapshot, that's fine — it increases confidence. "
            "Incorporate findings into your reasoning field."
        )
        sections.append("")
        sections.append(
            "IMPORTANT: After completing your research, respond with ONLY the "
            "JSON object specified in your instructions. The JSON must be your "
            "final message."
        )
        sections.append("")

    sections.append("MACRO CONTEXT:")
    sections.append(json.dumps(snapshot.get("macro_regime", {}), indent=2))
    sections.append("")

    sections.append(f"TICKER SNAPSHOT — {snapshot['ticker']}:")
    judge_snapshot_view = {
        "ticker": snapshot["ticker"],
        "price": snapshot["price"],
        "change_pct": snapshot["change_pct"],
        "indicators": snapshot["indicators"],
        "fundamentals": snapshot["fundamentals"],
        "sector_benchmarks": snapshot["sector_benchmarks"],
    }
    if snapshot.get("position_metrics"):
        judge_snapshot_view["position_metrics"] = snapshot["position_metrics"]
    sections.append(json.dumps(judge_snapshot_view, indent=2, default=str))
    sections.append("")

    if news_item is not None:
        sections.append("NEWS:")
        sections.append(json.dumps(news_item, indent=2))
        sections.append("")

    # Today's activity for EOD phase
    if today_activity is not None:
        sections.append("TODAY'S ACTIVITY:")
        sections.append(
            "  (News processed and intraday debates that ran today for this "
            "ticker. Consider these when synthesizing your verdict — avoid "
            "contradicting an intraday HOLD unless new information justifies it.)"
        )
        sections.append(today_activity)
        sections.append("")

    sections.append("CURRENT POSITION:")
    sections.append(json.dumps(asdict(position), indent=2))
    sections.append("")

    sections.append("USER RULES (relevant subset):")
    sections.append(json.dumps({
        "position_sizing": rules.get("position_sizing", {}),
        "stop_loss": rules.get("stop_loss", {}),
        "take_profit": rules.get("take_profit", {}),
    }, indent=2))
    sections.append("")

    sections.append("=" * 70)
    sections.append("BULL ARGUMENT:")
    sections.append("=" * 70)
    sections.append(json.dumps(bull_response, indent=2))
    sections.append("")

    sections.append("=" * 70)
    sections.append("BEAR ARGUMENT:")
    sections.append("=" * 70)
    sections.append(json.dumps(bear_response, indent=2))
    sections.append("")

    if escalated:
        sections.append("=" * 70)
        sections.append("ESCALATION NOTE")
        sections.append("=" * 70)
        sections.append(
            "This case was escalated because both analysts are persuasive and "
            "disagree strongly. Your task is NOT to decide which rhetoric is "
            "better — it is to identify which underlying factual premise is in "
            "dispute. Use your extended thinking to verify each cited figure "
            "against the snapshot before issuing the verdict."
        )
        sections.append("")

    if memory_summary:
        sections.append("MEMORY:")
        sections.append(memory_summary)
        sections.append("")

    # Final instruction — varies based on web search
    if not web_search_enabled:
        sections.append(
            "Respond with ONLY the JSON object specified in your instructions. "
            "No preamble, no markdown fences, no commentary."
        )

    return "\n".join(sections)


# =============================================================================
# Escalation logic
# =============================================================================

def _should_escalate(
    bull_confidence: int,
    bear_confidence: int,
    bull_action: str,
    bear_action: str,
) -> bool:
    """
    Decide whether to escalate due to strong disagreement.

    Per v3.1 Delta 3, never escalate when one side abstained — there is
    no real disagreement to resolve.

    Note: for EOD phase, Opus + thinking is ALWAYS used regardless of
    this function's result. This function only controls the ESCALATION
    NOTE in the context (which tells the Judge to resolve a specific
    disagreement between Bull and Bear).
    """
    if bull_action == "ABSTAIN" or bear_action == "ABSTAIN":
        return False

    # Both very confident
    if bull_confidence > ESCALATION_BOTH_HIGH_CONFIDENCE and bear_confidence > ESCALATION_BOTH_HIGH_CONFIDENCE:
        return True

    # Tightly contested with both sides moderately confident
    delta = abs(bull_confidence - bear_confidence)
    min_conf = min(bull_confidence, bear_confidence)
    if delta < ESCALATION_DELTA_THRESHOLD and min_conf > ESCALATION_MIN_CONFIDENCE:
        return True

    return False


# =============================================================================
# Main orchestration
# =============================================================================

@dataclass
class DebateResult:
    """Complete record of one debate."""

    debate_id: str
    timestamp: str
    phase: str
    ticker: str
    prompt_version: str
    regime: str

    allowed_actions: list[str]
    skip_debate: bool
    skip_reason: str | None

    bull_response: dict[str, Any] | None
    bull_metrics: dict[str, Any] | None

    bear_response: dict[str, Any] | None
    bear_metrics: dict[str, Any] | None

    judge_response: dict[str, Any] | None
    judge_metrics: dict[str, Any] | None
    judge_escalated: bool

    final_verdict: dict[str, Any] | None
    rule_violations: list[dict[str, Any]]
    was_downgraded: bool

    total_cost_usd: float
    total_latency_ms: int


def _llm_response_metrics(resp: LLMResponse) -> dict[str, Any]:
    return {
        "model": resp.model,
        "tokens_in": resp.tokens_in,
        "tokens_out": resp.tokens_out,
        "cost_usd": round(resp.cost_usd, 5),
        "latency_ms": resp.latency_ms,
        "parse_ok": resp.parse_ok,
    }


async def run_debate(
    ticker: str,
    phase: Phase | str,
    snapshot: dict[str, Any],
    position: Position,
    portfolio: PortfolioState,
    news_item: dict[str, Any] | None = None,
    memory_summary: str | None = None,
    blackout_active: bool = False,
    today_activity: str | None = None,
) -> DebateResult:
    """
    Run a complete Bull → Bear → Judge debate for the given ticker.

    This is the main entry point. It handles pre-filtering, parallel agent
    calls, ABSTAIN logic, escalation, and post-validation.

    For EOD phase (v3.2):
      - Judge ALWAYS uses Opus with extended thinking
      - Judge has web search tool access (up to 3 searches)
      - today_activity provides context of the day's news + intraday debates

    Args:
        ticker: Stock ticker (e.g. "NVDA").
        phase: Debate phase (INITIAL, INTRADAY, EOD, DISCOVERY).
        snapshot: Full ticker snapshot from compute_snapshot.
        position: Current position state for this ticker.
        portfolio: Portfolio-level state for circuit breakers.
        news_item: News payload (only for INTRADAY phase).
        memory_summary: Past opinions/memory to inject.
        blackout_active: Whether earnings blackout is active.
        today_activity: Summary of today's news + intraday debates
            for this ticker (used in EOD phase).
    """
    if isinstance(phase, str):
        phase = Phase(phase)

    debate_id = f"{ticker}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    start_total = datetime.now(timezone.utc)

    rules = load_rules()
    regime = snapshot.get("macro_regime", {}).get("regime", "UNKNOWN")

    # -------------------------------------------------------------------------
    # 1. Auto-detect earnings blackout from snapshot
    # -------------------------------------------------------------------------
    if not blackout_active:
        # Auto-compute from snapshot's next_earnings_date
        blackout_status = check_blackout(snapshot, rules)
        blackout_active = blackout_status.active
        if blackout_active:
            print(f"   ⚠️  EARNINGS BLACKOUT: {blackout_status.reason}")

    # -------------------------------------------------------------------------
    # 2. Pre-filter
    # -------------------------------------------------------------------------
    filter_result = compute_allowed_actions(
        phase=phase,
        position=position,
        portfolio=portfolio,
        rules=rules,
        blackout_active=blackout_active,
    )

    if filter_result.skip_debate:
        # Short-circuit: no informative debate possible
        return DebateResult(
            debate_id=debate_id,
            timestamp=start_total.isoformat(),
            phase=phase.value,
            ticker=ticker,
            prompt_version=PROMPT_VERSION,
            regime=regime,
            allowed_actions=sorted(filter_result.allowed_actions),
            skip_debate=True,
            skip_reason=filter_result.skip_reason,
            bull_response=None,
            bull_metrics=None,
            bear_response=None,
            bear_metrics=None,
            judge_response=None,
            judge_metrics=None,
            judge_escalated=False,
            final_verdict={
                "verdict": "HOLD",
                "confidence": 100,
                "reasoning": f"Auto-HOLD: {filter_result.skip_reason}",
            },
            rule_violations=[],
            was_downgraded=False,
            total_cost_usd=0.0,
            total_latency_ms=0,
        )

    # -------------------------------------------------------------------------
    # 2. Build researcher contexts
    # -------------------------------------------------------------------------
    bull_system = _load_prompt("bull")
    bear_system = _load_prompt("bear")
    judge_system = _load_prompt("judge")

    researcher_context = _build_researcher_context(
        phase=phase,
        snapshot=snapshot,
        position=position,
        rules=rules,
        allowed_actions=filter_result.allowed_actions,
        news_item=news_item,
        memory_summary=memory_summary,
        today_activity=today_activity,
    )

    # -------------------------------------------------------------------------
    # 3. Run Bull and Bear in parallel
    # -------------------------------------------------------------------------
    bull_task = call_grok(
        model=MODEL_BULL,
        system=bull_system,
        user=researcher_context,
        max_tokens=1500,
        temperature=0.7,
    )
    bear_task = call_claude(
        model=MODEL_BEAR,
        system=bear_system,
        user=researcher_context,
        max_tokens=1500,
        temperature=0.5,
    )
    bull_resp, bear_resp = await asyncio.gather(bull_task, bear_task)

    bull_action = bull_resp.content.get("suggested_action", "ABSTAIN")
    bear_action = bear_resp.content.get("suggested_action", "ABSTAIN")
    bull_confidence = bull_resp.content.get("confidence", 0)
    bear_confidence = bear_resp.content.get("confidence", 0)

    # -------------------------------------------------------------------------
    # 4. Decide escalation and EOD upgrades
    # -------------------------------------------------------------------------

    # Disagreement-based escalation (adds ESCALATION NOTE to context)
    escalated_disagreement = _should_escalate(
        bull_confidence, bear_confidence, bull_action, bear_action
    )

    # EOD phase: ALWAYS use Opus + thinking + web search
    is_eod = (phase == Phase.EOD)

    use_opus = is_eod or escalated_disagreement
    use_thinking = is_eod or escalated_disagreement
    use_web_search = is_eod  # web search only for EOD

    # For the DebateResult record, track disagreement escalation specifically
    judge_escalated_for_record = escalated_disagreement

    # -------------------------------------------------------------------------
    # 5. Compute ATR-based default stop for Judge context
    # -------------------------------------------------------------------------
    atr_pct = snapshot.get("indicators", {}).get("atr_pct", 3.0)
    default_stop = compute_default_stop_pct(
        atr_pct=atr_pct, regime=regime, rules=rules
    )

    # -------------------------------------------------------------------------
    # 6. Build Judge context and run Judge
    # -------------------------------------------------------------------------
    judge_context = _build_judge_context(
        phase=phase,
        snapshot=snapshot,
        position=position,
        rules=rules,
        allowed_actions=filter_result.allowed_actions,
        bull_response=bull_resp.content,
        bear_response=bear_resp.content,
        default_stop_pct=default_stop,
        escalated=escalated_disagreement,
        news_item=news_item,
        memory_summary=memory_summary,
        today_activity=today_activity,
        web_search_enabled=use_web_search,
    )

    judge_model = MODEL_JUDGE_ESCALATION if use_opus else MODEL_JUDGE_DEFAULT

    # Prepare tools list for Judge
    judge_tools = [WEB_SEARCH_TOOL] if use_web_search else None

    # max_tokens: generous for Opus + thinking + web search
    if use_thinking and use_web_search:
        judge_max_tokens = 16000
    elif use_thinking:
        judge_max_tokens = 10000
    else:
        judge_max_tokens = 1500

    judge_resp = await call_claude(
        model=judge_model,
        system=judge_system,
        user=judge_context,
        max_tokens=judge_max_tokens,
        temperature=0.3,
        extended_thinking=use_thinking,
        tools=judge_tools,
    )

    # -------------------------------------------------------------------------
    # 7. Post-validate verdict
    # -------------------------------------------------------------------------
    validation = validate_verdict(
        verdict=judge_resp.content,
        allowed_actions=filter_result.allowed_actions,
        position=position,
        rules=rules,
    )

    # -------------------------------------------------------------------------
    # 8. Aggregate and return
    # -------------------------------------------------------------------------
    total_cost = bull_resp.cost_usd + bear_resp.cost_usd + judge_resp.cost_usd
    total_latency = (
        bull_resp.latency_ms + bear_resp.latency_ms + judge_resp.latency_ms
    )

    return DebateResult(
        debate_id=debate_id,
        timestamp=start_total.isoformat(),
        phase=phase.value,
        ticker=ticker,
        prompt_version=PROMPT_VERSION,
        regime=regime,
        allowed_actions=sorted(filter_result.allowed_actions),
        skip_debate=False,
        skip_reason=None,
        bull_response=bull_resp.content,
        bull_metrics=_llm_response_metrics(bull_resp),
        bear_response=bear_resp.content,
        bear_metrics=_llm_response_metrics(bear_resp),
        judge_response=judge_resp.content,
        judge_metrics=_llm_response_metrics(judge_resp),
        judge_escalated=judge_escalated_for_record,
        final_verdict=validation.final_verdict,
        rule_violations=validation.violations,
        was_downgraded=validation.was_downgraded,
        total_cost_usd=round(total_cost, 5),
        total_latency_ms=total_latency,
    )
