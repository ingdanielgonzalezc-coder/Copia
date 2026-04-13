"""
Live price cache — single source of truth for current prices.

Used by the FastAPI portfolio endpoints to enrich position data without
hammering yfinance on every request. Caches prices for 5 minutes by default.

Strategy: when get_live_prices() is called with N tickers, return cached
values for any ticker whose cache is fresh, and batch-fetch the rest in a
single yfinance.download() call (much faster than per-ticker calls).

Usage:
    from src.quotes import get_live_prices

    prices = get_live_prices(["NVDA", "AAPL", "MSFT"])
    # → {"NVDA": 505.12, "AAPL": 180.34, "MSFT": 415.50}
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Iterable

# Module-level cache: ticker → (price, fetched_at_unix_ts)
_price_cache: dict[str, tuple[float, float]] = {}
_cache_lock = Lock()

DEFAULT_TTL_SECONDS = 300  # 5 minutes


def get_live_prices(
    tickers: Iterable[str],
    max_age_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, float]:
    """
    Return current prices for the given tickers, using cached values when fresh.

    Tickers whose cached price is older than max_age_seconds are re-fetched
    in a single batch yfinance call. Tickers that fail to fetch are simply
    omitted from the result (caller should handle missing keys).
    """
    tickers = sorted({t.upper() for t in tickers if t})
    if not tickers:
        return {}

    now = time.time()
    result: dict[str, float] = {}
    stale: list[str] = []

    with _cache_lock:
        for t in tickers:
            cached = _price_cache.get(t)
            if cached and (now - cached[1]) < max_age_seconds:
                result[t] = cached[0]
            else:
                stale.append(t)

    if stale:
        fresh = _fetch_batch(stale)
        with _cache_lock:
            for t, price in fresh.items():
                _price_cache[t] = (price, now)
                result[t] = price

    return result


def _fetch_batch(tickers: list[str]) -> dict[str, float]:
    """Fetch latest closes for a batch of tickers via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        print("⚠️  yfinance not installed — cannot fetch live prices")
        return {}

    out: dict[str, float] = {}

    try:
        # Single call for the whole batch. period=2d to handle weekends/holidays
        # where today's bar may not exist yet.
        data = yf.download(
            tickers=" ".join(tickers),
            period="2d",
            interval="1d",
            progress=False,
            group_by="ticker",
            auto_adjust=False,
            threads=True,
        )

        if data is None or data.empty:
            return out

        if len(tickers) == 1:
            # yfinance returns a flat DataFrame for a single ticker
            t = tickers[0]
            try:
                close = float(data["Close"].dropna().iloc[-1])
                out[t] = close
            except (KeyError, IndexError, ValueError):
                pass
        else:
            # Multi-ticker → MultiIndex columns (ticker, field)
            for t in tickers:
                try:
                    close = float(data[t]["Close"].dropna().iloc[-1])
                    out[t] = close
                except (KeyError, IndexError, ValueError):
                    continue

    except Exception as e:
        print(f"⚠️  yfinance batch fetch failed for {tickers}: {e}")

    return out


def invalidate_cache(ticker: str | None = None) -> None:
    """
    Clear the price cache. Pass a ticker to invalidate just one entry,
    or None to clear everything.
    """
    with _cache_lock:
        if ticker is None:
            _price_cache.clear()
        else:
            _price_cache.pop(ticker.upper(), None)


def get_cache_stats() -> dict:
    """Diagnostic helper — return cache size and oldest entry age."""
    with _cache_lock:
        if not _price_cache:
            return {"entries": 0, "oldest_age_seconds": None}
        now = time.time()
        oldest = max(now - ts for _, ts in _price_cache.values())
        return {
            "entries": len(_price_cache),
            "oldest_age_seconds": round(oldest, 1),
            "tickers": sorted(_price_cache.keys()),
        }
