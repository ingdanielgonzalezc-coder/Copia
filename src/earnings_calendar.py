"""
Earnings calendar — blackout window detection.

Uses the `next_earnings_date` already present in compute_snapshot
(extracted from yfinance) to determine if a ticker is in an earnings
blackout window.

Blackout rules (from config/rules.yaml):
  - before_earnings_days: 3  (no new positions 3 days before earnings)
  - after_earnings_days: 1   (no new positions 1 day after earnings)

During a blackout:
  - BUY_NEW and ADD are removed from allowed_actions
  - HOLD, TRIM, SELL remain available
  - The debate still runs but with restricted actions

Usage:
    from src.earnings_calendar import is_in_blackout, check_blackout

    # From a snapshot (preferred — zero cost)
    blackout = check_blackout(snapshot, rules)
    print(f"Blackout: {blackout.active}")
    print(f"Reason: {blackout.reason}")

    # Standalone check for a ticker
    active = is_in_blackout("NVDA", before_days=3, after_days=1)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import yfinance as yf


@dataclass
class BlackoutStatus:
    """Result of a blackout check."""
    active: bool
    reason: str | None = None
    next_earnings_date: str | None = None
    days_until_earnings: int | None = None
    days_since_earnings: int | None = None


def _parse_date(date_str: str | None) -> date | None:
    """Parse a date string (YYYY-MM-DD) into a date object."""
    if not date_str:
        return None
    try:
        return datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def is_in_blackout(
    ticker: str,
    before_days: int = 3,
    after_days: int = 1,
) -> BlackoutStatus:
    """
    Check if a ticker is in an earnings blackout window.

    Fetches earnings date from yfinance. Use check_blackout() instead
    if you already have a snapshot (avoids redundant API call).

    Args:
        ticker: Stock ticker symbol.
        before_days: Days before earnings to start blackout.
        after_days: Days after earnings to end blackout.

    Returns:
        BlackoutStatus with active flag and details.
    """
    try:
        from src.compute_snapshot import _fetch_earnings_date
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info or {}
        earnings_str = _fetch_earnings_date(yf_ticker, info)
    except Exception as e:
        return BlackoutStatus(
            active=False,
            reason=f"Could not fetch earnings date: {e}",
        )

    return _compute_blackout(
        earnings_date_str=earnings_str,
        before_days=before_days,
        after_days=after_days,
    )


def check_blackout(
    snapshot: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> BlackoutStatus:
    """
    Check blackout status using data already in the snapshot.

    This is the preferred method — zero additional API cost since
    compute_snapshot already fetched the earnings date.

    Args:
        snapshot: Ticker snapshot from compute_snapshot().
        rules: Rules dict (loaded from rules.yaml). If None, uses defaults.

    Returns:
        BlackoutStatus with active flag and details.
    """
    # Extract earnings date from snapshot
    fundamentals = snapshot.get("fundamentals", {})
    earnings_str = fundamentals.get("next_earnings_date")

    # Extract blackout config from rules
    if rules:
        blackouts = rules.get("blackouts", {})
        before_days = blackouts.get("before_earnings_days", 3)
        after_days = blackouts.get("after_earnings_days", 1)
    else:
        before_days = 3
        after_days = 1

    return _compute_blackout(
        earnings_date_str=earnings_str,
        before_days=before_days,
        after_days=after_days,
    )


def _compute_blackout(
    earnings_date_str: str | None,
    before_days: int,
    after_days: int,
) -> BlackoutStatus:
    """
    Core blackout computation.

    Timeline:
        |--- before_days ---|  EARNINGS  |--- after_days ---|
        |<--- BLACKOUT WINDOW (no BUY_NEW / ADD) ---------->|
    """
    earnings_date = _parse_date(earnings_date_str)

    if earnings_date is None:
        return BlackoutStatus(
            active=False,
            reason="No earnings date available",
        )

    today = datetime.now(timezone.utc).date()
    days_until = (earnings_date - today).days

    # Before earnings: blackout starts `before_days` before
    if 0 <= days_until <= before_days:
        return BlackoutStatus(
            active=True,
            reason=f"Earnings in {days_until} day(s) ({earnings_date}). "
                   f"Blackout: no new positions {before_days}d before earnings.",
            next_earnings_date=str(earnings_date),
            days_until_earnings=days_until,
        )

    # Earnings day itself
    if days_until == 0:
        return BlackoutStatus(
            active=True,
            reason=f"Earnings TODAY ({earnings_date}). "
                   f"Blackout: no position changes on earnings day.",
            next_earnings_date=str(earnings_date),
            days_until_earnings=0,
        )

    # After earnings: blackout lasts `after_days` after
    if days_until < 0:
        days_since = abs(days_until)
        if days_since <= after_days:
            return BlackoutStatus(
                active=True,
                reason=f"Earnings was {days_since} day(s) ago ({earnings_date}). "
                       f"Blackout: no new positions {after_days}d after earnings.",
                next_earnings_date=str(earnings_date),
                days_since_earnings=days_since,
            )

    # Not in blackout window
    return BlackoutStatus(
        active=False,
        reason=None,
        next_earnings_date=str(earnings_date),
        days_until_earnings=days_until if days_until >= 0 else None,
    )


def get_blackout_summary(tickers: list[str], rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    Check blackout status for multiple tickers. Useful for portfolio overview.

    Returns list of dicts with ticker, active, reason, next_earnings_date.
    """
    blackouts = rules.get("blackouts", {}) if rules else {}
    before_days = blackouts.get("before_earnings_days", 3)
    after_days = blackouts.get("after_earnings_days", 1)

    results = []
    for ticker in tickers:
        status = is_in_blackout(ticker, before_days, after_days)
        results.append({
            "ticker": ticker,
            "blackout_active": status.active,
            "reason": status.reason,
            "next_earnings_date": status.next_earnings_date,
            "days_until_earnings": status.days_until_earnings,
        })
    return results
