"""
Refresh sector_benchmarks.yaml from yfinance data.

Per v3.1 Delta 1 + Patch A, this script:
  - Fetches components for each sector ETF (XLK, XLF, etc.)
  - Pulls fundamentals from yfinance for each component
  - Computes median + p25 + p75 for each metric per sector
  - Validates percentile coherence (p25 <= median <= p75)
  - Falls back to previous values if >30% of components fail
  - Excludes negative P/E and small caps that distort medians

Run weekly via cron:
    uv run python -m scripts.refresh_sector_benchmarks
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import yaml
import yfinance as yf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECTOR_BENCHMARKS_PATH = PROJECT_ROOT / "config" / "sector_benchmarks.yaml"

# Sector ETFs with hand-curated component lists.
# yfinance does not reliably return ETF holdings, so we use known constituents.
# This list is intentionally conservative — top ~20-30 holdings per sector are
# enough to get robust medians for the sector.
SECTOR_ETFS: dict[str, dict[str, Any]] = {
    "Technology": {
        "etf_proxy": "XLK",
        "components": [
            "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE",
            "ACN", "CSCO", "QCOM", "TXN", "INTU", "AMAT", "IBM", "NOW",
            "MU", "LRCX", "ADI", "PANW", "KLAC", "SNPS", "CDNS", "FTNT",
            "ANET", "ROP", "MSI", "APH", "GLW", "MCHP",
        ],
    },
    "Financials": {
        "etf_proxy": "XLF",
        "components": [
            "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP",
            "BLK", "C", "SPGI", "PGR", "MMC", "CB", "SCHW", "FI", "BX",
            "TFC", "USB", "PNC", "AON", "CME", "ICE", "COF",
        ],
    },
    "Healthcare": {
        "etf_proxy": "XLV",
        "components": [
            "LLY", "UNH", "JNJ", "MRK", "ABBV", "PFE", "TMO", "ABT", "DHR",
            "AMGN", "ISRG", "GILD", "MDT", "CVS", "BMY", "ELV", "VRTX",
            "REGN", "BSX", "SYK", "ZTS", "CI", "BDX", "MCK", "HCA",
        ],
    },
    "ConsumerDiscretionary": {
        "etf_proxy": "XLY",
        "components": [
            "AMZN", "TSLA", "HD", "MCD", "LOW", "BKNG", "TJX", "NKE", "SBUX",
            "CMG", "ABNB", "ORLY", "AZO", "MAR", "HLT", "GM", "F", "ROST",
            "DHI", "LEN", "YUM", "RCL",
        ],
    },
    "ConsumerStaples": {
        "etf_proxy": "XLP",
        "components": [
            "WMT", "PG", "COST", "KO", "PEP", "PM", "MDLZ", "CL", "TGT",
            "MO", "KMB", "EL", "GIS", "STZ", "SYY", "KR", "ADM", "KDP",
            "HSY", "MNST",
        ],
    },
    "Energy": {
        "etf_proxy": "XLE",
        "components": [
            "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "OXY", "VLO",
            "PXD", "WMB", "OKE", "KMI", "HES", "FANG", "DVN", "BKR", "HAL",
        ],
    },
    "Industrials": {
        "etf_proxy": "XLI",
        "components": [
            "GE", "CAT", "RTX", "HON", "UNP", "BA", "ETN", "LMT", "DE",
            "UPS", "ADP", "WM", "ITW", "EMR", "PH", "GD", "NOC", "FDX",
            "CSX", "NSC", "MMM", "JCI", "CMI", "ROK", "CARR",
        ],
    },
    "Materials": {
        "etf_proxy": "XLB",
        "components": [
            "LIN", "SHW", "APD", "ECL", "FCX", "NEM", "DOW", "DD", "CTVA",
            "PPG", "NUE", "VMC", "MLM", "STLD", "BALL", "IFF", "PKG", "ALB",
        ],
    },
    "Utilities": {
        "etf_proxy": "XLU",
        "components": [
            "NEE", "SO", "DUK", "CEG", "AEP", "SRE", "D", "PCG", "EXC",
            "XEL", "PEG", "ED", "WEC", "AWK", "ES", "DTE", "FE", "ETR",
            "AEE", "PPL",
        ],
    },
    "RealEstate": {
        "etf_proxy": "XLRE",
        "components": [
            "PLD", "AMT", "EQIX", "WELL", "PSA", "SPG", "O", "DLR", "CCI",
            "CBRE", "AVB", "EXR", "VICI", "EQR", "WY", "INVH", "SBAC", "ARE",
            "MAA", "ESS",
        ],
    },
    "CommunicationServices": {
        "etf_proxy": "XLC",
        "components": [
            "GOOGL", "GOOG", "META", "NFLX", "DIS", "TMUS", "VZ", "T",
            "CMCSA", "EA", "CHTR", "TTWO", "WBD", "OMC", "PARA", "FOXA",
            "IPG", "LYV", "MTCH", "NWS",
        ],
    },
}

# Metrics to compute and their yfinance keys
METRICS_MAP = {
    "pe_ratio": "trailingPE",
    "forward_pe": "forwardPE",
    "peg_ratio": "trailingPegRatio",
    "profit_margin": "profitMargins",
    "operating_margin": "operatingMargins",
    "debt_to_equity": "debtToEquity",
    "revenue_growth": "revenueGrowth",
}

# Quality filters — exclude components that would distort the median
MIN_MARKET_CAP_USD = 1_000_000_000

# Failure threshold per Patch A
MAX_FAIL_RATE = 0.30


def fetch_component_fundamentals(ticker: str) -> dict[str, Any] | None:
    """Fetch fundamentals for a single ticker. Returns None on failure."""
    try:
        info = yf.Ticker(ticker).info
        if not info:
            return None

        market_cap = info.get("marketCap", 0)
        if market_cap < MIN_MARKET_CAP_USD:
            return None

        result = {}
        for our_key, yf_key in METRICS_MAP.items():
            value = info.get(yf_key)
            # Filter out negative P/E (distorts median) and other invalid values
            if our_key in ("pe_ratio", "forward_pe") and value is not None and value < 0:
                value = None
            result[our_key] = value

        # Require at least half the metrics to be present
        valid_count = sum(1 for v in result.values() if v is not None)
        if valid_count < len(METRICS_MAP) / 2:
            return None

        return result
    except Exception as e:
        print(f"   ⚠️  {ticker}: {type(e).__name__}: {e}")
        return None


def compute_quartiles(fundamentals_list: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Compute median, p25, p75 for each metric."""
    metrics = {}
    for metric_name in METRICS_MAP.keys():
        values = [
            f[metric_name]
            for f in fundamentals_list
            if f.get(metric_name) is not None
        ]
        if len(values) < 5:  # need minimum sample size
            metrics[metric_name] = None
            continue

        arr = np.array(values, dtype=float)
        # Remove extreme outliers (>5 std devs from mean)
        if len(arr) > 10:
            mean = arr.mean()
            std = arr.std()
            arr = arr[np.abs(arr - mean) < 5 * std]

        if len(arr) < 5:
            metrics[metric_name] = None
            continue

        median = float(np.median(arr))
        p25 = float(np.percentile(arr, 25))
        p75 = float(np.percentile(arr, 75))

        # Patch A: validate percentile coherence
        if not (p25 <= median <= p75):
            print(f"   ⚠️  {metric_name}: incoherent percentiles, discarding")
            metrics[metric_name] = None
            continue

        metrics[metric_name] = {
            "median": round(median, 4),
            "p25": round(p25, 4),
            "p75": round(p75, 4),
        }

    return metrics


