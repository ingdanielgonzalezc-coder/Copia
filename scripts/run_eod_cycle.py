"""
EOD cycle — end-of-day review + new opportunity discovery.

Three phases:
  Phase A: Review each open position (Opus + web search + today's activity)
  Phase B: If cash available → check watchlist flagged items → INITIAL debate
  Phase C: If watchlist yields nothing AND cash >10% → mini-discovery scan

Usage:
    uv run python -m scripts.run_eod_cycle
    uv run python -m scripts.run_eod_cycle --dry-run
    uv run python -m scripts.run_eod_cycle --cost-cap 5.0
    uv run python -m scripts.run_eod_cycle --ticker NVDA            # single position
    uv run python -m scripts.run_eod_cycle --skip-discovery          # phase A only
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from src.compute_snapshot import compute_snapshot
from src.db import get_client, persist_debate_complete, save_macro_snapshot
from src.debate_engine import run_debate
from src.discovery import run_screener
from src.macro_context import MacroSnapshot, fetch_macro_snapshot
from src.portfolio import (
    Portfolio,
    clear_old_watchlist,
    get_watchlist_tickers,
    load_portfolio,
    promote_from_watchlist,
    save_candidates,
    save_portfolio_state,
)
from src.rules_engine import Phase, Position, PortfolioState

DEFAULT_COST_CAP = 3.0
STALE_DAYS = 14


# =============================================================================
# Helpers
# =============================================================================

def _load_positions() -> list[dict[str, Any]]:
    """Load all positions from Supabase."""
    try:
        client = get_client()
        response = client.table("positions").select("*").execute()
        return response.data or []
    except Exception as e:
        print(f"⚠️  Failed to load positions: {e}")
        return []


def _was_reviewed_today(row: dict[str, Any]) -> bool:
    """Check if position already had an EOD debate today."""
    last = row.get("last_debate_at")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        return last_dt >= today_start
    except Exception:
        return False


def _days_since_review(row: dict[str, Any]) -> int:
    """Days since last debate on this position."""
    last = row.get("last_debate_at")
    if not last:
        return 999
    try:
        last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last_dt).days
    except Exception:
        return 999


def _fetch_today_activity(ticker: str) -> str:
    """Build summary of today's intraday activity for a ticker."""
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    sections = []

    try:
        client = get_client()

        # Today's news
        news_resp = (
            client.table("news_dedup")
            .select("title, relevance_score, impact_direction, published_utc")
            .contains("tickers", [ticker])
            .gte("published_utc", today_start.isoformat())
            .order("relevance_score", desc=True)
            .limit(10)
            .execute()
        )
        if news_resp.data:
            lines = []
            for n in news_resp.data:
                lines.append(
                    f"  • [{n.get('impact_direction', '?')}] "
                    f"score {n.get('relevance_score', '?')}: "
                    f"{n.get('title', 'N/A')[:100]}"
                )
            sections.append("NEWS TODAY:\n" + "\n".join(lines))

        # Today's debates
        debate_resp = (
            client.table("debates")
            .select("phase, verdict, confidence, reasoning, timestamp")
            .eq("ticker", ticker)
            .gte("timestamp", today_start.isoformat())
            .order("timestamp", desc=True)
            .limit(5)
            .execute()
        )
        if debate_resp.data:
            lines = []
            for d in debate_resp.data:
                lines.append(
                    f"  • [{d.get('phase')}] {d.get('verdict')} "
                    f"conf {d.get('confidence')} — "
                    f"{(d.get('reasoning') or '')[:120]}"
                )
            sections.append("INTRADAY DEBATES:\n" + "\n".join(lines))
    except Exception:
        pass

    if not sections:
        return "No significant intraday activity."
    return "\n\n".join(sections)


