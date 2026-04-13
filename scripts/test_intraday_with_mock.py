"""
Smoke test the INTRADAY phase end-to-end with a mock news event.

Useful when the live news feed has nothing material — you can deterministically
fire a debate against a real position with a controlled news payload, then
inspect how Bull/Bear/Judge react.

All mock events are clearly tagged: title prefixed with "[MOCK]", publisher
set to "MOCK_TEST", trigger_type set to "mock_test" in Supabase. This makes
it trivial to filter test debates out of production analytics later.

Usage:
    uv run python -m scripts.test_intraday_with_mock NVDA
    uv run python -m scripts.test_intraday_with_mock NVDA --scenario negative_strong
    uv run python -m scripts.test_intraday_with_mock MSFT --scenario positive_partnership
    uv run python -m scripts.test_intraday_with_mock JPM --list   # list scenarios
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from src.compute_snapshot import compute_snapshot
from src.db import get_client, persist_debate_complete
from src.debate_engine import run_debate
from src.macro_context import fetch_macro_snapshot
from src.rules_engine import Phase, Position, PortfolioState

# =============================================================================
# Mock news scenarios — designed to exercise distinct branches of the debate
# =============================================================================

MOCK_SCENARIOS: dict[str, dict[str, Any]] = {
    "positive_strong": {
        "title": "[MOCK] {ticker} reports blowout Q earnings: revenue +28% Y/Y, raises full-year guidance",
        "description": (
            "{ticker} reported quarterly earnings significantly above consensus, with revenue "
            "growing 28% year-over-year and operating margins expanding 320 basis points. "
            "Management raised full-year revenue guidance by 8% citing strong demand across all "
            "business segments. Analysts at major banks are expected to lift price targets."
        ),
        "impact_direction": "positive",
        "urgency": "immediate",
        "relevance_score": 92,
        "summary": "Major earnings beat with raised guidance",
    },
    "negative_strong": {
        "title": "[MOCK] {ticker} discloses SEC investigation into revenue recognition practices",
        "description": (
            "{ticker} disclosed in an 8-K filing this morning that it has received a subpoena "
            "from the SEC related to its revenue recognition practices over the past three "
            "fiscal years. The company stated it is cooperating fully but cannot predict the "
            "outcome. Shares fell sharply in pre-market trading. Several law firms have already "
            "announced shareholder investigations."
        ),
        "impact_direction": "negative",
        "urgency": "immediate",
        "relevance_score": 95,
        "summary": "SEC subpoena over revenue accounting practices",
    },
    "negative_specific": {
        "title": "[MOCK] {ticker} loses largest customer representing ~15% of annual revenue",
        "description": (
            "{ticker} confirmed that one of its top customers has decided not to renew a "
            "multi-year contract that had represented approximately 15% of total annual revenue. "
            "The customer is reportedly switching to a competitor citing pricing and integration "
            "concerns. Management said it expects the impact to be felt over the next two quarters "
            "but maintained its long-term outlook."
        ),
        "impact_direction": "negative",
        "urgency": "this_week",
        "relevance_score": 85,
        "summary": "Lost top customer (~15% of revenue)",
    },
    "positive_partnership": {
        "title": "[MOCK] {ticker} announces strategic partnership with major hyperscaler, multi-year deal",
        "description": (
            "{ticker} announced a multi-year strategic partnership with a major hyperscale cloud "
            "provider. Under the agreement, {ticker}'s technology will be deployed across the "
            "partner's global infrastructure starting next quarter. Financial terms were not "
            "disclosed but analysts estimate the deal could add 5-8% to revenue over three years. "
            "The companies will jointly develop new offerings for enterprise customers."
        ),
        "impact_direction": "positive",
        "urgency": "this_week",
        "relevance_score": 80,
        "summary": "Multi-year hyperscaler partnership announced",
    },
    "ambiguous_macro": {
        "title": "[MOCK] Fed signals more aggressive rate path; tech and growth names under pressure",
        "description": (
            "The Federal Reserve released minutes from its latest meeting showing several "
            "members favor a more aggressive rate path than markets had priced in, citing "
            "persistent services inflation. Tech and growth stocks sold off in response while "
            "financials gained on improved net interest margin expectations. The impact on "
            "individual names depends on duration of cash flows and balance sheet composition."
        ),
        "impact_direction": "ambiguous",
        "urgency": "this_week",
        "relevance_score": 70,
        "summary": "Hawkish Fed minutes, mixed sector impact",
    },
    "regulatory_approval": {
        "title": "[MOCK] {ticker} receives unexpected regulatory clearance for key product expansion",
        "description": (
            "{ticker} announced it has received regulatory clearance from a key authority for the "
            "expansion of its flagship product into a new market segment. The approval came earlier "
            "than analysts had expected and removes a major overhang on the stock. The new market "
            "is estimated to be worth several billion dollars annually. {ticker} expects to begin "
            "rollout within 90 days."
        ),
        "impact_direction": "positive",
        "urgency": "immediate",
        "relevance_score": 88,
        "summary": "Unexpected regulatory clearance for product expansion",
    },
}


def build_mock_news(scenario_key: str, ticker: str) -> dict[str, Any]:
    """Build a news_item payload from a scenario template."""
    template = MOCK_SCENARIOS[scenario_key]
    return {
        "title": template["title"].format(ticker=ticker),
        "description": template["description"].format(ticker=ticker),
        "publisher": "MOCK_TEST",
        "url": None,
        "published_utc": "MOCK",
        "relevance_score": template["relevance_score"],
        "impact_direction": template["impact_direction"],
        "urgency": template["urgency"],
        "summary": template["summary"],
        "scenario_key": scenario_key,
        "is_mock": True,
    }


def load_position(ticker: str) -> Position | None:
    """Load a single position from Supabase."""
    try:
        client = get_client()
        response = (
            client.table("positions")
            .select("*")
            .eq("ticker", ticker)
            .limit(1)
            .execute()
        )
        rows = response.data or []
    except Exception as e:
        print(f"⚠️  Failed to load position for {ticker}: {e}")
        return None

    if not rows:
        return None

    row = rows[0]
    return Position(
        ticker=row["ticker"],
        shares=float(row.get("shares") or 0),
        cost_basis=row.get("cost_basis"),
        allocation_pct=float(row.get("current_alloc_pct") or 0),
        sector=row.get("sector"),
    )


async def main_async(args: argparse.Namespace) -> None:
    if args.list:
        print("Available mock scenarios:")
        print()
        for key, template in MOCK_SCENARIOS.items():
            print(f"  {key}")
            print(f"    impact: {template['impact_direction']}  "
                  f"urgency: {template['urgency']}  "
                  f"score: {template['relevance_score']}")
            print(f"    {template['summary']}")
            print()
        return

    ticker = args.ticker.upper()
    scenario = args.scenario

    if scenario not in MOCK_SCENARIOS:
        print(f"❌ Unknown scenario: {scenario}")
        print(f"   Available: {', '.join(MOCK_SCENARIOS.keys())}")
        return

    print("=" * 70)
    print(f"INTRADAY MOCK TEST — {ticker}")
    print("=" * 70)
    print(f"Scenario: {scenario}")
    print()

    # Load position from DB
    position = load_position(ticker)
    if not position or position.shares <= 0:
        print(f"❌ No position found for {ticker} in Supabase 'positions' table.")
        print(f"   INTRADAY phase requires an existing position.")
        print(f"   Insert one first or pick a different ticker.")
        return

    print(f"📋 Position: {position.shares} shares @ ${position.cost_basis} "
          f"({position.allocation_pct}% allocation)")
    print()

    # Build mock news
    news_payload = build_mock_news(scenario, ticker)
    print("📰 Mock news:")
    print(f"   Title: {news_payload['title']}")
    print(f"   Impact: {news_payload['impact_direction']}  "
          f"Urgency: {news_payload['urgency']}  "
          f"Score: {news_payload['relevance_score']}")
    print()

    # Macro + snapshot
    print("📈 Fetching macro context...")
    macro = fetch_macro_snapshot()
    print(f"   Regime: {macro.regime}")
    print()

    print("📊 Computing snapshot...")
    snapshot = compute_snapshot(
        ticker,
        macro=macro,
        cost_basis=position.cost_basis,
        shares=position.shares,
    )
    print(f"   Price: ${snapshot['price']} ({snapshot['change_pct']:+.2f}%)")
    pm = snapshot.get("position_metrics")
    if pm:
        marker = "🟢" if pm["unrealized_pnl_pct"] >= 0 else "🔴"
        print(f"   {marker} Unrealized P&L: ${pm['unrealized_pnl_usd']:+,.2f} "
              f"({pm['unrealized_pnl_pct']:+.2f}%)")
    print()

    # Run debate
    portfolio = PortfolioState(total_open_positions=1, sector_allocations={})

    print("🤖 Running INTRADAY debate (Bull + Bear + Judge)...")
    print()
    result = await run_debate(
        ticker=ticker,
        phase=Phase.INTRADAY,
        snapshot=snapshot,
        position=position,
        portfolio=portfolio,
        news_item=news_payload,
    )

    # Print agent outputs
    if result.bull_response:
        print("=" * 70)
        print(f"🐂 BULL  ({result.bull_metrics['model']})  "
              f"latency {result.bull_metrics['latency_ms']}ms  "
              f"cost ${result.bull_metrics['cost_usd']:.5f}")
        print("=" * 70)
        print(json.dumps(result.bull_response, indent=2, ensure_ascii=False))
        print()

    if result.bear_response:
        print("=" * 70)
        print(f"🐻 BEAR  ({result.bear_metrics['model']})  "
              f"latency {result.bear_metrics['latency_ms']}ms  "
              f"cost ${result.bear_metrics['cost_usd']:.5f}")
        print("=" * 70)
        print(json.dumps(result.bear_response, indent=2, ensure_ascii=False))
        print()

    if result.judge_response:
        print("=" * 70)
        print(f"⚖️  JUDGE  ({result.judge_metrics['model']})  "
              f"escalated={result.judge_escalated}  "
              f"latency {result.judge_metrics['latency_ms']}ms  "
              f"cost ${result.judge_metrics['cost_usd']:.5f}")
        print("=" * 70)
        print(json.dumps(result.judge_response, indent=2, ensure_ascii=False))
        print()

    # Final verdict and persistence
    print("=" * 70)
    print("FINAL VERDICT")
    print("=" * 70)
    if result.final_verdict:
        verdict = result.final_verdict.get("verdict")
        confidence = result.final_verdict.get("confidence")
        print(f"  {verdict}  (confidence {confidence})")
        if result.was_downgraded:
            print(f"  ⚠️  Downgraded by post-validator")
    print(f"  Total cost: ${result.total_cost_usd:.4f}")
    print(f"  Total latency: {result.total_latency_ms/1000:.1f}s")
    print()

    if not args.no_save:
        print("💾 Persisting to Supabase (trigger_type='mock_test')...")
        persist_result = persist_debate_complete(
            result=result,
            snapshot=snapshot,
            position=position,
            news_item=news_payload,
            trigger_type="mock_test",
        )
        if persist_result.get("debate_uuid"):
            print(f"   ✅ debate uuid: {persist_result['debate_uuid']}")
        else:
            print(f"   ❌ Persistence failed: {persist_result.get('errors')}")
    else:
        print("(--no-save: skipped persistence)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test INTRADAY phase with a mock news event")
    parser.add_argument("ticker", nargs="?", default="NVDA", help="Ticker (must have a position)")
    parser.add_argument(
        "--scenario",
        default="positive_strong",
        help=f"Mock scenario name. One of: {', '.join(MOCK_SCENARIOS.keys())}",
    )
    parser.add_argument("--list", action="store_true", help="List available scenarios and exit")
    parser.add_argument("--no-save", action="store_true", help="Skip Supabase persistence")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
