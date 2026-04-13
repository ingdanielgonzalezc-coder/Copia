"""
Smoke test: run the 3-stage news pipeline on a portfolio.

Usage:
    uv run python -m scripts.process_news
    uv run python -m scripts.process_news --tickers NVDA AAPL MSFT
    uv run python -m scripts.process_news --hours 48 --min-score 50

Default tickers: the test portfolio (NVDA, AAPL, MSFT, JPM, XOM).
"""

from __future__ import annotations

import argparse
import asyncio

from src.news_pipeline import ScoredNewsItem, process_news_pipeline

DEFAULT_TICKERS = ["NVDA", "AAPL", "MSFT", "JPM", "XOM"]


def _print_item(item: ScoredNewsItem) -> None:
    marker = {
        "trigger_debate": "🔥",
        "skip_low_score": "🟡",
        "skip_duplicate": "🔄",
        "skip_heuristic": "❌",
    }.get(item.decision, "❓")

    print()
    print(f"{marker} {item.decision}  (score={item.relevance_score})")
    print(f"   {item.news.title[:120]}")
    print(f"   tickers={item.news.tickers}  publisher={item.news.publisher or 'N/A'}")
    if item.one_line_summary and item.decision != "skip_heuristic":
        print(f"   summary: {item.one_line_summary}")
    if item.decision == "trigger_debate":
        print(f"   impact: {item.impact_direction}  urgency: {item.urgency}")
    if item.duplicate_similarity is not None and item.decision == "skip_duplicate":
        print(f"   similarity: {item.duplicate_similarity:.3f}")


async def main_async(args: argparse.Namespace) -> None:
    print(f"Running news pipeline on {args.tickers}")
    print(f"Window: last {args.hours}h  |  min_score: {args.min_score}")
    print()

    results = await process_news_pipeline(
        portfolio_tickers=set(args.tickers),
        min_relevance_score=args.min_score,
        since_hours=args.hours,
        limit_per_ticker=args.limit,
    )

    print()
    print("=" * 80)
    print("RESULTS")
    print("=" * 80)

    if not results:
        print("No news items returned. Possible causes:")
        print("  - No news in the time window")
        print("  - Polygon API returned empty")
        print("  - All tickers filtered out")
        return

    # Triggered first, then by category
    order = {"trigger_debate": 0, "skip_low_score": 1, "skip_duplicate": 2, "skip_heuristic": 3}
    sorted_results = sorted(results, key=lambda r: (order.get(r.decision, 9), -r.relevance_score))

    if not args.show_skipped:
        sorted_results = [r for r in sorted_results if r.decision in ("trigger_debate", "skip_low_score")]
        if not sorted_results:
            print("(no items above heuristic filter — pass --show-skipped to see filtered ones)")
            return

    for item in sorted_results:
        _print_item(item)

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    total_cost = sum(r.cost_usd for r in results)
    counts = {
        "trigger_debate": sum(1 for r in results if r.decision == "trigger_debate"),
        "skip_low_score": sum(1 for r in results if r.decision == "skip_low_score"),
        "skip_duplicate": sum(1 for r in results if r.decision == "skip_duplicate"),
        "skip_heuristic": sum(1 for r in results if r.decision == "skip_heuristic"),
    }

    print(f"  🔥 trigger_debate:  {counts['trigger_debate']}")
    print(f"  🟡 skip_low_score:  {counts['skip_low_score']}")
    print(f"  🔄 skip_duplicate:  {counts['skip_duplicate']}")
    print(f"  ❌ skip_heuristic:  {counts['skip_heuristic']}")
    print(f"  Total LLM scoring cost: ${total_cost:.5f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the news pipeline smoke test")
    parser.add_argument(
        "--tickers", nargs="+", default=DEFAULT_TICKERS,
        help="Tickers to monitor (default: test portfolio)",
    )
    parser.add_argument(
        "--min-score", type=int, default=60,
        help="Minimum relevance score to trigger a debate (default 60)",
    )
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Look back N hours for news (default 24)",
    )
    parser.add_argument(
        "--limit", type=int, default=10,
        help="Max news items per ticker from Polygon (default 10)",
    )
    parser.add_argument(
        "--show-skipped", action="store_true",
        help="Show items skipped by heuristic and dedup (default: hide)",
    )

    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