def _get_watchlist_with_scores() -> list[dict[str, Any]]:
    """Get watchlist candidates with any news scores from today."""
    try:
        client = get_client()
        response = (
            client.table("discovery_candidates")
            .select("ticker, setup_type, screener_score, snapshot_summary, scan_timestamp")
            .eq("user_decision", "watchlist")
            .order("screener_score", desc=True)
            .execute()
        )
        candidates = response.data or []
    except Exception as e:
        print(f"⚠️  Failed to load watchlist candidates: {e}")
        return []

    # Enrich with today's news count/max score per ticker
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    enriched = []
    for c in candidates:
        ticker = c["ticker"]
        news_score = 0
        news_count = 0
        try:
            client = get_client()
            news_resp = (
                client.table("news_dedup")
                .select("relevance_score")
                .contains("tickers", [ticker])
                .gte("published_utc", today_start.isoformat())
                .execute()
            )
            if news_resp.data:
                news_count = len(news_resp.data)
                news_score = max(
                    int(n.get("relevance_score", 0)) for n in news_resp.data
                )
        except Exception:
            pass

        enriched.append({
            **c,
            "today_news_count": news_count,
            "today_max_news_score": news_score,
            # Composite: favor flagged items (high news) + high screener score
            "priority_score": news_score * 2 + (c.get("screener_score") or 0),
        })

    # Sort by priority (flagged items first)
    enriched.sort(key=lambda x: -x["priority_score"])
    return enriched


def _update_last_debate(ticker: str) -> None:
    """Mark position as reviewed."""
    try:
        client = get_client()
        client.table("positions").update({
            "last_debate_at": datetime.now(timezone.utc).isoformat(),
        }).eq("ticker", ticker).execute()
    except Exception:
        pass


# =============================================================================
# Phase A: Review open positions
# =============================================================================

async def phase_a_review_positions(
    positions: list[dict[str, Any]],
    macro: MacroSnapshot,
    portfolio_state: PortfolioState,
    cost_cap: float,
    single_ticker: str | None = None,
    dry_run: bool = False,
) -> tuple[float, int]:
    """
    Review each open position with Opus + web search.

    Returns (total_cost, debates_run).
    """
    total_cost = 0.0
    debates_run = 0

    # Sort: stale first (not reviewed in 14+ days), then by allocation
    positions.sort(key=lambda r: (
        0 if _days_since_review(r) >= STALE_DAYS else 1,
        -float(r.get("current_alloc_pct") or 0),
    ))

    for row in positions:
        ticker = row["ticker"]
        shares = float(row.get("shares") or 0)

        if shares <= 0:
            continue
        if single_ticker and ticker != single_ticker:
            continue
        if _was_reviewed_today(row) and not single_ticker:
            print(f"   ⏭️  {ticker}: already reviewed today")
            continue
        if total_cost >= cost_cap:
            print(f"   ⏭️  Cost cap reached (${total_cost:.2f})")
            break

        stale = _days_since_review(row) >= STALE_DAYS
        print(f"\n{'─' * 60}")
        print(f"📊 {ticker}  |  {shares} shares  |  "
              f"alloc {row.get('current_alloc_pct', '?')}%"
              f"{'  ⚠️ STALE' if stale else ''}")
        print(f"{'─' * 60}")

        # Build snapshot
        try:
            snapshot = compute_snapshot(
                ticker, macro=macro,
                cost_basis=row.get("cost_basis"),
                shares=shares,
            )
        except Exception as e:
            print(f"   ❌ Snapshot failed: {e}")
            continue

        position = Position(
            ticker=ticker,
            shares=shares,
            cost_basis=row.get("cost_basis"),
            allocation_pct=float(row.get("current_alloc_pct") or 0),
            sector=row.get("sector"),
        )

        # Fetch today's intraday activity
        today_activity = _fetch_today_activity(ticker)

        if dry_run:
            print(f"   ⚠️  DRY RUN — would debate {ticker}")
            continue

        try:
            result = await run_debate(
                ticker=ticker,
                phase=Phase.EOD,
                snapshot=snapshot,
                position=position,
                portfolio=portfolio_state,
                today_activity=today_activity,
            )
        except Exception as e:
            print(f"   ❌ Debate failed: {e}")
            continue

        fv = result.final_verdict or {}
        total_cost += result.total_cost_usd
        debates_run += 1

        # Print verbose results
        bull = result.bull_response or {}
        bear = result.bear_response or {}
        print(f"\n   🐂 BULL: {bull.get('suggested_action', '?')} "
              f"(conf {bull.get('confidence', '?')}) — "
              f"{bull.get('thesis', '')[:200]}")
        print(f"   🐻 BEAR: {bear.get('suggested_action', '?')} "
              f"(conf {bear.get('confidence', '?')}) — "
              f"{bear.get('thesis', '')[:200]}")
        print(f"\n   ⚖️  JUDGE VERDICT: {fv.get('verdict')} "
              f"(confidence {fv.get('confidence')})")
        print(f"   Reasoning: {fv.get('reasoning', '')[:300]}")

        if fv.get("consensus_analysis"):
            print(f"   Consensus: {fv['consensus_analysis'][:200]}")
        if fv.get("disagreement_areas"):
            print(f"   Disagreement: {fv['disagreement_areas'][:200]}")
        if fv.get("suggested_sizing"):
            print(f"   Sizing: {fv['suggested_sizing']}")
        if fv.get("stop_loss"):
            print(f"   Stop loss: {fv['stop_loss']}")
        if fv.get("catalysts_to_watch"):
            print(f"   Catalysts: {fv['catalysts_to_watch'][:200]}")
        if fv.get("follow_up_action"):
            print(f"   Follow-up: {fv['follow_up_action']}")
        if fv.get("telegram_alert"):
            print(f"\n   📱 ALERT: {fv['telegram_alert']}")

        print(f"\n   💰 Cost: ${result.total_cost_usd:.4f}  |  "
              f"Latency: {result.total_latency_ms/1000:.1f}s")

        # Persist
        persist_debate_complete(
            result=result,
            snapshot=snapshot,
            position=position,
            trigger_type="eod_review",
        )
        _update_last_debate(ticker)

    return total_cost, debates_run


