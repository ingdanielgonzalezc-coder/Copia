"""
Snapshot module — builds the complete context dict consumed by Bull/Bear/Judge.

A "snapshot" combines:
  - Technical indicators (RSI, MACD, MAs, Bollinger, ATR, stochastic, OBV)
  - Fundamentals from yfinance (P/E, margins, debt, growth, market cap)
  - Analyst consensus (price targets, recommendation, upgrades/downgrades)
  - Short interest (short % of float, short ratio)
  - Earnings date (next expected report)
  - Sector benchmarks from sector_benchmarks.yaml (median + p25/p75)
  - Macro regime from macro_context.py

The snapshot is the single source of truth that the agents consume.
Run as a script to smoke-test on any ticker:
    uv run python -m src.compute_snapshot NVDA
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import yaml
import yfinance as yf
from dotenv import load_dotenv
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import EMAIndicator, MACD, SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator

from src.macro_context import MacroSnapshot, fetch_macro_snapshot

load_dotenv()

POLYGON_BASE_URL = "https://api.polygon.io"
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECTOR_BENCHMARKS_PATH = PROJECT_ROOT / "config" / "sector_benchmarks.yaml"

# Map yfinance sector strings to our sector_benchmarks.yaml keys
YFINANCE_SECTOR_MAP = {
    "Technology": "Technology",
    "Financial Services": "Financials",
    "Healthcare": "Healthcare",
    "Consumer Cyclical": "ConsumerDiscretionary",
    "Consumer Defensive": "ConsumerStaples",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Basic Materials": "Materials",
    "Utilities": "Utilities",
    "Real Estate": "RealEstate",
    "Communication Services": "CommunicationServices",
}


# =============================================================================
# Polygon — daily aggregates
# =============================================================================

def _fetch_daily_bars(ticker: str, days_back: int = 250) -> pd.DataFrame:
    """Fetch daily OHLCV bars from Polygon and return as a DataFrame."""
    if not POLYGON_API_KEY:
        raise RuntimeError("POLYGON_API_KEY not set in environment")

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=days_back)

    url = (
        f"{POLYGON_BASE_URL}/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start_date}/{end_date}"
    )
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
        raise RuntimeError(f"Polygon error for {ticker}: {data}")

    results = data.get("results", [])
    if not results:
        raise RuntimeError(f"No bars returned for {ticker}")

    df = pd.DataFrame(results)
    df["date"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    df = df[["date", "open", "high", "low", "close", "volume"]]
    return df


# =============================================================================
# Technical indicators
# =============================================================================

def _compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """Compute the full technical indicator set from a daily bars DataFrame."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # Trend
    ma5 = SMAIndicator(close, window=5).sma_indicator()
    ma20 = SMAIndicator(close, window=20).sma_indicator()
    ma50 = SMAIndicator(close, window=50).sma_indicator()
    ma200 = SMAIndicator(close, window=200).sma_indicator() if len(close) >= 200 else None
    ema12 = EMAIndicator(close, window=12).ema_indicator()
    ema26 = EMAIndicator(close, window=26).ema_indicator()

    # Momentum
    rsi = RSIIndicator(close, window=14).rsi()
    macd = MACD(close, window_slow=26, window_fast=12, window_sign=9)
    stoch = StochasticOscillator(high, low, close, window=14, smooth_window=3)

    # Volatility
    bb = BollingerBands(close, window=20, window_dev=2)
    atr = AverageTrueRange(high, low, close, window=14)

    # Volume
    obv = OnBalanceVolumeIndicator(close, volume).on_balance_volume()

    # Latest values
    current_price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    change_pct = ((current_price - prev_price) / prev_price) * 100

    ma5_val = float(ma5.iloc[-1])
    ma20_val = float(ma20.iloc[-1])
    ma50_val = float(ma50.iloc[-1])
    ma200_val = float(ma200.iloc[-1]) if ma200 is not None else None

    rsi_val = float(rsi.iloc[-1])
    macd_hist = float(macd.macd_diff().iloc[-1])
    macd_hist_prev = float(macd.macd_diff().iloc[-2])

    atr_val = float(atr.average_true_range().iloc[-1])
    atr_pct = (atr_val / current_price) * 100

    bb_upper = float(bb.bollinger_hband().iloc[-1])
    bb_middle = float(bb.bollinger_mavg().iloc[-1])
    bb_lower = float(bb.bollinger_lband().iloc[-1])

    # Detect signals
    signals = []
    if rsi_val > 70:
        signals.append("RSI overbought")
    elif rsi_val < 30:
        signals.append("RSI oversold")

    if current_price < bb_lower:
        signals.append("Price below lower Bollinger Band")
    elif current_price > bb_upper:
        signals.append("Price above upper Bollinger Band")

    if macd_hist > 0 and macd_hist_prev < 0:
        signals.append("MACD bullish crossover")
    elif macd_hist < 0 and macd_hist_prev > 0:
        signals.append("MACD bearish crossover")

    ma5_prev = float(ma5.iloc[-2])
    ma20_prev = float(ma20.iloc[-2])
    if ma5_prev < ma20_prev and ma5_val > ma20_val:
        signals.append("Golden cross (MA5 x MA20)")
    elif ma5_prev > ma20_prev and ma5_val < ma20_val:
        signals.append("Death cross (MA5 x MA20)")

    if ma200_val is not None:
        if current_price > ma200_val and current_price > ma50_val and ma50_val > ma200_val:
            signals.append("Strong uptrend (price > MA50 > MA200)")
        elif current_price < ma200_val and current_price < ma50_val and ma50_val < ma200_val:
            signals.append("Strong downtrend (price < MA50 < MA200)")

    obv_recent = obv.iloc[-1] - obv.iloc[-5]
    obv_trend = "rising" if obv_recent > 0 else "declining"

    return {
        "price": round(current_price, 2),
        "change_pct": round(change_pct, 2),
        "indicators": {
            "rsi_14": round(rsi_val, 1),
            "macd": {
                "value": round(float(macd.macd().iloc[-1]), 3),
                "signal": round(float(macd.macd_signal().iloc[-1]), 3),
                "histogram": round(macd_hist, 3),
            },
            "ma5": round(ma5_val, 2),
            "ma20": round(ma20_val, 2),
            "ma50": round(ma50_val, 2),
            "ma200": round(ma200_val, 2) if ma200_val is not None else None,
            "ema12": round(float(ema12.iloc[-1]), 2),
            "ema26": round(float(ema26.iloc[-1]), 2),
            "bollinger": {
                "upper": round(bb_upper, 2),
                "middle": round(bb_middle, 2),
                "lower": round(bb_lower, 2),
            },
            "atr_14": round(atr_val, 2),
            "atr_pct": round(atr_pct, 2),
            "obv_trend": obv_trend,
            "stochastic": {
                "k": round(float(stoch.stoch().iloc[-1]), 1),
                "d": round(float(stoch.stoch_signal().iloc[-1]), 1),
            },
            "pct_vs_ma20": round(((current_price - ma20_val) / ma20_val) * 100, 2),
            "pct_vs_ma50": round(((current_price - ma50_val) / ma50_val) * 100, 2),
            "pct_vs_ma200": round(
                ((current_price - ma200_val) / ma200_val) * 100, 2
            ) if ma200_val is not None else None,
        },
        "signals_summary": ", ".join(signals) if signals else "No significant signals",
    }


