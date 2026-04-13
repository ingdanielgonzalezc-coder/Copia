"""
Outcome tracker — compute post-hoc outcomes for past debates.

For each paper_trade with missing outcomes, fetch historical price data
from Polygon and calculate:
  - outcome_1d_pct, outcome_1w_pct, outcome_1m_pct
  - was_correct_1d, was_correct_1w, was_correct_1m
  - was_correct_attributed (based on time_horizon declared in debate)

Re-runnable and idempotent: only processes rows where (a) an outcome is
still null AND (b) enough wall-clock time has passed for that horizon to
be computable.

The was_correct logic for HOLD verdicts uses an ATR-based noise floor
(per v3.1.1 Patch C): a HOLD is "correct" if the price moved less than
1.5× ATR (with a 2% floor) OR if the position was held and moved up.

Usage:
    uv run python -m scripts.compute_outcomes
    uv run python -m scripts.compute_outcomes --dry-run
    uv run python -m scripts.compute_outcomes --debate-id <uuid>
    uv run python -m scripts.compute_outcomes --stats
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

from src.db import get_client

load_dotenv()

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")
POLYGON_BASE_URL = "https://api.polygon.io"

# Verdict categories
BULLISH_VERDICTS = {"BUY", "BUY_NEW", "ADD"}
BEARISH_VERDICTS = {"SELL", "TRIM", "AVOID_NEW"}
NEUTRAL_VERDICTS = {"HOLD"}
ABSTAIN_VERDICTS = {"ABSTAIN"}

# Horizon → field name mapping
HORIZON_FIELD_MAP = {
    "short-term": "outcome_1d_pct",
    "medium-term": "outcome_1w_pct",
    "long-term": "outcome_1m_pct",
}
HORIZON_CORRECT_MAP = {
    "short-term": "was_correct_1d",
    "medium-term": "was_correct_1w",
    "long-term": "was_correct_1m",
}


# =============================================================================
# Polygon historical price fetching
# =============================================================================

def fetch_polygon_daily_bars(ticker: str, from_date: str, to_date: str) -> list[dict]:
    """Fetch daily OHLCV bars for a ticker between two dates (inclusive)."""
    if not POLYGON_API_KEY:
        raise RuntimeError("POLYGON_API_KEY not set")

    url = f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 5000,
        "apiKey": POLYGON_API_KEY,
    }

    with httpx.Client(timeout=15.0) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()

    if data.get("status") not in ("OK", "DELAYED"):
        return []

    return data.get("results", []) or []


def find_close_at_or_after(bars: list[dict], target_date: datetime) -> float | None:
    """Find the close of the first trading day on or after target_date."""
    target_ms = int(target_date.timestamp() * 1000)
    for bar in bars:
        if bar["t"] >= target_ms:
            return float(bar["c"])
    return None


def compute_outcomes(
    ticker: str,
    debate_time: datetime,
    price_at_decision: float,
) -> dict[str, float | None]:
    """Compute outcome_1d, outcome_1w, outcome_1m as percentage changes."""
    # Normalize to start of debate day so "next bar after target" works
    # against Polygon's daily bar timestamps (which are at 00:00 UTC).
    debate_day = debate_time.replace(hour=0, minute=0, second=0, microsecond=0)

    from_date = debate_day.strftime("%Y-%m-%d")
    to_date = (debate_day + timedelta(days=40)).strftime("%Y-%m-%d")

    try:
        bars = fetch_polygon_daily_bars(ticker, from_date, to_date)
    except Exception as e:
        print(f"   ⚠️  Failed to fetch bars for {ticker}: {e}")
        return {"outcome_1d_pct": None, "outcome_1w_pct": None, "outcome_1m_pct": None}

    target_1d = debate_day + timedelta(days=1)
    target_1w = debate_day + timedelta(days=7)
    target_1m = debate_day + timedelta(days=30)

    price_1d = find_close_at_or_after(bars, target_1d)
    price_1w = find_close_at_or_after(bars, target_1w)
    price_1m = find_close_at_or_after(bars, target_1m)

    def pct(target: float | None) -> float | None:
        if target is None or price_at_decision <= 0:
            return None
        return round((target - price_at_decision) / price_at_decision * 100, 4)

    return {
        "outcome_1d_pct": pct(price_1d),
        "outcome_1w_pct": pct(price_1w),
        "outcome_1m_pct": pct(price_1m),
    }


# =============================================================================
# was_correct logic (per v3.1.1 Patch C)
# =============================================================================

def was_correct(
    verdict: str,
    has_position: bool,
    snapshot: dict[str, Any],
    outcome_pct: float | None,
) -> bool | None:
    """
    Determine if a verdict was correct given the actual outcome.

    Returns None when the outcome cannot be evaluated (no price data, or
    ABSTAIN verdict).
    """
    if outcome_pct is None:
        return None

    if verdict in BULLISH_VERDICTS:
        return outcome_pct > 0

    if verdict in BEARISH_VERDICTS:
        return outcome_pct < 0

    if verdict in ABSTAIN_VERDICTS:
        return None  # not counted in calibration

    if verdict in NEUTRAL_VERDICTS:
        # ATR-based noise floor (Patch C)
        indicators = snapshot.get("indicators") or {}
        atr_14 = indicators.get("atr_14") or 0
        price = snapshot.get("price") or 1
        atr_pct = (atr_14 / price * 100) if price > 0 else 0
        noise_floor = max(2.0, atr_pct * 1.5)

        if abs(outcome_pct) < noise_floor:
            return True  # movement within expected noise
        if has_position and outcome_pct > 0:
            return True  # held and went up
        return False

    return None


def determine_attributed_horizon(
    bull_response: dict | None,
    bear_response: dict | None,
) -> str:
    """
    Choose the horizon for attributed outcome.

    Convention: prefer the SHORTER horizon when Bull and Bear declare
    different ones. This is more conservative for evaluation purposes.
    """
    horizons = []
    if bull_response and bull_response.get("time_horizon"):
        horizons.append(bull_response["time_horizon"])
    if bear_response and bear_response.get("time_horizon"):
        horizons.append(bear_response["time_horizon"])

    if not horizons:
        return "medium-term"

    horizon_order = ["short-term", "medium-term", "long-term"]
    for h in horizon_order:
        if h in horizons:
            return h
    return "medium-term"


# =============================================================================
# Database queries
# =============================================================================

def needs_processing(row: dict) -> bool:
    """Check if a paper_trade row has new outcome data available to compute."""
    timestamp = row.get("timestamp")
    if not timestamp:
        return False

    debate_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    # Use calendar-day age, not seconds — a debate from yesterday is age=1
    today = datetime.now(timezone.utc).date()
    age_days = (today - debate_time.date()).days

    if row.get("outcome_1d_pct") is None and age_days >= 1:
        return True
    if row.get("outcome_1w_pct") is None and age_days >= 7:
        return True
    if row.get("outcome_1m_pct") is None and age_days >= 30:
        return True
    return False


def fetch_candidate_paper_trades(limit: int = 200) -> list[dict]:
    """Fetch paper_trade rows that may need outcome updates."""
    client = get_client()
    response = (
        client.table("paper_trades")
        .select("*")
        .order("timestamp", desc=False)
        .limit(limit)
        .execute()
    )
    rows = response.data or []
    return [r for r in rows if needs_processing(r)]


def fetch_debate(debate_id: str) -> dict | None:
    """Fetch a debate row by id."""
    client = get_client()
    response = client.table("debates").select("*").eq("id", debate_id).limit(1).execute()
    return response.data[0] if response.data else None


def update_paper_trade(paper_trade_id: str, updates: dict) -> bool:
    """Update a paper_trade row with computed outcomes."""
    try:
        client = get_client()
        client.table("paper_trades").update(updates).eq("id", paper_trade_id).execute()
        return True
    except Exception as e:
        print(f"   ⚠️  Failed to update paper_trade {paper_trade_id}: {e}")
        return False


# =============================================================================
# Main processing
# =============================================================================

def process_paper_trade(row: dict, dry_run: bool = False) -> dict:
    """Process a single paper_trade: compute outcomes and update."""
    debate_id = row.get("debate_id")
    if not debate_id:
        return {"status": "skipped", "reason": "no_debate_id"}

    debate = fetch_debate(debate_id)
    if not debate:
        return {"status": "skipped", "reason": "debate_not_found"}

    ticker = row["ticker"]
    timestamp = row["timestamp"]
    debate_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    price_at_decision = row.get("price_at_decision")

    if price_at_decision is None or float(price_at_decision) <= 0:
        return {"status": "skipped", "reason": "invalid_price_at_decision"}

    outcomes = compute_outcomes(ticker, debate_time, float(price_at_decision))

    snapshot = debate.get("snapshot") or {}
    verdict = debate.get("verdict") or "HOLD"
    position_at_debate = debate.get("position_at_debate") or {}
    has_position = (position_at_debate.get("shares") or 0) > 0

    correct_1d = was_correct(verdict, has_position, snapshot, outcomes["outcome_1d_pct"])
    correct_1w = was_correct(verdict, has_position, snapshot, outcomes["outcome_1w_pct"])
    correct_1m = was_correct(verdict, has_position, snapshot, outcomes["outcome_1m_pct"])

    bull_resp = debate.get("bull_response")
    bear_resp = debate.get("bear_response")
    attributed_horizon = determine_attributed_horizon(bull_resp, bear_resp)
    correct_map = {
        "short-term": correct_1d,
        "medium-term": correct_1w,
        "long-term": correct_1m,
    }
    was_correct_attributed = correct_map.get(attributed_horizon)

    updates = {
        "outcome_1d_pct": outcomes["outcome_1d_pct"],
        "outcome_1w_pct": outcomes["outcome_1w_pct"],
        "outcome_1m_pct": outcomes["outcome_1m_pct"],
        "was_correct_1d": correct_1d,
        "was_correct_1w": correct_1w,
        "was_correct_1m": correct_1m,
        "was_correct_attributed": was_correct_attributed,
        "outcome_computed_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        return {
            "status": "dry_run",
            "ticker": ticker,
            "verdict": verdict,
            "attributed_horizon": attributed_horizon,
            **updates,
        }

    success = update_paper_trade(row["id"], updates)
    return {
        "status": "updated" if success else "failed",
        "ticker": ticker,
        "verdict": verdict,
        "attributed_horizon": attributed_horizon,
        **updates,
    }


# =============================================================================
# Stats summary
# =============================================================================

def show_stats() -> None:
    """Print aggregate hit-rate stats from paper_trades."""
    client = get_client()
    response = client.table("paper_trades").select("*").execute()
    rows = response.data or []

    if not rows:
        print("No paper_trades in database.")
        return

    print("=" * 70)
    print("PAPER TRADE STATS")
    print("=" * 70)
    print(f"Total paper_trades: {len(rows)}")
    print()

    def hit_rate(field: str) -> tuple[int, int]:
        evaluated = [r for r in rows if r.get(field) is not None]
        correct = [r for r in evaluated if r[field] is True]
        return len(correct), len(evaluated)

    for label, field in [
        ("1-day", "was_correct_1d"),
        ("1-week", "was_correct_1w"),
        ("1-month", "was_correct_1m"),
        ("Attributed", "was_correct_attributed"),
    ]:
        c, e = hit_rate(field)
        rate = (c / e * 100) if e > 0 else 0
        print(f"  {label:12} hit rate: {c}/{e}  ({rate:.1f}%)")

    # By verdict
    print()
    print("By verdict:")
    verdicts: dict[str, list[bool | None]] = {}
    for r in rows:
        v = r.get("simulated_action") or "?"
        verdicts.setdefault(v, []).append(r.get("was_correct_attributed"))

    for v in sorted(verdicts.keys()):
        results = verdicts[v]
        evaluated = [x for x in results if x is not None]
        correct = sum(1 for x in evaluated if x)
        rate = (correct / len(evaluated) * 100) if evaluated else 0
        print(f"  {v:12} {correct}/{len(evaluated)}  ({rate:.1f}%)  (total: {len(results)})")

    # By regime
    print()
    print("By regime:")
    regimes: dict[str, list[bool | None]] = {}
    for r in rows:
        rg = r.get("macro_regime") or "?"
        regimes.setdefault(rg, []).append(r.get("was_correct_attributed"))

    for rg in sorted(regimes.keys()):
        results = regimes[rg]
        evaluated = [x for x in results if x is not None]
        correct = sum(1 for x in evaluated if x)
        rate = (correct / len(evaluated) * 100) if evaluated else 0
        print(f"  {rg:18} {correct}/{len(evaluated)}  ({rate:.1f}%)  (total: {len(results)})")


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Compute outcomes for past debates")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB")
    parser.add_argument("--limit", type=int, default=200, help="Max paper_trades to scan")
    parser.add_argument("--debate-id", type=str, default=None, help="Process only this debate")
    parser.add_argument("--stats", action="store_true", help="Show aggregate stats and exit")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    print("=" * 70)
    print("OUTCOME TRACKER")
    print("=" * 70)
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    if args.debate_id:
        client = get_client()
        response = (
            client.table("paper_trades")
            .select("*")
            .eq("debate_id", args.debate_id)
            .execute()
        )
        rows = response.data or []
    else:
        rows = fetch_candidate_paper_trades(limit=args.limit)

    if not rows:
        print("✅ No paper_trades need outcome computation right now.")
        print("   (All evaluable rows already have outcomes, or none are old enough.)")
        return

    print(f"Found {len(rows)} paper_trade(s) to process")
    print()

    counts = {"updated": 0, "skipped": 0, "failed": 0, "dry_run": 0}

    for i, row in enumerate(rows, start=1):
        ticker = row.get("ticker", "?")
        timestamp = (row.get("timestamp") or "")[:19]
        print(f"[{i}/{len(rows)}] {ticker} @ {timestamp}")

        result = process_paper_trade(row, dry_run=args.dry_run)
        status = result["status"]
        counts[status] = counts.get(status, 0) + 1

        if status in ("updated", "dry_run"):
            print(f"   verdict: {result['verdict']}  attributed horizon: {result['attributed_horizon']}")

            def fmt_pct(pct: float | None) -> str:
                if pct is None:
                    return "  N/A "
                return f"{pct:+6.2f}%"

            def fmt_correct(c: bool | None) -> str:
                if c is None:
                    return "?"
                return "✓" if c else "✗"

            print(
                f"   1d: {fmt_pct(result.get('outcome_1d_pct'))} {fmt_correct(result.get('was_correct_1d'))}   "
                f"1w: {fmt_pct(result.get('outcome_1w_pct'))} {fmt_correct(result.get('was_correct_1w'))}   "
                f"1m: {fmt_pct(result.get('outcome_1m_pct'))} {fmt_correct(result.get('was_correct_1m'))}"
            )
            print(f"   attributed: {fmt_correct(result.get('was_correct_attributed'))}")
        else:
            print(f"   skipped: {result.get('reason', 'unknown')}")
        print()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for status, count in counts.items():
        if count > 0:
            print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