# =============================================================================
# Phase B: Evaluate watchlist candidates
# =============================================================================

async def phase_b_watchlist(
    macro: MacroSnapshot,
    portfolio: Portfolio,
    portfolio_state: PortfolioState,
    cost_budget: float,
    max_candidates: int = 3,
    dry_run: bool = False,
) -> tuple[float, int, int]:
    """
    Evaluate top watchlist candidates via INITIAL debate.

    Prioritizes items flagged by intraday news, then by screener score.

    Returns (cost_spent, debates_run, positions_opened).
    """
    if not portfolio.can_open_new_position:
        print("   ⏭️  No room for new positions (max reached or insufficient cash)")
        return 0.0, 0, 0

    candidates = _get_watchlist_with_scores()
    if not candidates:
        print("   📭 Watchlist is empty")
        return 0.0, 0, 0

    # Filter out tickers already in portfolio
    held = {h.ticker for h in portfolio.holdings}
    candidates = [c for c in candidates if c["ticker"] not in held]

    if not candidates:
        print("   📭 All watchlist candidates already in portfolio")
        return 0.0, 0, 0

    total_cost = 0.0
    debates_run = 0
    positions_opened = 0

    for c in candidates[:max_candidates]:
        ticker = c["ticker"]
        if total_cost >= cost_budget:
            break
        if not portfolio.can_open_new_position:
            break

        flagged = c["today_news_count"] > 0
        print(f"\n   {'🔥' if flagged else '📋'} {ticker}  "
              f"screener={c.get('screener_score', '?')}  "
              f"news_today={c['today_news_count']}  "
              f"max_news_score={c['today_max_news_score']}")

        if dry_run:
            print(f"      ⚠️  DRY RUN — would debate {ticker}")
            continue

        try:
            snapshot = compute_snapshot(ticker, macro=macro)
        except Exception as e:
            print(f"      ❌ Snapshot failed: {e}")
            continue

        position = Position(ticker=ticker, shares=0, cost_basis=None, allocation_pct=0)

        try:
            result = await run_debate(
                ticker=ticker,
                phase=Phase.INITIAL,
                snapshot=snapshot,
                position=position,
                portfolio=portfolio_state,
            )
        except Exception as e:
            print(f"      ❌ Debate failed: {e}")
            continue

        fv = result.final_verdict or {}
        verdict = fv.get("verdict", "UNKNOWN")
        confidence = fv.get("confidence", 0)
        total_cost += result.total_cost_usd
        debates_run += 1

        print(f"      → {verdict} (conf {confidence})  "
              f"cost ${result.total_cost_usd:.4f}")

        if verdict == "BUY_NEW" and confidence >= 50:
            positions_opened += 1
            print(f"      ✅ APPROVED — promote from watchlist")
            promote_from_watchlist(ticker, result.debate_id or "", verdict)
        else:
            print(f"      ⏭️  Not approved")
            promote_from_watchlist(ticker, result.debate_id or "", verdict)

        persist_debate_complete(
            result=result,
            snapshot=snapshot,
            position=position,
            trigger_type="watchlist_eod",
        )

    return total_cost, debates_run, positions_opened