def refresh_sector(
    sector_name: str,
    sector_config: dict[str, Any],
    previous_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Refresh a single sector. Falls back to previous data on excessive failures."""
    components = sector_config["components"]
    print(f"\n📊 {sector_name} ({sector_config['etf_proxy']}) — {len(components)} components")

    fundamentals_list = []
    failures = 0
    for ticker in components:
        result = fetch_component_fundamentals(ticker)
        if result is None:
            failures += 1
        else:
            fundamentals_list.append(result)

    fail_rate = failures / len(components)
    print(f"   ✓ {len(fundamentals_list)} succeeded, {failures} failed ({fail_rate:.0%})")

    if fail_rate > MAX_FAIL_RATE:
        print(f"   ⚠️  Fail rate {fail_rate:.0%} > {MAX_FAIL_RATE:.0%}, keeping previous data")
        if previous_data is not None:
            return previous_data
        else:
            print(f"   ❌ No previous data either; sector will be empty")
            return {
                "etf_proxy": sector_config["etf_proxy"],
                "n_components": 0,
                "n_failures": failures,
                "metrics": {},
            }

    metrics = compute_quartiles(fundamentals_list)

    # Patch A: merge with previous metrics where new ones failed
    if previous_data is not None:
        prev_metrics = previous_data.get("metrics", {})
        for metric_name, value in metrics.items():
            if value is None and metric_name in prev_metrics:
                print(f"   ↩️  {metric_name}: using previous value (new computation failed)")
                metrics[metric_name] = prev_metrics[metric_name]

    # Drop None values
    metrics = {k: v for k, v in metrics.items() if v is not None}

    return {
        "etf_proxy": sector_config["etf_proxy"],
        "n_components": len(fundamentals_list),
        "n_failures": failures,
        "metrics": metrics,
    }


def main() -> None:
    """Refresh all sector benchmarks and write the YAML file."""
    print("=" * 70)
    print("Refreshing sector benchmarks")
    print("=" * 70)

    # Load previous data for fallback
    previous = None
    if SECTOR_BENCHMARKS_PATH.exists():
        with open(SECTOR_BENCHMARKS_PATH) as f:
            previous = yaml.safe_load(f)

    new_sectors = {}
    for sector_name, sector_config in SECTOR_ETFS.items():
        prev_sector = None
        if previous and sector_name in previous.get("sectors", {}):
            prev_sector = previous["sectors"][sector_name]
        new_sectors[sector_name] = refresh_sector(sector_name, sector_config, prev_sector)

    output = {
        "metadata": {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source": "yfinance via refresh_sector_benchmarks.py",
            "refresh_cadence": "weekly (Sunday 08:00 UTC)",
            "status": "fresh",
        },
        "sectors": new_sectors,
    }

    with open(SECTOR_BENCHMARKS_PATH, "w") as f:
        yaml.dump(output, f, sort_keys=False, default_flow_style=False)

    print()
    print("=" * 70)
    print(f"✅ Wrote {SECTOR_BENCHMARKS_PATH}")
    print(f"   {len(new_sectors)} sectors refreshed")
    print("=" * 70)


if __name__ == "__main__":
    main()