# =============================================================================
# Fundamentals from yfinance (expanded)
# =============================================================================

def _fetch_fundamentals(ticker: str) -> dict[str, Any]:
    """
    Fetch fundamentals from yfinance, including analyst consensus and
    short interest. Returns dict with None for missing fields.
    """
    yf_ticker = yf.Ticker(ticker)
    info = yf_ticker.info

    raw_sector = info.get("sector")
    mapped_sector = YFINANCE_SECTOR_MAP.get(raw_sector) if raw_sector else None

    current_price = info.get("currentPrice") or info.get("regularMarketPrice") or 0

    # --- Core fundamentals (existing) ---
    fundamentals: dict[str, Any] = {
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "peg_ratio": info.get("pegRatio") or info.get("trailingPegRatio"),
        "profit_margin": info.get("profitMargins"),
        "operating_margin": info.get("operatingMargins"),
        "debt_to_equity": info.get("debtToEquity"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "market_cap": info.get("marketCap"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "raw_sector": raw_sector,
        "sector": mapped_sector,
        "industry": info.get("industry"),
        "average_volume": info.get("averageVolume"),
        "average_volume_10d": info.get("averageDailyVolume10Day"),
    }

    # --- Analyst consensus (NEW) ---
    target_mean = info.get("targetMeanPrice")
    target_high = info.get("targetHighPrice")
    target_low = info.get("targetLowPrice")
    recommendation = info.get("recommendationKey")  # strong_buy, buy, hold, sell
    analyst_count = info.get("numberOfAnalystOpinions")

    analyst_upside_pct = None
    if target_mean and current_price and current_price > 0:
        analyst_upside_pct = round(
            ((target_mean - current_price) / current_price) * 100, 2
        )

    fundamentals["analyst_consensus"] = {
        "target_mean": target_mean,
        "target_high": target_high,
        "target_low": target_low,
        "recommendation": recommendation,
        "analyst_count": analyst_count,
        "upside_pct": analyst_upside_pct,
    }

    # --- Short interest (NEW) ---
    fundamentals["short_interest"] = {
        "short_pct_of_float": info.get("shortPercentOfFloat"),
        "short_ratio": info.get("shortRatio"),  # days to cover
    }

    # --- 52-week position (NEW) ---
    high_52w = info.get("fiftyTwoWeekHigh")
    low_52w = info.get("fiftyTwoWeekLow")
    if high_52w and low_52w and high_52w != low_52w and current_price:
        pct_in_range = round(
            ((current_price - low_52w) / (high_52w - low_52w)) * 100, 1
        )
        pct_from_high = round(
            ((current_price - high_52w) / high_52w) * 100, 2
        )
    else:
        pct_in_range = None
        pct_from_high = None

    fundamentals["price_position"] = {
        "pct_in_52w_range": pct_in_range,
        "pct_from_52w_high": pct_from_high,
    }

    # --- Earnings date (NEW — also closes §7.1 gap) ---
    earnings_date = _fetch_earnings_date(yf_ticker, info)
    fundamentals["next_earnings_date"] = earnings_date

    return fundamentals


def _fetch_earnings_date(yf_ticker, info: dict) -> str | None:
    """
    Extract the next earnings date from yfinance.

    Tries multiple sources since yfinance is inconsistent across tickers.
    Returns ISO date string or None.
    """
    # Method 1: calendar property
    try:
        calendar = yf_ticker.calendar
        if calendar is not None:
            if isinstance(calendar, dict):
                # Newer yfinance versions return a dict
                earnings_date = calendar.get("Earnings Date")
                if earnings_date:
                    if isinstance(earnings_date, list) and len(earnings_date) > 0:
                        return str(earnings_date[0].date()) if hasattr(earnings_date[0], "date") else str(earnings_date[0])
                    elif hasattr(earnings_date, "date"):
                        return str(earnings_date.date())
                    return str(earnings_date)
            elif hasattr(calendar, "iloc"):
                # Older versions return a DataFrame
                if "Earnings Date" in calendar.columns:
                    val = calendar["Earnings Date"].iloc[0]
                    return str(val.date()) if hasattr(val, "date") else str(val)
    except Exception:
        pass

    # Method 2: info dict timestamps
    try:
        ts_start = info.get("earningsTimestampStart")
        ts_end = info.get("earningsTimestampEnd")
        if ts_start:
            return datetime.fromtimestamp(ts_start, tz=timezone.utc).strftime("%Y-%m-%d")
        if ts_end:
            return datetime.fromtimestamp(ts_end, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        pass

    # Method 3: earnings_dates property (list of historical + upcoming)
    try:
        earnings_dates = yf_ticker.earnings_dates
        if earnings_dates is not None and not earnings_dates.empty:
            now = datetime.now(timezone.utc)
            future = earnings_dates[earnings_dates.index >= now]
            if not future.empty:
                return str(future.index[0].date())
    except Exception:
        pass

    return None


# =============================================================================
# Sector benchmarks loading
# =============================================================================

def _load_sector_benchmarks() -> dict[str, Any]:
    """Load sector_benchmarks.yaml and return its content."""
    if not SECTOR_BENCHMARKS_PATH.exists():
        raise FileNotFoundError(
            f"sector_benchmarks.yaml not found at {SECTOR_BENCHMARKS_PATH}"
        )
    with open(SECTOR_BENCHMARKS_PATH) as f:
        return yaml.safe_load(f)


def _get_sector_benchmark(sector: str | None) -> dict[str, Any]:
    """Get the benchmark dict for a given sector, with status info."""
    benchmarks = _load_sector_benchmarks()
    metadata = benchmarks.get("metadata", {})
    last_updated_str = metadata.get("last_updated", "1970-01-01T00:00:00Z")
    last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
    days_old = (datetime.now(timezone.utc) - last_updated).days

    if days_old > 21:
        status = "outdated_critical"
    elif days_old > 14:
        status = "outdated_warning"
    else:
        status = "fresh"

    if sector and sector in benchmarks.get("sectors", {}):
        sector_data = benchmarks["sectors"][sector]
        return {
            "sector": sector,
            "status": status,
            "last_updated_days_ago": days_old,
            "n_components": sector_data.get("n_components"),
            **sector_data.get("metrics", {}),
        }

    return {
        "sector": sector,
        "status": "unknown_sector",
        "last_updated_days_ago": days_old,
        "note": "Sector not found in benchmarks; comparisons unavailable",
    }


# =============================================================================
# Public API
# =============================================================================

def compute_snapshot(
    ticker: str,
    macro: MacroSnapshot | None = None,
    cost_basis: float | None = None,
    shares: float = 0.0,
) -> dict[str, Any]:
    """
    Compute the full snapshot for a ticker.

    This is the canonical input for the Bull/Bear/Judge agents. The returned
    dict contains technical indicators, fundamentals (including analyst
    consensus, short interest, and earnings date), sector benchmarks, and
    macro context — everything the agents need to reason about a position.

    Args:
        ticker: Stock ticker (e.g. "NVDA").
        macro: Optional pre-fetched macro snapshot. If None, will fetch.
        cost_basis: Cost basis per share (for position metrics).
        shares: Number of shares held (for position metrics).

    Returns:
        Dict ready to be JSON-serialized into the agent context.
    """
    if macro is None:
        macro = fetch_macro_snapshot()

    df = _fetch_daily_bars(ticker, days_back=300)
    technicals = _compute_indicators(df)
    fundamentals = _fetch_fundamentals(ticker)
    sector_benchmarks = _get_sector_benchmark(fundamentals.get("sector"))

    snapshot = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "price": technicals["price"],
        "change_pct": technicals["change_pct"],
        "indicators": technicals["indicators"],
        "fundamentals": fundamentals,
        "sector_benchmarks": sector_benchmarks,
        "macro_regime": macro.to_dict(),
        "signals_summary": technicals["signals_summary"],
    }

    # Position-derived metrics (only if a real position with cost basis is provided)
    if cost_basis is not None and shares > 0:
        current_price = technicals["price"]
        current_value = current_price * shares
        cost_total = cost_basis * shares
        unrealized_pnl_usd = current_value - cost_total
        unrealized_pnl_pct = ((current_price - cost_basis) / cost_basis) * 100

        snapshot["position_metrics"] = {
            "cost_basis": round(cost_basis, 2),
            "shares": shares,
            "current_value_usd": round(current_value, 2),
            "cost_basis_total_usd": round(cost_total, 2),
            "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
        }

    return snapshot


def main() -> None:
    """Smoke test: compute snapshot for a ticker passed as CLI arg."""
    import json
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(f"Computing snapshot for {ticker}...")
    print()

    try:
        snapshot = compute_snapshot(ticker)
    except Exception as e:
        print(f"❌ Error: {e}")
        raise SystemExit(1)

    print(json.dumps(snapshot, indent=2, default=str))
    print()
    print(f"✅ Snapshot computed for {ticker}")
    print(f"   Price: ${snapshot['price']} ({snapshot['change_pct']:+.2f}%)")
    print(f"   Sector: {snapshot['fundamentals']['sector']} ({snapshot['fundamentals']['raw_sector']})")
    print(f"   Regime: {snapshot['macro_regime']['regime']}")
    print(f"   Signals: {snapshot['signals_summary']}")

    # Print new fields
    ac = snapshot["fundamentals"].get("analyst_consensus", {})
    if ac.get("target_mean"):
        print(f"   Analyst target: ${ac['target_mean']} ({ac['upside_pct']:+.1f}% upside) "
              f"— {ac['recommendation']} ({ac['analyst_count']} analysts)")

    si = snapshot["fundamentals"].get("short_interest", {})
    if si.get("short_pct_of_float"):
        print(f"   Short interest: {si['short_pct_of_float']:.1%} of float "
              f"(ratio: {si['short_ratio']:.1f} days)")

    pp = snapshot["fundamentals"].get("price_position", {})
    if pp.get("pct_in_52w_range") is not None:
        print(f"   52-week position: {pp['pct_in_52w_range']}% of range "
              f"({pp['pct_from_52w_high']:+.1f}% from high)")

    ed = snapshot["fundamentals"].get("next_earnings_date")
    if ed:
        print(f"   Next earnings: {ed}")


if __name__ == "__main__":
    main()