# =============================================================================
# Phase C: Mini-discovery scan
# =============================================================================

async def phase_c_discovery(
    macro: MacroSnapshot,
    portfolio: Portfolio,
    portfolio_state: PortfolioState,
    cost_budget: float,
    dry_run: bool = False,
) -> tuple[float, int]:
    """
    Run a lightweight mini-screener when watchlist is exhausted.

    Screens S&P 500, saves new watchlist, and optionally debates
    the single best candidate if budget allows.

    Returns (cost_spent, new_watchlist_count).
    """
    held = {h.ticker for h in portfolio.holdings}
    existing_watchlist = set(get_watchlist_tickers())

    print("   🔍 Running mini-discovery scan...")
    invest, watchlist = run_screener(
        top_n=3,
        watchlist_n=10,
        existing_tickers=held | existing_watchlist,
    )

    if not invest and not watchlist:
        print("   📭 No new candidates found")
        return 0.0, 0

    # Save new watchlist entries
    all_new = invest + watchlist
    saved = save_candidates(all_new, status="watchlist")
    print(f"   💾 Saved {saved} new watchlist candidates")

    for c in all_new[:5]:
        print(f"      {c['ticker']:6}  score={c['score']:5.1f}  {c['setup_type']}")

    # If budget allows, debate the top candidate
    if invest and cost_budget >= 0.10 and not dry_run and portfolio.can_open_new_position:
        top = invest[0]
        ticker = top["ticker"]
        print(f"\n   🎯 Debating top discovery: {ticker} (score {top['score']})")

        try:
            snapshot = compute_snapshot(ticker, macro=macro)
            position = Position(ticker=ticker, shares=0, cost_basis=None, allocation_pct=0)
            result = await run_debate(
                ticker=ticker, phase=Phase.INITIAL,
                snapshot=snapshot, position=position,
                portfolio=portfolio_state,
            )
            fv = result.final_verdict or {}
            verdict = fv.get("verdict", "UNKNOWN")
            print(f"      → {verdict} (conf {fv.get('confidence')})")

            persist_debate_complete(
                result=result, snapshot=snapshot, position=position,
                trigger_type="discovery_eod",
            )
            if verdict == "BUY_NEW":
                promote_from_watchlist(ticker, result.debate_id or "", verdict)

            return result.total_cost_usd, saved
        except Exception as e:
            print(f"      ❌ Discovery debate failed: {e}")

    return 0.0, saved


# =============================================================================
# Main orchestrator
# =============================================================================

