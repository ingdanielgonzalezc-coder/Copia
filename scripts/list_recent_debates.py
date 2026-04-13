"""
Verify persistence: list the most recent debates from Supabase.

Usage:
    uv run python -m scripts.list_recent_debates
    uv run python -m scripts.list_recent_debates --limit 20
    uv run python -m scripts.list_recent_debates --ticker NVDA
"""

from __future__ import annotations

import argparse
import json

from src.db import get_client


def main() -> None:
    parser = argparse.ArgumentParser(description="List recent debates from Supabase")
    parser.add_argument("--limit", type=int, default=10, help="Max rows to fetch (default 10)")
    parser.add_argument("--ticker", type=str, default=None, help="Filter by ticker")
    parser.add_argument("--full", action="store_true", help="Print full row JSON")
    args = parser.parse_args()

    client = get_client()
    query = client.table("debates").select(
        "id, timestamp, phase, ticker, regime, verdict, confidence, "
        "judge_escalated, total_cost_usd, total_latency_ms, rules_violated"
    ).order("timestamp", desc=True).limit(args.limit)

    if args.ticker:
        query = query.eq("ticker", args.ticker.upper())

    response = query.execute()
    rows = response.data or []

    if not rows:
        print("No debates found.")
        return

    print(f"Found {len(rows)} debate(s):")
    print()
    print(f"{'Timestamp':<22}  {'Phase':<10} {'Ticker':<6} {'Regime':<16} "
          f"{'Verdict':<10} {'Conf':<5} {'Esc':<4} {'Cost':<8} {'Latency':<8}")
    print("-" * 100)

    for row in rows:
        timestamp = row["timestamp"][:19].replace("T", " ")
        cost = f"${row['total_cost_usd']:.4f}" if row.get("total_cost_usd") else "$0.0000"
        latency = f"{row.get('total_latency_ms', 0)/1000:.1f}s"
        esc = "Yes" if row.get("judge_escalated") else "No"
        violation_marker = " ⚠️" if row.get("rules_violated") else ""

        print(
            f"{timestamp}  "
            f"{row['phase']:<10} "
            f"{row['ticker']:<6} "
            f"{(row.get('regime') or 'N/A'):<16} "
            f"{(row.get('verdict') or 'N/A'):<10} "
            f"{str(row.get('confidence', '')):<5} "
            f"{esc:<4} "
            f"{cost:<8} "
            f"{latency:<8}{violation_marker}"
        )

    if args.full:
        print()
        print("=" * 100)
        print("FULL ROWS")
        print("=" * 100)
        print(json.dumps(rows, indent=2, default=str))

    # Aggregate stats
    print()
    print("-" * 100)
    total_cost = sum(r.get("total_cost_usd") or 0 for r in rows)
    avg_latency = sum(r.get("total_latency_ms") or 0 for r in rows) / len(rows)
    escalations = sum(1 for r in rows if r.get("judge_escalated"))
    violations = sum(1 for r in rows if r.get("rules_violated"))
    print(
        f"Totals over {len(rows)} debates: "
        f"cost ${total_cost:.4f}, "
        f"avg latency {avg_latency/1000:.1f}s, "
        f"escalations {escalations}, "
        f"rule violations {violations}"
    )


if __name__ == "__main__":
    main()
