"""
Discovery screener — scans S&P 500 for investment candidates.

Two-stage process:
  1. LIGHTWEIGHT SCAN: Fetch daily bars for all ~500 tickers via yfinance
     batch download. Compute basic indicators (MAs, RSI, ATR, volume).
     Apply hard filters from investor_profile.yaml. Cost: $0 (no LLM).

  2. SCORING: Score surviving tickers using two setup types:
     - momentum_with_pullback: strong uptrend but 3-7% off recent high
     - mean_reversion_in_uptrend: pullback to MA20/MA50 within uptrend

Output:
  - Top N candidates → ready for INITIAL debate (invest)
  - Next M candidates → saved to watchlist (monitor)

Usage:
    from src.discovery import run_screener
    invest, watchlist = run_screener(top_n=10, watchlist_n=15)

    # Or as CLI:
    uv run python -m src.discovery
    uv run python -m src.discovery --top 10 --watchlist 20
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = PROJECT_ROOT / "config" / "investor_profile.yaml"

# Sectors to exclude from screening (optional)
EXCLUDE_SECTORS: set[str] = set()


# =============================================================================
# S&P 500 ticker list
# =============================================================================

def fetch_sp500_tickers() -> list[dict[str, str]]:
    """
    Fetch current S&P 500 constituents from Wikipedia.

    Returns list of dicts with 'ticker' and 'sector' keys.
    """
    import io
    import requests

    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    try:
        resp = requests.get(url, headers={"User-Agent": "StockAdvisor/1.0"}, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        df = tables[0]
        tickers = []
        for _, row in df.iterrows():
            ticker = str(row.get("Symbol", "")).strip().replace(".", "-")
            sector = str(row.get("GICS Sector", ""))
            if ticker and sector not in EXCLUDE_SECTORS:
                tickers.append({"ticker": ticker, "sector": sector})
        return tickers
    except Exception as e:
        print(f"⚠️  Failed to fetch S&P 500 list: {e}")
        return []


# =============================================================================
# Load screening config
# =============================================================================

def _load_profile() -> dict[str, Any]:
    """Load investor_profile.yaml."""
    with open(PROFILE_PATH) as f:
        return yaml.safe_load(f).get("investor_profile", {})


# =============================================================================
# Batch data fetching (lightweight — no LLM cost)
# =============================================================================

def fetch_batch_data(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    """
    Batch-download daily bars for all tickers via yfinance.

    Returns a MultiIndex DataFrame with (Date, Ticker) structure.
    This is MUCH faster than per-ticker Polygon calls.
    """
    print(f"   Downloading data for {len(tickers)} tickers...")
    try:
        data = yf.download(
            tickers,
            period=period,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        return data
    except Exception as e:
        print(f"   ⚠️  Batch download failed: {e}")
        return pd.DataFrame()


# =============================================================================
# Per-ticker indicator computation (lightweight)
# =============================================================================

def compute_lightweight_indicators(
    df: pd.DataFrame,
) -> dict[str, Any] | None:
    """
    Compute basic indicators for a single ticker's OHLCV data.

    Returns None if insufficient data.
    """
    if df is None or len(df) < 200:
        return None

    try:
        close = df["Close"].dropna()
        high = df["High"].dropna()
        low = df["Low"].dropna()
        volume = df["Volume"].dropna()

        if len(close) < 200:
            return None

        price = float(close.iloc[-1])
        if price <= 0:
            return None

        # Moving averages
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])

        # RSI (14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else 100
        rsi = 100 - (100 / (1 + rs))

        # ATR (14)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(14).mean().iloc[-1])
        atr_pct = (atr / price) * 100

        # Volume
        avg_volume = float(volume.rolling(50).mean().iloc[-1])
        avg_dollar_volume = avg_volume * price

        # 52-week high/low
        high_52w = float(high.tail(252).max())
        low_52w = float(low.tail(252).min())
        pct_from_high = ((price - high_52w) / high_52w) * 100

        # Recent high (20d) for pullback detection
        high_20d = float(high.tail(20).max())
        pullback_from_20d = ((price - high_20d) / high_20d) * 100

        # Bollinger Bands (20, 2)
        bb_mid = ma20
        bb_std = float(close.rolling(20).std().iloc[-1])
        bb_lower = bb_mid - 2 * bb_std

        # Volume trend (recent vs average)
        vol_10d = float(volume.tail(10).mean())
        vol_ratio = vol_10d / avg_volume if avg_volume > 0 else 1.0

        # MA slopes (trend strength)
        ma50_5d_ago = float(close.rolling(50).mean().iloc[-6])
        ma50_slope = ((ma50 - ma50_5d_ago) / ma50_5d_ago) * 100

        return {
            "price": round(price, 2),
            "ma20": round(ma20, 2),
            "ma50": round(ma50, 2),
            "ma200": round(ma200, 2),
            "rsi": round(rsi, 1),
            "atr": round(atr, 2),
            "atr_pct": round(atr_pct, 2),
            "avg_dollar_volume": round(avg_dollar_volume, 0),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "pct_from_high": round(pct_from_high, 2),
            "pullback_from_20d": round(pullback_from_20d, 2),
            "bb_lower": round(bb_lower, 2),
            "vol_ratio": round(vol_ratio, 2),
            "ma50_slope": round(ma50_slope, 3),
            "price_above_ma50": price > ma50,
            "price_above_ma200": price > ma200,
            "ma50_above_ma200": ma50 > ma200,
            "uptrend": price > ma50 > ma200,
        }
    except Exception:
        return None


# =============================================================================
# Hard filters (from investor_profile.yaml)
# =============================================================================

def apply_hard_filters(
    indicators: dict[str, Any],
    profile: dict[str, Any],
) -> tuple[bool, str]:
    """
    Apply hard filters. Returns (passed, reason_if_failed).
    """
    filters = profile.get("hard_filters", {})

    # Market cap filter is handled by S&P 500 inclusion (all >$10B)

    # Volume
    min_vol = filters.get("min_avg_volume_usd", 50_000_000)
    if indicators["avg_dollar_volume"] < min_vol:
        return False, f"volume ${indicators['avg_dollar_volume']:,.0f} < ${min_vol:,.0f}"

    # ATR range
    atr_range = filters.get("atr_pct_range", [1.5, 6.0])
    if indicators["atr_pct"] < atr_range[0]:
        return False, f"atr {indicators['atr_pct']}% < {atr_range[0]}% (too quiet)"
    if indicators["atr_pct"] > atr_range[1]:
        return False, f"atr {indicators['atr_pct']}% > {atr_range[1]}% (too volatile)"

    # Must be above MA200 (we only buy in uptrends)
    if not indicators["price_above_ma200"]:
        return False, "price below MA200"

    return True, ""


# =============================================================================
# Scoring
# =============================================================================

def score_momentum_with_pullback(ind: dict[str, Any]) -> float:
    """
    Score: strong uptrend with a mild pullback (entry opportunity).

    Best candidate: trending up strongly, pulled back 3-7% from recent
    high, RSI 50-65 (not overbought), above MA50, good volume.
    """
    score = 0.0

    # Uptrend alignment (0-25)
    if ind["uptrend"]:
        score += 15
    if ind["price_above_ma50"]:
        score += 5
    if ind["ma50_above_ma200"]:
        score += 5

    # Pullback depth: sweet spot is -3% to -7% from 20d high (0-25)
    pb = abs(ind["pullback_from_20d"])
    if 3.0 <= pb <= 7.0:
        score += 25
    elif 1.5 <= pb < 3.0:
        score += 15
    elif 7.0 < pb <= 10.0:
        score += 10

    # RSI sweet spot: 45-65 (not overbought, not oversold) (0-20)
    rsi = ind["rsi"]
    if 50 <= rsi <= 65:
        score += 20
    elif 45 <= rsi < 50 or 65 < rsi <= 70:
        score += 10

    # Trend strength: MA50 slope positive (0-15)
    if ind["ma50_slope"] > 0.2:
        score += 15
    elif ind["ma50_slope"] > 0.05:
        score += 8

    # Volume confirmation (0-15)
    if ind["vol_ratio"] > 1.1:
        score += 15
    elif ind["vol_ratio"] > 0.9:
        score += 8

    return round(score, 1)


def score_mean_reversion_in_uptrend(ind: dict[str, Any]) -> float:
    """
    Score: price pulled back to support within intact uptrend.

    Best candidate: uptrend intact (MA50 > MA200), price near MA20 or
    lower Bollinger, RSI 30-45, declining panic volume.
    """
    score = 0.0

    # Uptrend must be intact (0-20)
    if ind["ma50_above_ma200"]:
        score += 15
    if ind["ma50_slope"] > 0:
        score += 5

    # Price near MA20 support (0-25)
    pct_vs_ma20 = ((ind["price"] - ind["ma20"]) / ind["ma20"]) * 100
    if -2.0 <= pct_vs_ma20 <= 1.0:
        score += 25  # right at MA20
    elif -4.0 <= pct_vs_ma20 < -2.0:
        score += 15  # slightly below

    # RSI in reversion zone (0-20)
    rsi = ind["rsi"]
    if 30 <= rsi <= 45:
        score += 20
    elif 45 < rsi <= 50:
        score += 10

    # Price near lower Bollinger (0-20)
    if ind["price"] <= ind["bb_lower"] * 1.02:
        score += 20
    elif ind["price"] <= ind["bb_lower"] * 1.05:
        score += 10

    # Panic volume decreasing (0-15)
    if ind["vol_ratio"] < 0.9:
        score += 15  # declining volume = selling exhaustion
    elif ind["vol_ratio"] < 1.0:
        score += 8

    return round(score, 1)


# =============================================================================
# Main screener
# =============================================================================

def run_screener(
    top_n: int = 10,
    watchlist_n: int = 15,
    existing_tickers: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Run the full screening pipeline.

    Args:
        top_n: Number of top candidates to return for investing.
        watchlist_n: Number of additional candidates for watchlist.
        existing_tickers: Tickers already in portfolio (excluded from results).

    Returns:
        (invest_candidates, watchlist_candidates)
        Each is a list of dicts with: ticker, sector, score, setup_type, summary, indicators
    """
    existing = existing_tickers or set()
    profile = _load_profile()

    # Step 1: Get universe
    print("📡 Fetching S&P 500 constituents...")
    sp500 = fetch_sp500_tickers()
    if not sp500:
        print("❌ Could not fetch S&P 500 list")
        return [], []
    print(f"   {len(sp500)} tickers in universe")

    # Exclude already-held tickers
    sp500 = [t for t in sp500 if t["ticker"] not in existing]
    print(f"   {len(sp500)} after excluding {len(existing)} held positions")

    # Step 2: Batch download data
    all_tickers = [t["ticker"] for t in sp500]
    sector_map = {t["ticker"]: t["sector"] for t in sp500}

    raw_data = fetch_batch_data(all_tickers, period="1y")
    if raw_data.empty:
        print("❌ No data returned from batch download")
        return [], []

    # Step 3: Compute indicators and apply filters
    print("   Computing indicators and filtering...")
    candidates = []
    filtered_count = 0

    for ticker in all_tickers:
        try:
            # Extract single ticker data from multi-ticker DataFrame
            if isinstance(raw_data.columns, pd.MultiIndex):
                if ticker not in raw_data.columns.get_level_values(0):
                    continue
                ticker_df = raw_data[ticker].dropna(how="all")
            else:
                ticker_df = raw_data
                if len(all_tickers) > 1:
                    continue

            indicators = compute_lightweight_indicators(ticker_df)
            if indicators is None:
                filtered_count += 1
                continue

            passed, reason = apply_hard_filters(indicators, profile)
            if not passed:
                filtered_count += 1
                continue

            # Score with both strategies, take the best
            momentum_score = score_momentum_with_pullback(indicators)
            reversion_score = score_mean_reversion_in_uptrend(indicators)

            if momentum_score >= reversion_score:
                best_score = momentum_score
                setup_type = "momentum_with_pullback"
            else:
                best_score = reversion_score
                setup_type = "mean_reversion_in_uptrend"

            # Minimum score threshold
            if best_score < 20:
                filtered_count += 1
                continue

            candidates.append({
                "ticker": ticker,
                "sector": sector_map.get(ticker, "Unknown"),
                "score": best_score,
                "setup_type": setup_type,
                "indicators": indicators,
                "summary": {
                    "price": indicators["price"],
                    "rsi": indicators["rsi"],
                    "pct_from_high": indicators["pct_from_high"],
                    "pullback_20d": indicators["pullback_from_20d"],
                    "atr_pct": indicators["atr_pct"],
                    "uptrend": indicators["uptrend"],
                    "vol_ratio": indicators["vol_ratio"],
                },
            })

        except Exception:
            filtered_count += 1
            continue

    print(f"   {len(candidates)} candidates survived filters "
          f"({filtered_count} filtered out)")

    # Step 4: Sort by score and split into invest + watchlist
    candidates.sort(key=lambda x: -x["score"])

    invest = candidates[:top_n]
    watchlist = candidates[top_n:top_n + watchlist_n]

    return invest, watchlist


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    """Run screener and print results."""
    import argparse

    parser = argparse.ArgumentParser(description="S&P 500 screener")
    parser.add_argument("--top", type=int, default=10, help="Top N to invest")
    parser.add_argument("--watchlist", type=int, default=15, help="Watchlist size")
    args = parser.parse_args()

    print("=" * 70)
    print("DISCOVERY SCREENER")
    print("=" * 70)
    print()

    invest, watchlist = run_screener(top_n=args.top, watchlist_n=args.watchlist)

    if invest:
        print()
        print(f"🎯 TOP {len(invest)} — INVEST CANDIDATES")
        print("-" * 70)
        for i, c in enumerate(invest, 1):
            ind = c["indicators"]
            print(f"  {i:2}. {c['ticker']:6} score={c['score']:5.1f}  "
                  f"setup={c['setup_type']}")
            print(f"      ${ind['price']:>8.2f}  RSI {ind['rsi']:4.1f}  "
                  f"ATR {ind['atr_pct']:.1f}%  "
                  f"from_high {ind['pct_from_high']:+.1f}%  "
                  f"pb20d {ind['pullback_from_20d']:+.1f}%  "
                  f"{'↑ uptrend' if ind['uptrend'] else '— no uptrend'}")

    if watchlist:
        print()
        print(f"👀 WATCHLIST ({len(watchlist)} tickers)")
        print("-" * 70)
        for c in watchlist:
            ind = c["indicators"]
            print(f"  {c['ticker']:6} score={c['score']:5.1f}  "
                  f"${ind['price']:>8.2f}  RSI {ind['rsi']:4.1f}  "
                  f"from_high {ind['pct_from_high']:+.1f}%  "
                  f"{c['setup_type']}")

    if not invest and not watchlist:
        print("No candidates found. Market conditions may be unfavorable.")


if __name__ == "__main__":
    main()