async def run_eod_cycle(
    cost_cap: float = DEFAULT_COST_CAP,
    single_ticker: str | None = None,
    skip_discovery: bool = False,
    dry_run: bool = False,
) -> None:
    print("=" * 70)
    print("EOD CYCLE")
    print("=" * 70)
    now_utc = datetime.now(timezone.utc)
    print(f"Time: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Cost cap: ${cost_cap:.2f}  |  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    # Macro context
    print("📈 Fetching macro context...")
    macro = fetch_macro_snapshot()
    print(f"   Regime: {macro.regime}  ({macro.description})")
    print()
    if not dry_run:
        save_macro_snapshot(macro)

    # Load portfolio
    portfolio = load_portfolio()
    print(f"💼 Portfolio: ${portfolio.total_value_usd:,.2f} total  "
          f"| ${portfolio.cash_usd:,.2f} cash ({portfolio.cash_pct:.1f}%)")
    print(f"   {portfolio.open_positions} positions")
    print()

    portfolio_state = PortfolioState(
        total_open_positions=portfolio.open_positions,
        sector_allocations=portfolio.sector_allocations,
    )

    # Clean old watchlist entries
    cleared = clear_old_watchlist(days_old=14)
    if cleared:
        print(f"🧹 Cleared {cleared} stale watchlist entries (>14 days)")

    total_cost = 0.0

    # =========================================================================
    # PHASE A: Review open positions
    # =========================================================================
    positions = _load_positions()
    if positions:
        print()
        print("━" * 70)
        print("PHASE A — POSITION REVIEW")
        print("━" * 70)

        phase_a_cost, phase_a_debates = await phase_a_review_positions(
            positions=positions,
            macro=macro,
            portfolio_state=portfolio_state,
            cost_cap=cost_cap,
            single_ticker=single_ticker,
            dry_run=dry_run,
        )
        total_cost += phase_a_cost
        print(f"\n   Phase A total: {phase_a_debates} debates, ${phase_a_cost:.4f}")

    # If single ticker mode, stop here
    if single_ticker:
        print(f"\n✅ Single ticker review complete. Cost: ${total_cost:.4f}")
        return

    remaining_budget = cost_cap - total_cost

    # =========================================================================
    # PHASE B: Check watchlist for opportunities
    # =========================================================================
    if not skip_discovery and portfolio.can_open_new_position and remaining_budget > 0.10:
        print()
        print("━" * 70)
        print("PHASE B — WATCHLIST EVALUATION")
        print("━" * 70)

        phase_b_cost, phase_b_debates, new_positions = await phase_b_watchlist(
            macro=macro,
            portfolio=portfolio,
            portfolio_state=portfolio_state,
            cost_budget=min(remaining_budget, 1.5),
            dry_run=dry_run,
        )
        total_cost += phase_b_cost
        remaining_budget = cost_cap - total_cost
        print(f"\n   Phase B total: {phase_b_debates} debates, "
              f"{new_positions} new positions, ${phase_b_cost:.4f}")

        # =====================================================================
        # PHASE C: Mini-discovery (only if watchlist yielded nothing + cash >10%)
        # =====================================================================
        if (new_positions == 0
                and portfolio.cash_pct > 10
                and remaining_budget > 0.10
                and portfolio.can_open_new_position):
            print()
            print("━" * 70)
            print("PHASE C — MINI-DISCOVERY SCAN")
            print("━" * 70)

            phase_c_cost, new_watchlist = await phase_c_discovery(
                macro=macro,
                portfolio=portfolio,
                portfolio_state=portfolio_state,
                cost_budget=min(remaining_budget, 1.0),
                dry_run=dry_run,
            )
            total_cost += phase_c_cost
            print(f"\n   Phase C total: {new_watchlist} new watchlist, "
                  f"${phase_c_cost:.4f}")
    elif skip_discovery:
        print("\n⏭️  Discovery phases skipped (--skip-discovery)")
    elif not portfolio.can_open_new_position:
        print("\n⏭️  Discovery phases skipped (max positions or no cash)")
    else:
        print(f"\n⏭️  Discovery phases skipped (budget exhausted: "
              f"${remaining_budget:.2f} remaining)")

    # =========================================================================
    # Summary
    # =========================================================================
    print()
    print("=" * 70)
    print("EOD CYCLE COMPLETE")
    print("=" * 70)
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Budget remaining: ${cost_cap - total_cost:.4f}")

    # Update portfolio state
    if not dry_run:
        save_portfolio_state(
            total_value=portfolio.total_value_usd,
            cash=portfolio.cash_usd,
            notes=f"EOD review complete. Cost: ${total_cost:.4f}",
        )


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Run EOD review cycle")
    parser.add_argument("--cost-cap", type=float, default=DEFAULT_COST_CAP)
    parser.add_argument("--ticker", type=str, default=None, help="Single ticker")
    parser.add_argument("--skip-discovery", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asyncio.run(run_eod_cycle(
        cost_cap=args.cost_cap,
        single_ticker=args.ticker,
        skip_discovery=args.skip_discovery,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
