"""
Supabase persistence layer.

Wraps the Supabase client with typed save_* functions for each table.
All functions are best-effort: failures are logged but never raised, so
that a database outage cannot lose an expensive LLM call.

Each function returns the inserted row's id (UUID string) or None on failure.

Usage:
    from src.db import save_debate, save_rule_violations, save_paper_trade

    debate_uuid = save_debate(result, snapshot, position)
    if debate_uuid and result.rule_violations:
        save_rule_violations(debate_uuid, result.rule_violations)
    if debate_uuid:
        save_paper_trade(debate_uuid, result, snapshot)
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

from src.debate_engine import DebateResult
from src.macro_context import MacroSnapshot
from src.rules_engine import Position

load_dotenv()

_client: Client | None = None


def get_client() -> Client:
    """Lazy singleton Supabase client."""
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env"
            )
        _client = create_client(url, key)
    return _client


def _safe_execute(operation_name: str, fn) -> Any | None:
    """Run a Supabase operation, log errors, return None on failure."""
    try:
        return fn()
    except Exception as e:
        print(f"⚠️  DB error in {operation_name}: {type(e).__name__}: {e}")
        return None


# =============================================================================
# Debates
# =============================================================================

def save_debate(
    result: DebateResult,
    snapshot: dict[str, Any],
    position: Position,
    news_item: dict[str, Any] | None = None,
    trigger_type: str = "manual",
    trigger_data: dict[str, Any] | None = None,
) -> str | None:
    """
    Persist a complete debate to the `debates` table.

    Returns the inserted row's UUID, or None on failure.
    """
    macro = snapshot.get("macro_regime", {})

    row: dict[str, Any] = {
        "timestamp": result.timestamp,
        "phase": result.phase,
        "ticker": result.ticker,
        "prompt_version": result.prompt_version,
        "regime": result.regime,
        "spy_price": macro.get("spy_price"),
        "vix_level": macro.get("vix_level"),
        "trigger_type": trigger_type,
        "trigger_data": trigger_data,
        "snapshot": snapshot,
        "position_at_debate": asdict(position),
        "allowed_actions": result.allowed_actions,
        "news_item": news_item,
        "verdict": result.final_verdict.get("verdict") if result.final_verdict else None,
        "confidence": result.final_verdict.get("confidence") if result.final_verdict else None,
        "rules_violated": len(result.rule_violations) > 0,
        "total_cost_usd": result.total_cost_usd,
        "total_latency_ms": result.total_latency_ms,
    }

    # Bull metrics (None if debate was skipped)
    if result.bull_response is not None and result.bull_metrics is not None:
        row.update({
            "bull_response": result.bull_response,
            "bull_model": result.bull_metrics.get("model"),
            "bull_latency_ms": result.bull_metrics.get("latency_ms"),
            "bull_tokens_in": result.bull_metrics.get("tokens_in"),
            "bull_tokens_out": result.bull_metrics.get("tokens_out"),
            "bull_cost_usd": result.bull_metrics.get("cost_usd"),
        })

    # Bear metrics
    if result.bear_response is not None and result.bear_metrics is not None:
        row.update({
            "bear_response": result.bear_response,
            "bear_model": result.bear_metrics.get("model"),
            "bear_latency_ms": result.bear_metrics.get("latency_ms"),
            "bear_tokens_in": result.bear_metrics.get("tokens_in"),
            "bear_tokens_out": result.bear_metrics.get("tokens_out"),
            "bear_cost_usd": result.bear_metrics.get("cost_usd"),
        })

    # Judge metrics
    if result.judge_response is not None and result.judge_metrics is not None:
        row.update({
            "judge_response": result.judge_response,
            "judge_model": result.judge_metrics.get("model"),
            "judge_escalated": result.judge_escalated,
            "judge_latency_ms": result.judge_metrics.get("latency_ms"),
            "judge_tokens_in": result.judge_metrics.get("tokens_in"),
            "judge_tokens_out": result.judge_metrics.get("tokens_out"),
            "judge_cost_usd": result.judge_metrics.get("cost_usd"),
        })

    # If post-validator downgraded the verdict, capture the original
    if result.was_downgraded and result.judge_response:
        row["verdict_pre_validator"] = result.judge_response.get("verdict")

    def _do():
        client = get_client()
        response = client.table("debates").insert(row).execute()
        return response.data[0]["id"] if response.data else None

    return _safe_execute("save_debate", _do)


# =============================================================================
# Rule violations
# =============================================================================

def save_rule_violations(
    debate_uuid: str,
    violations: list[dict[str, Any]],
) -> int:
    """
    Persist rule violations caught by the post-validator.

    Returns the number of violations successfully saved.
    """
    if not violations:
        return 0

    rows = []
    for v in violations:
        rows.append({
            "debate_id": debate_uuid,
            "violation_type": v.get("type", "unknown"),
            "original_verdict": v.get("original_verdict"),
            "downgraded_to": "HOLD",
            "rule_breached": v.get("rule_breached"),
            "details": v,
        })

    def _do():
        client = get_client()
        response = client.table("rule_violations").insert(rows).execute()
        return len(response.data) if response.data else 0

    result = _safe_execute("save_rule_violations", _do)
    return result or 0


# =============================================================================
# Paper trades (simulated execution for outcome tracking)
# =============================================================================

def save_paper_trade(
    debate_uuid: str,
    result: DebateResult,
    snapshot: dict[str, Any],
) -> str | None:
    """
    Record a paper trade simulating the verdict's execution.

    Outcomes (1d/1w/1m) are computed later by a separate outcome tracker job.
    """
    if not result.final_verdict:
        return None

    verdict = result.final_verdict.get("verdict", "HOLD")
    sizing_str = result.final_verdict.get("suggested_sizing")

    # Parse sizing percentage (e.g. "+5%", "-10%", "0%", None)
    shares_change_pct = 0.0
    if sizing_str and isinstance(sizing_str, str):
        try:
            shares_change_pct = float(sizing_str.strip().rstrip("%").replace("+", ""))
        except ValueError:
            shares_change_pct = 0.0

    row = {
        "debate_id": debate_uuid,
        "timestamp": result.timestamp,
        "ticker": result.ticker,
        "simulated_action": verdict,
        "price_at_decision": snapshot.get("price"),
        "shares_change": shares_change_pct,  # percentage allocation change
        "allocation_pct": shares_change_pct,
        "macro_regime": result.regime,
    }

    def _do():
        client = get_client()
        response = client.table("paper_trades").insert(row).execute()
        return response.data[0]["id"] if response.data else None

    return _safe_execute("save_paper_trade", _do)


# =============================================================================
# Opinions (memory layer for future debates on the same ticker)
# =============================================================================

def save_opinion(
    debate_uuid: str,
    result: DebateResult,
) -> str | None:
    """
    Save a one-line opinion summary that may be injected into future debates
    as historical memory for the same ticker.
    """
    if not result.final_verdict:
        return None

    verdict = result.final_verdict.get("verdict", "HOLD")
    confidence = result.final_verdict.get("confidence", 0)
    reasoning = result.final_verdict.get("reasoning", "")

    # Try to derive time horizon from bull or bear response
    time_horizon = None
    for resp in (result.bull_response, result.bear_response):
        if resp and "time_horizon" in resp:
            time_horizon = resp["time_horizon"]
            break

    summary = f"{verdict} (conf {confidence}): {reasoning[:200]}"

    row = {
        "ticker": result.ticker,
        "debate_id": debate_uuid,
        "timestamp": result.timestamp,
        "verdict": verdict,
        "confidence": confidence,
        "time_horizon": time_horizon,
        "summary": summary,
    }

    def _do():
        client = get_client()
        response = client.table("opinions").insert(row).execute()
        return response.data[0]["id"] if response.data else None

    return _safe_execute("save_opinion", _do)


# =============================================================================
# Macro snapshots
# =============================================================================

def save_macro_snapshot(macro: MacroSnapshot) -> str | None:
    """Persist a macro snapshot for historical regime tracking."""
    row = {
        "timestamp": macro.timestamp.isoformat(),
        "spy_price": macro.spy_price,
        "spy_ma200": macro.spy_ma200,
        "spy_ma50": macro.spy_ma50,
        "vix_level": macro.vix_level,
        "regime": macro.regime,
    }

    def _do():
        client = get_client()
        response = client.table("macro_snapshots").insert(row).execute()
        return response.data[0]["id"] if response.data else None

    return _safe_execute("save_macro_snapshot", _do)


# =============================================================================
# Convenience: persist everything related to a single debate
# =============================================================================

def persist_debate_complete(
    result: DebateResult,
    snapshot: dict[str, Any],
    position: Position,
    news_item: dict[str, Any] | None = None,
    trigger_type: str = "manual",
) -> dict[str, Any]:
    """
    Persist a debate and all its related records in one call.

    Returns a dict with the ids of each record created and any errors:
        {
            "debate_uuid": "...",
            "violations_saved": 0,
            "paper_trade_uuid": "...",
            "opinion_uuid": "...",
            "errors": [],
        }
    """
    output: dict[str, Any] = {
        "debate_uuid": None,
        "violations_saved": 0,
        "paper_trade_uuid": None,
        "opinion_uuid": None,
        "errors": [],
    }

    debate_uuid = save_debate(
        result=result,
        snapshot=snapshot,
        position=position,
        news_item=news_item,
        trigger_type=trigger_type,
    )
    output["debate_uuid"] = debate_uuid

    if not debate_uuid:
        output["errors"].append("save_debate returned None — see logs above")
        return output

    if result.rule_violations:
        output["violations_saved"] = save_rule_violations(
            debate_uuid, result.rule_violations
        )

    output["paper_trade_uuid"] = save_paper_trade(debate_uuid, result, snapshot)
    output["opinion_uuid"] = save_opinion(debate_uuid, result)

    return output
