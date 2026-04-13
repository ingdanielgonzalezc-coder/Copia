"""
Smoke test: run a complete debate end-to-end on a single ticker.

Usage:
    uv run python -m scripts.run_debate NVDA
    uv run python -m scripts.run_debate AAPL --phase EOD
    uv run python -m scripts.run_debate JPM --shares 100 --cost-basis 195
    uv run python -m scripts.run_debate NVDA --no-save     # don't persist

By default, every debate is persisted to Supabase. Use --no-save to skip.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from src.compute_snapshot import compute_snapshot
from src.db import persist_debate_complete, save_macro_snapshot
from src.debate_engine import run_debate
from src.macro_context import fetch_macro_snapshot
from src.rules_engine import Phase, Position, PortfolioState


def _print_separator(label: str = "") -> None:
    if label:
        print(f"\n{'=' * 70}")
        print(f"  {label}")
        print(f"{'=' * 70}")
    else:
        print("=" * 70)


def _print_agent_response(name: str, response: dict, metrics: dict) -> None:
    _print_separator(f"{name}  ({metrics['model']})")
    print(f"Latency: {metrics['latency_ms']} ms  |  "
          f"Tokens: {metrics['tokens_in']} in / {metrics['tokens_out']} out  |  "
          f"Cost: ${metrics['cost_usd']:.5f}")
    if not metrics["parse_ok"]:
        print("⚠️  WARNING: JSON parsing failed for this agent")
    print()
    print(json.dumps(response, indent=2, ensure_ascii=False))


async def main_async(args: argparse.Namespace) -> None:
    print(f"Running debate for {args.ticker} (phase={args.phase})...")
    print()

    # Fetch macro snapshot once and reuse it
    print("📈 Fetching macro context...")
    macro = fetch_macro_snapshot()
    print(f"   Regime: {macro.regime}")
    print(f"   {macro.description}")
    print()

    # Persist macro snapshot for historical tracking (best-effort)
    if args.save:
        save_macro_snapshot(macro)

    # Build full snapshot
    print("📊 Computing snapshot...")
    snapshot = compute_snapshot(
        args.ticker,
        macro=macro,
        cost_basis=args.cost_basis,
        shares=args.shares,
    )
    print(f"   Price: ${snapshot['price']} ({snapshot['change_pct']:+.2f}%)")
    print(f"   Sector: {snapshot['fundamentals']['sector']}")
    print(f"   Signals: {snapshot['signals_summary']}")

    pm = snapshot.get("position_metrics")
    if pm:
        pnl_marker = "🟢" if pm["unrealized_pnl_pct"] >= 0 else "🔴"
        print(f"   {pnl_marker} Position: {pm['shares']} shares @ ${pm['cost_basis']} cost basis")
        print(f"      Unrealized P&L: ${pm['unrealized_pnl_usd']:+,.2f} ({pm['unrealized_pnl_pct']:+.2f}%)")
    print()

    # Build position and portfolio state
    position = Position(
        ticker=args.ticker,
        shares=args.shares,
        cost_basis=args.cost_basis,
        allocation_pct=args.allocation_pct,
        sector=snapshot["fundamentals"].get("sector"),
    )
    portfolio = PortfolioState(
        total_open_positions=args.open_positions,
        sector_allocations={},
    )

    # Run debate
    print("🤖 Running Bull + Bear in parallel, then Judge...")
    print()
    result = await run_debate(
        ticker=args.ticker,
        phase=Phase(args.phase),
        snapshot=snapshot,
        position=position,
        portfolio=portfolio,
    )

    # Print results
    if result.skip_debate:
        _print_separator("DEBATE SKIPPED")
        print(f"Reason: {result.skip_reason}")
        print(f"Auto-verdict: {result.final_verdict['verdict']}")
    else:
        print(f"Allowed actions: {result.allowed_actions}")
        print(f"Escalation: {'YES (Opus + extended thinking)' if result.judge_escalated else 'NO'}")
        print()

        _print_agent_response("🐂 BULL", result.bull_response, result.bull_metrics)
        _print_agent_response("🐻 BEAR", result.bear_response, result.bear_metrics)
        _print_agent_response("⚖️  JUDGE", result.judge_response, result.judge_metrics)

        _print_separator("FINAL VERDICT")
        print(json.dumps(result.final_verdict, indent=2, ensure_ascii=False))

        if result.was_downgraded:
            print()
            print("⚠️  Verdict was downgraded by post-validator")
            print(json.dumps(result.rule_violations, indent=2))

    _print_separator("SUMMARY")
    print(f"Debate ID:      {result.debate_id}")
    print(f"Total cost:     ${result.total_cost_usd:.4f}")
    print(f"Total latency:  {result.total_latency_ms} ms ({result.total_latency_ms/1000:.1f}s)")
    print(f"Final verdict:  {result.final_verdict.get('verdict')}  "
          f"(confidence {result.final_verdict.get('confidence')})")

    # Persistence
    if args.save:
        _print_separator("PERSISTENCE")
        print("Saving to Supabase...")
        persist_result = persist_debate_complete(
            result=result,
            snapshot=snapshot,
            position=position,
            trigger_type="manual_smoke_test",
        )
        if persist_result["debate_uuid"]:
            print(f"   ✅ debate            uuid: {persist_result['debate_uuid']}")
            if persist_result["paper_trade_uuid"]:
                print(f"   ✅ paper_trade       uuid: {persist_result['paper_trade_uuid']}")
            if persist_result["opinion_uuid"]:
                print(f"   ✅ opinion           uuid: {persist_result['opinion_uuid']}")
            if persist_result["violations_saved"]:
                print(f"   ✅ rule_violations   count: {persist_result['violations_saved']}")
        else:
            print("   ❌ Persistence failed:")
            for err in persist_result["errors"]:
                print(f"      {err}")
    else:
        print()
        print("(--no-save: skipped Supabase persistence)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a debate on a single ticker")
    parser.add_argument("ticker", help="Ticker symbol (e.g. NVDA)")
    parser.add_argument(
        "--phase",
        choices=["INITIAL", "INTRADAY", "EOD", "DISCOVERY"],
        default="INITIAL",
        help="Debate phase (default: INITIAL)",
    )
    parser.add_argument(
        "--shares", type=float, default=0.0, help="Current shares held (default: 0)"
    )
    parser.add_argument(
        "--cost-basis", type=float, default=None, help="Cost basis per share"
    )
    parser.add_argument(
        "--allocation-pct",
        type=float,
        default=0.0,
        help="Current allocation as % of portfolio (default: 0)",
    )
    parser.add_argument(
        "--open-positions",
        type=int,
        default=0,
        help="Total open positions in portfolio (default: 0)",
    )
    parser.add_argument(
        "--no-save",
        dest="save",
        action="store_false",
        help="Skip persistence to Supabase",
    )
    parser.set_defaults(save=True)

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
