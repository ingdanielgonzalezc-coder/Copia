"""
Macro context module — 4-regime market classifier.

Fetches SPY and VIX data from Polygon.io and classifies the current
market regime into one of: BULL, NEUTRAL, HIGH_VOLATILITY, BEAR.

The 4 regimes (per v3.1 Delta 2):
    BULL:            SPY > MA200 AND VIX < 20
    NEUTRAL:         SPY > MA200 AND 20 <= VIX <= 25
    HIGH_VOLATILITY: SPY > MA200 AND VIX > 25  (fragile bull)
    BEAR:            SPY < MA200                (VIX irrelevant)

Run as a script to verify Polygon credentials and see the current regime:
    uv run python -m src.macro_context
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from dotenv import load_dotenv

load_dotenv()

Regime = Literal["BULL", "NEUTRAL", "HIGH_VOLATILITY", "BEAR"]

POLYGON_BASE_URL = "https://api.polygon.io"
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

# VIX thresholds
VIX_LOW = 20.0
VIX_HIGH = 25.0


@dataclass(frozen=True)
class MacroSnapshot:
    """Point-in-time macro context for the debate engine."""

    timestamp: datetime
    spy_price: float
    spy_ma200: float
    spy_ma50: float
    spy_vs_ma200_pct: float
    vix_level: float
    regime: Regime
    description: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "spy_price": round(self.spy_price, 2),
            "spy_ma200": round(self.spy_ma200, 2),
            "spy_ma50": round(self.spy_ma50, 2),
            "spy_vs_ma200_pct": round(self.spy_vs_ma200_pct, 2),
            "spy_above_ma200": self.spy_price > self.spy_ma200,
            "spy_above_ma50": self.spy_price > self.spy_ma50,
            "vix_level": round(self.vix_level, 2),
            "regime": self.regime,
            "description": self.description,
        }


def classify_regime(spy_price: float, spy_ma200: float, vix: float) -> Regime:
    """
    Canonical 4-state regime classifier per v3.1 Delta 2.

    Order matters: SPY < MA200 is BEAR regardless of VIX.
    """
    if spy_price < spy_ma200:
        return "BEAR"
    if vix > VIX_HIGH:
        return "HIGH_VOLATILITY"
    if vix < VIX_LOW:
        return "BULL"
    return "NEUTRAL"


def regime_description(regime: Regime, spy_vs_ma200_pct: float, vix: float) -> str:
    """Human-readable regime description for logs and prompts."""
    descriptions = {
        "BULL": (
            f"Bull market. SPY {spy_vs_ma200_pct:+.1f}% above MA200, VIX {vix:.1f} (low). "
            f"Trend intact, low volatility. LLM bias: counter-conservatism."
        ),
        "NEUTRAL": (
            f"Neutral. SPY {spy_vs_ma200_pct:+.1f}% above MA200, VIX {vix:.1f} (moderate). "
            f"Trend intact, elevated but contained volatility."
        ),
        "HIGH_VOLATILITY": (
            f"Fragile bull. SPY {spy_vs_ma200_pct:+.1f}% above MA200 but VIX {vix:.1f} > 25. "
            f"Wider stops mandatory, wash-out risk."
        ),
        "BEAR": (
            f"Bear market. SPY {spy_vs_ma200_pct:+.1f}% below MA200, VIX {vix:.1f}. "
            f"Wider stops, prefer TRIM over SELL, beware bull traps."
        ),
    }
    return descriptions[regime]


def _fetch_polygon_aggregates(ticker: str, days_back: int = 250) -> list[dict]:
    """Fetch daily OHLCV bars from Polygon for the given ticker."""
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

    return data.get("results", [])


def _moving_average(closes: list[float], window: int) -> float:
    """Simple moving average of the last `window` closes."""
    if len(closes) < window:
        raise ValueError(f"Not enough data: need {window}, have {len(closes)}")
    return sum(closes[-window:]) / window


def _fetch_vix_level() -> float:
    """
    Fetch current VIX spot level using yfinance (^VIX).

    Polygon's free/Starter plans don't include real index data, and using
    VIXY as a proxy is unreliable (the conversion factor varies with
    contango). yfinance gives us the actual VIX spot for free.
    """
    import yfinance as yf

    vix_ticker = yf.Ticker("^VIX")
    hist = vix_ticker.history(period="5d")

    if hist.empty:
        raise RuntimeError("No VIX data returned from yfinance")

    latest_vix = float(hist["Close"].iloc[-1])

    # Sanity bounds
    if not (5.0 <= latest_vix <= 100.0):
        raise RuntimeError(f"VIX value out of expected range: {latest_vix}")

    return latest_vix


def fetch_macro_snapshot() -> MacroSnapshot:
    """
    Fetch the current macro snapshot.

    Pulls SPY daily bars (250 days for MA200 calculation) and VIX level,
    then classifies the regime.
    """
    spy_bars = _fetch_polygon_aggregates("SPY", days_back=300)
    if len(spy_bars) < 200:
        raise RuntimeError(f"Insufficient SPY history: {len(spy_bars)} bars")

    closes = [bar["c"] for bar in spy_bars]
    spy_price = closes[-1]
    spy_ma200 = _moving_average(closes, 200)
    spy_ma50 = _moving_average(closes, 50)
    spy_vs_ma200_pct = ((spy_price - spy_ma200) / spy_ma200) * 100

    vix_level = _fetch_vix_level()
    regime = classify_regime(spy_price, spy_ma200, vix_level)
    description = regime_description(regime, spy_vs_ma200_pct, vix_level)

    return MacroSnapshot(
        timestamp=datetime.now(timezone.utc),
        spy_price=spy_price,
        spy_ma200=spy_ma200,
        spy_ma50=spy_ma50,
        spy_vs_ma200_pct=spy_vs_ma200_pct,
        vix_level=vix_level,
        regime=regime,
        description=description,
    )


def main() -> None:
    """Smoke test: fetch and print the current macro snapshot."""
    import json

    print("Fetching macro snapshot from Polygon...")
    print()

    try:
        snapshot = fetch_macro_snapshot()
    except Exception as e:
        print(f"❌ Error: {e}")
        print()
        print("Common causes:")
        print("  - POLYGON_API_KEY not set in .env")
        print("  - Polygon plan does not include daily aggregates (need Basic+)")
        print("  - Network issue")
        raise SystemExit(1)

    print("✅ Macro snapshot fetched successfully")
    print()
    print(json.dumps(snapshot.to_dict(), indent=2))
    print()
    print(f"REGIME: {snapshot.regime}")
    print(f"  → {snapshot.description}")


if __name__ == "__main__":
    main()
