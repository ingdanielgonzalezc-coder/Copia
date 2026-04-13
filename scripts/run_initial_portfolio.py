"""
Initial portfolio setup — "I have $X, build me a portfolio."

Flow:
  1. Initialize portfolio with starting capital
  2. Run S&P 500 screener → top candidates + watchlist
  3. For each top candidate, run INITIAL debate
  4. Collect BUY_NEW verdicts and compute allocation plan
  5. Apply sector diversification rules
  6. Present final proposal for user approval
  7. Save positions + watchlist to Supabase

Usage:
    uv run python -m scripts.run_initial_portfolio --capital 50000
    uv run python -m scripts.run_initial_portfolio --capital 50000 --max-positions 8
    uv run python -m scripts.run_initial_portfolio --capital 50000 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from typing import Any

from src.compute_snapshot import compute_snapshot
from src.db import get_client, persist_debate_complete, save_macro_snapshot
from src.debate_engine import run_debate
from src.discovery import run_screener
from src.macro_context import fetch_macro_snapshot
from src.portfolio import (
    initialize_portfolio,
    save_candidates,
    save_portfolio_state,
)
from src.rules_engine import Phase, Position, PortfolioState


@dataclass
class ProposedPosition:
    ticker: str
    sector: str
    allocation_pct: float
    allocation_usd: float
    shares_approx: int
    price: float
    verdict: str
    confidence: int
    reasoning: str
    debate_id: str | None = None


# =============================================================================
# Allocation planner
# =============================================================================

def plan_allocation(
    approved: list[dict[str, Any]],
    total_capital: float,
    max_per_ticker_pct: float = 10.0,
    max_per_sector_pct: float = 25.0,
    cash_reserve_pct: float = 10.0,
) -> list[ProposedPosition]:
    """
    Distribute capital across approved tickers respecting rules.

    Each ticker gets allocation proportional to confidence, capped
    at max_per_ticker_pct. Sector caps are enforced. A cash reserve
    is kept for future opportunities.
    """
    investable = total_capital * (1 - cash_reserve_pct / 100)

    # Sort by confidence descending
    approved.sort(key=lambda x: -x["confidence"])

    # First pass: assign raw allocation by confidence
    total_conf = sum(a["confidence"] for a in approved)
    if total_conf == 0:
        return []

    positions = []
    sector_used: dict[str, float] = {}

    for a in approved:
        # Raw allocation proportional to confidence
        raw_pct = (a["confidence"] / total_conf) * (100 - cash_reserve_pct)

        # Cap at max per ticker
        alloc_pct = min(raw_pct, max_per_ticker_pct)

        # Cap at sector limit
        sector = a.get("sector", "Unknown")
        current_sector = sector_used.get(sector, 0)
        if current_sector + alloc_pct > max_per_sector_pct:
            alloc_pct = max(0, max_per_sector_pct - current_sector)

        if alloc_pct < 2.0:  # min position size
            continue

        alloc_pct = round(alloc_pct, 1)
        alloc_usd = total_capital * (alloc_pct / 100)
        price = a["price"]
        shares = int(alloc_usd / price) if price > 0 else 0

        positions.append(ProposedPosition(
            ticker=a["ticker"],
            sector=sector,
            allocation_pct=alloc_pct,
            allocation_usd=round(alloc_usd, 2),
            shares_approx=shares,
            price=round(price, 2),
            verdict=a["verdict"],
            confidence=a["confidence"],
            reasoning=a["reasoning"][:200],
            debate_id=a.get("debate_id"),
        ))

        sector_used[sector] = current_sector + alloc_pct

    return positions


# =============================================================================
# Main flow
# =============================================================================

async def run_initial_portfolio(
    capital: float,
    max_positions: int = 10,
    watchlist_size: int = 15,
    dry_run: bool = False,
    cost_cap: float = 5.0,
) -> None:
    """
    Build an initial portfolio from scratch.

    Args:
        capital: Starting capital in USD.
        max_positions: Maximum positions to open.
        watchlist_size: Additional tickers to save as watchlist.
        dry_run: If True, screen and debate but don't save positions.
        cost_cap: Maximum USD to spend on debates.
    """
    print("=" * 70)
    print("INITIAL PORTFOLIO SETUP")
    print("=" * 70)
    print(f"Starting capital: ${capital:,.2f}")
    print(f"Max positions: {max_positions}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print()

    # -------------------------------------------------------------------------
    # 1. Initialize portfolio
    # -------------------------------------------------------------------------
    if not dry_run:
        initialize_portfolio(capital)
    print(f"✅ Portfolio initialized with ${capital:,.2f}")
    print()

    # -------------------------------------------------------------------------
    # 2. Fetch macro context
    # -------------------------------------------------------------------------
    print("📈 Fetching macro context...")
    macro = fetch_macro_snapshot()
    print(f"   Regime: {macro.regime}  ({macro.description})")
    print()

    if not dry_run:
        save_macro_snapshot(macro)

    # -------------------------------------------------------------------------
    # 3. Run screener
    # -------------------------------------------------------------------------
    print("🔍 Running S&P 500 screener...")
    print()

    invest_candidates, watchlist_candidates = run_screener(
        top_n=max_positions + 5,  # screen a few extra in case debates reject some
        watchlist_n=watchlist_size,
    )

    if not invest_candidates:
        print("❌ No candidates found. Market may be unfavorable.")
        return

    print()
    print(f"Found {len(invest_candidates)} invest candidates "
          f"+ {len(watchlist_candidates)} watchlist")
    print()

    # Save watchlist immediately (free, no debates needed)
    if not dry_run and watchlist_candidates:
        saved = save_candidates(watchlist_candidates, status="watchlist")
        print(f"💾 Saved {saved} tickers to watchlist")
        print()

    # -------------------------------------------------------------------------
    # 4. Run INITIAL debates on top candidates
    # -------------------------------------------------------------------------
    print("🤖 Running INITIAL debates on top candidates...")
    print()

    approved: list[dict[str, Any]] = []
    total_cost = 0.0

    portfolio_state = PortfolioState(
        total_open_positions=0,
        sector_allocations={},
    )

    for idx, candidate in enumerate(invest_candidates, 1):
        ticker = candidate["ticker"]
        sector = candidate["sector"]

        if total_cost >= cost_cap:
            print(f"   ⏭️  Cost cap reached (${total_cost:.2f})")
            break

        print(f"─" * 60)
        print(f"[{idx}/{len(invest_candidates)}] {ticker}  "
              f"(score {candidate['score']}, {candidate['setup_type']})")
        print(f"─" * 60)

        # Compute full snapshot for this ticker
        try:
            snapshot = compute_snapshot(ticker, macro=macro)
            print(f"   Price: ${snapshot['price']}  "
                  f"Sector: {snapshot['fundamentals'].get('sector', 'N/A')}")
        except Exception as e:
            print(f"   ❌ Snapshot failed: {e}")
            continue

        # Use sector from snapshot if available (more accurate than Wikipedia)
        actual_sector = snapshot["fundamentals"].get("raw_sector") or sector

        # Build position (empty — we don't hold this yet)
        position = Position(
            ticker=ticker,
            shares=0,
            cost_basis=None,
            allocation_pct=0,
            sector=snapshot["fundamentals"].get("sector"),
        )

        # Run debate
        try:
            result = await run_debate(
                ticker=ticker,
                phase=Phase.INITIAL,
                snapshot=snapshot,
                position=position,
                portfolio=portfolio_state,
            )
        except Exception as e:
            print(f"   ❌ Debate failed: {e}")
            continue

        total_cost += result.total_cost_usd
        verdict = (result.final_verdict or {}).get("verdict", "UNKNOWN")
        confidence = (result.final_verdict or {}).get("confidence", 0)
        reasoning = (result.final_verdict or {}).get("reasoning", "")

        print(f"   → {verdict} (confidence {confidence})  "
              f"cost ${result.total_cost_usd:.4f}")

        if verdict == "BUY_NEW" and confidence >= 50:
            sizing = (result.final_verdict or {}).get("suggested_sizing", "+5%")
            approved.append({
                "ticker": ticker,
                "sector": actual_sector,
                "price": snapshot["price"],
                "verdict": verdict,
                "confidence": confidence,
                "reasoning": reasoning,
                "sizing": sizing,
                "debate_id": result.debate_id,
                "result": result,
                "snapshot": snapshot,
                "position": position,
            })
            print(f"   ✅ APPROVED for portfolio")

            # Update portfolio state for next debate's context
            portfolio_state.total_open_positions += 1
        else:
            print(f"   ⏭️  Not approved (verdict={verdict})")

        print()

    # -------------------------------------------------------------------------
    # 5. Plan allocation
    # -------------------------------------------------------------------------
    if not approved:
        print("❌ No tickers approved by debates. Try with different market conditions.")
        return

    print("=" * 70)
    print("ALLOCATION PLAN")
    print("=" * 70)
    print()

    positions = plan_allocation(
        approved=approved,
        total_capital=capital,
        cash_reserve_pct=10.0,
    )

    total_invested = sum(p.allocation_usd for p in positions)
    cash_remaining = capital - total_invested

    for i, p in enumerate(positions, 1):
        print(f"  {i}. {p.ticker:6}  {p.allocation_pct:5.1f}%  "
              f"${p.allocation_usd:>10,.2f}  ~{p.shares_approx} shares @ ${p.price}")
        print(f"     {p.sector}  |  {p.verdict} conf {p.confidence}")
        print(f"     {p.reasoning}")
        print()

    print(f"  Total invested: ${total_invested:>10,.2f} "
          f"({total_invested/capital*100:.1f}%)")
    print(f"  Cash reserve:   ${cash_remaining:>10,.2f} "
          f"({cash_remaining/capital*100:.1f}%)")
    print(f"  Debate costs:   ${total_cost:>10,.4f}")
    print()

    # Sector breakdown
    sector_totals: dict[str, float] = {}
    for p in positions:
        sector_totals[p.sector] = sector_totals.get(p.sector, 0) + p.allocation_pct
    print("  Sector allocation:")
    for sector, pct in sorted(sector_totals.items(), key=lambda x: -x[1]):
        print(f"    {sector:30} {pct:5.1f}%")
    print()

    # -------------------------------------------------------------------------
    # 6. Save positions (if not dry run)
    # -------------------------------------------------------------------------
    if dry_run:
        print("⚠️  DRY RUN — positions not saved")
        return

    print("💾 Saving positions to Supabase...")

    client = get_client()
    for p in positions:
        # Find the matching approved entry for persistence
        match = next((a for a in approved if a["ticker"] == p.ticker), None)

        # Save position
        try:
            client.table("positions").upsert({
                "ticker": p.ticker,
                "sector": p.sector,
                "shares": p.shares_approx,
                "cost_basis": p.price,
                "current_alloc_pct": p.allocation_pct,
                "notes": f"Initial portfolio setup. {p.verdict} conf {p.confidence}",
            }, on_conflict="ticker").execute()
            print(f"   ✅ {p.ticker}: {p.shares_approx} shares @ ${p.price}")
        except Exception as e:
            print(f"   ❌ {p.ticker}: {e}")

        # Persist debate
        if match and match.get("result"):
            persist_debate_complete(
                result=match["result"],
                snapshot=match["snapshot"],
                position=match["position"],
                trigger_type="initial_portfolio",
            )

    # Update portfolio state with remaining cash
    save_portfolio_state(
        total_value=capital,
        cash=cash_remaining,
        notes=f"Initial portfolio: {len(positions)} positions, "
              f"${total_invested:,.2f} invested, ${cash_remaining:,.2f} cash",
    )

    print()
    print("=" * 70)
    print("✅ PORTFOLIO SETUP COMPLETE")
    print("=" * 70)
    print(f"   {len(positions)} positions opened")
    print(f"   {len(watchlist_candidates)} tickers in watchlist")
    print(f"   ${cash_remaining:,.2f} cash available")
    print()
    print("Next steps:")
    print("   1. Run intraday cycle to monitor news")
    print("   2. Run EOD cycle tonight for first review")
    print("   3. The system will check watchlist for new opportunities at EOD")


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Build initial portfolio")
    parser.add_argument(
        "--capital", type=float, required=True,
        help="Starting capital in USD (e.g. 50000)",
    )
    parser.add_argument(
        "--max-positions", type=int, default=10,
        help="Maximum number of positions (default 10)",
    )
    parser.add_argument(
        "--watchlist", type=int, default=15,
        help="Watchlist size (default 15)",
    )
    parser.add_argument(
        "--cost-cap", type=float, default=5.0,
        help="Max USD to spend on debates (default $5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Screen and debate but don't save positions",
    )
    args = parser.parse_args()

    asyncio.run(run_initial_portfolio(
        capital=args.capital,
        max_positions=args.max_positions,
        watchlist_size=args.watchlist,
        dry_run=args.dry_run,
        cost_cap=args.cost_cap,
    ))


if __name__ == "__main__":
    main()
