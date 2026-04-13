"""
Intraday cycle — news pipeline + INTRADAY debates + watchlist monitoring.

Flow:
  1. Load portfolio positions + watchlist tickers
  2. Run news pipeline on ALL monitored tickers (portfolio + watchlist)
  3. Portfolio news → debate if material (score >= 60, urgency immediate/this_week)
  4. Watchlist news → log only (flagged for EOD review, no debate cost)
  5. Apply per-ticker cooldown and per-cycle cap
  6. Persist debates

Usage:
    uv run python -m scripts.run_intraday_cycle
    uv run python -m scripts.run_intraday_cycle --dry-run
    uv run python -m scripts.run_intraday_cycle --hours 4 --max-debates 3
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.compute_snapshot import compute_snapshot
from src.db import get_client, persist_debate_complete, save_macro_snapshot
from src.debate_engine import run_debate
from src.macro_context import fetch_macro_snapshot
from src.news_pipeline import ScoredNewsItem, process_news_pipeline
from src.portfolio import get_watchlist_tickers
from src.rules_engine import Phase, Position, PortfolioState

TICKER_COOLDOWN_MIN = 30
DEFAULT_MAX_DEBATES_PER_CYCLE = 5
URGENCY_TRIGGERS = {"immediate", "this_week"}


@dataclass
class CycleSummary:
    news_evaluated: int
    news_triggered_pipeline: int
    skipped_urgency: int
    skipped_cooldown: int
    skipped_cap: int
    skipped_no_position: int
    debates_run: int
    debate_verdicts: dict[str, int]
    watchlist_alerts: int
    total_news_cost: float
    total_debate_cost: float

    @property
    def total_cost(self) -> float:
        return self.total_news_cost + self.total_debate_cost


def load_portfolio_positions() -> dict[str, Position]:
    try:
        client = get_client()
        response = client.table("positions").select("*").execute()
        rows = response.data or []
    except Exception as e:
        print(f"⚠️  Failed to load positions: {e}")
        return {}

    positions: dict[str, Position] = {}
    for row in rows:
        if row.get("shares", 0) <= 0:
            continue
        positions[row["ticker"]] = Position(
            ticker=row["ticker"],
            shares=float(row.get("shares", 0)),
            cost_basis=row.get("cost_basis"),
            allocation_pct=float(row.get("current_alloc_pct") or 0),
            sector=row.get("sector"),
            last_debate_at=row.get("last_debate_at"),
        )
    return positions


def has_recent_debate(ticker: str, cooldown_min: int = TICKER_COOLDOWN_MIN) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=cooldown_min)
    try:
        client = get_client()
        response = (
            client.table("debates")
            .select("id, timestamp")
            .eq("ticker", ticker)
            .eq("phase", "INTRADAY")
            .gte("timestamp", cutoff.isoformat())
            .limit(1)
            .execute()
        )
        return bool(response.data)
    except Exception:
        return False


async def run_cycle(
    dry_run: bool = False,
    hours: int = 2,
    max_debates: int = DEFAULT_MAX_DEBATES_PER_CYCLE,
    min_score: int = 60,
) -> CycleSummary:
    print("=" * 70)
    print("INTRADAY CYCLE")
    print("=" * 70)
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"News window: {hours}h  |  min_score: {min_score}  |  cap: {max_debates}")
    print()

    # 1. Load portfolio + watchlist
    portfolio_positions = load_portfolio_positions()
    portfolio_tickers = set(portfolio_positions.keys())
    if portfolio_tickers:
        print(f"📋 Portfolio: {len(portfolio_tickers)} positions — {sorted(portfolio_tickers)}")

    watchlist_tickers = set(get_watchlist_tickers()) - portfolio_tickers
    if watchlist_tickers:
        print(f"👀 Watchlist: {len(watchlist_tickers)} tickers — {sorted(watchlist_tickers)}")

    all_monitored = portfolio_tickers | watchlist_tickers
    if not all_monitored:
        print("⚠️  No tickers to monitor.")
        return CycleSummary(0, 0, 0, 0, 0, 0, 0, {}, 0, 0.0, 0.0)
    print()

    # 2. Macro
    print("📈 Fetching macro context...")
    macro = fetch_macro_snapshot()
    print(f"   Regime: {macro.regime}  ({macro.description})")
    print()
    if not dry_run:
        save_macro_snapshot(macro)

    # 3. News pipeline on ALL monitored tickers
    news_results = await process_news_pipeline(
        portfolio_tickers=all_monitored,
        min_relevance_score=min_score,
        since_hours=hours,
    )

    triggered = [r for r in news_results if r.decision == "trigger_debate"]
    total_news_cost = sum(r.cost_usd for r in news_results)
    print(f"📰 News pipeline: {len(triggered)} items above score {min_score}")
    print(f"   Cost: ${total_news_cost:.5f}")
    print()

    # 4. Separate portfolio vs watchlist alerts
    portfolio_triggered = []
    watchlist_alerts = []

    for item in triggered:
        is_portfolio = any(t in portfolio_tickers for t in item.news.tickers)
        is_watchlist = any(t in watchlist_tickers for t in item.news.tickers)
        if is_portfolio:
            portfolio_triggered.append(item)
        elif is_watchlist:
            watchlist_alerts.append(item)

    if watchlist_alerts:
        print(f"👀 Watchlist alerts ({len(watchlist_alerts)}) — logged for EOD:")
        for item in watchlist_alerts:
            wl = [t for t in item.news.tickers if t in watchlist_tickers]
            print(f"   {','.join(wl)}: \"{item.news.title[:80]}\" "
                  f"(score {item.relevance_score}, {item.impact_direction})")
        print()

    # 5. Urgency filter (portfolio only)
    after_urgency = [r for r in portfolio_triggered if r.urgency in URGENCY_TRIGGERS]
    skipped_urgency = len(portfolio_triggered) - len(after_urgency)

    # 6. Build debate plan
    debate_plan: list[tuple[ScoredNewsItem, str]] = []
    skipped_no_position = 0
    for item in after_urgency:
        targets = [t for t in item.news.tickers if t in portfolio_tickers]
        if not targets:
            skipped_no_position += 1
            continue
        for ticker in targets:
            if portfolio_positions[ticker].shares <= 0:
                skipped_no_position += 1
                continue
            debate_plan.append((item, ticker))

    # 7. Cooldown
    after_cooldown: list[tuple[ScoredNewsItem, str]] = []
    skipped_cooldown = 0
    seen: set[str] = set()
    for item, ticker in debate_plan:
        if ticker in seen or has_recent_debate(ticker):
            skipped_cooldown += 1
            continue
        seen.add(ticker)
        after_cooldown.append((item, ticker))

    # 8. Cap
    after_cooldown.sort(key=lambda x: -x[0].relevance_score)
    skipped_cap = max(0, len(after_cooldown) - max_debates)
    final_plan = after_cooldown[:max_debates]

    if not final_plan:
        print("✅ No debates to run this cycle.")
        return CycleSummary(
            len(news_results), len(triggered), skipped_urgency,
            skipped_cooldown, skipped_cap, skipped_no_position,
            0, {}, len(watchlist_alerts), total_news_cost, 0.0,
        )

    if dry_run:
        print(f"⚠️  DRY RUN — {len(final_plan)} debates would run.")
        return CycleSummary(
            len(news_results), len(triggered), skipped_urgency,
            skipped_cooldown, skipped_cap, skipped_no_position,
            0, {}, len(watchlist_alerts), total_news_cost, 0.0,
        )

    # 9. Execute debates
    total_debate_cost = 0.0
    verdict_counts: dict[str, int] = {}
    portfolio_state = PortfolioState(
        total_open_positions=len(portfolio_positions),
        sector_allocations={},
    )

    for idx, (news_item, ticker) in enumerate(final_plan, start=1):
        print(f"\n{'─' * 70}")
        print(f"DEBATE {idx}/{len(final_plan)} — {ticker}")
        print(f"  News: {news_item.news.title[:100]}")
        print(f"{'─' * 70}")

        position = portfolio_positions[ticker]
        try:
            snapshot = compute_snapshot(
                ticker, macro=macro,
                cost_basis=position.cost_basis, shares=position.shares,
            )
        except Exception as e:
            print(f"   ❌ Snapshot failed: {e}")
            continue

        news_payload = {
            "title": news_item.news.title,
            "description": news_item.news.description,
            "publisher": news_item.news.publisher,
            "url": news_item.news.url,
            "published_utc": news_item.news.published_utc,
            "relevance_score": news_item.relevance_score,
            "impact_direction": news_item.impact_direction,
            "urgency": news_item.urgency,
            "summary": news_item.one_line_summary,
        }

        try:
            result = await run_debate(
                ticker=ticker, phase=Phase.INTRADAY,
                snapshot=snapshot, position=position,
                portfolio=portfolio_state, news_item=news_payload,
            )
        except Exception as e:
            print(f"   ❌ Debate failed: {e}")
            continue

        verdict = (result.final_verdict or {}).get("verdict", "UNKNOWN")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        total_debate_cost += result.total_cost_usd
        print(f"   Verdict: {verdict} (conf {(result.final_verdict or {}).get('confidence')})")
        print(f"   Cost: ${result.total_cost_usd:.4f}")

        persist_debate_complete(
            result=result, snapshot=snapshot, position=position,
            news_item=news_payload, trigger_type="news_intraday",
        )

    return CycleSummary(
        len(news_results), len(triggered), skipped_urgency,
        skipped_cooldown, skipped_cap, skipped_no_position,
        len(verdict_counts), verdict_counts, len(watchlist_alerts),
        total_news_cost, total_debate_cost,
    )


def print_summary(s: CycleSummary) -> None:
    print(f"\n{'=' * 70}\nCYCLE SUMMARY\n{'=' * 70}")
    print(f"News evaluated:            {s.news_evaluated}")
    print(f"News triggered:            {s.news_triggered_pipeline}")
    print(f"  👀 Watchlist alerts:      {s.watchlist_alerts}")
    print(f"  ⏭️  Skipped (urgency):     {s.skipped_urgency}")
    print(f"  ⏭️  Skipped (cooldown):    {s.skipped_cooldown}")
    print(f"Debates run:               {s.debates_run}")
    if s.debate_verdicts:
        for v, c in sorted(s.debate_verdicts.items()):
            print(f"  {v}: {c}")
    print(f"\nNews cost:   ${s.total_news_cost:.5f}")
    print(f"Debate cost: ${s.total_debate_cost:.5f}")
    print(f"TOTAL:       ${s.total_cost:.5f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one intraday cycle")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--hours", type=int, default=2)
    parser.add_argument("--max-debates", type=int, default=DEFAULT_MAX_DEBATES_PER_CYCLE)
    parser.add_argument("--min-score", type=int, default=60)
    args = parser.parse_args()
    summary = asyncio.run(run_cycle(
        dry_run=args.dry_run, hours=args.hours,
        max_debates=args.max_debates, min_score=args.min_score,
    ))
    print_summary(summary)


if __name__ == "__main__":
    main()
