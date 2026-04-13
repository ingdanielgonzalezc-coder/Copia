"""
Portfolio tracker — cash management, allocation computation, watchlist.

Tracks total portfolio value, cash available, and computes real-time
allocations from positions + current prices. Single source of truth for
"how much money do I have and where is it."

Usage:
    from src.portfolio import load_portfolio

    portfolio = load_portfolio()
    print(f"Total: ${portfolio.total_value_usd:,.2f}")
    print(f"Cash: ${portfolio.cash_usd:,.2f}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from src.db import get_client


@dataclass
class PortfolioHolding:
    ticker: str
    shares: float
    cost_basis: float | None
    sector: str | None
    current_value_usd: float = 0.0
    allocation_pct: float = 0.0


@dataclass
class Portfolio:
    total_value_usd: float = 0.0
    cash_usd: float = 0.0
    invested_usd: float = 0.0
    holdings: list[PortfolioHolding] = field(default_factory=list)
    sector_allocations: dict[str, float] = field(default_factory=dict)
    open_positions: int = 0
    cash_pct: float = 0.0

    @property
    def can_open_new_position(self) -> bool:
        from src.rules_engine import load_rules
        rules = load_rules()
        max_pos = rules.get("position_sizing", {}).get("max_open_positions", 12)
        min_pct = rules.get("position_sizing", {}).get("min_position_size_pct", 2.0)
        min_usd = self.total_value_usd * (min_pct / 100)
        return self.open_positions < max_pos and self.cash_usd >= min_usd


def initialize_portfolio(total_usd: float) -> dict[str, Any]:
    """Initialize portfolio with starting cash. Call once at setup."""
    today = datetime.now(timezone.utc).date().isoformat()
    row = {
        "date": today,
        "total_value_usd": total_usd,
        "cash_usd": total_usd,
        "daily_pnl_pct": 0.0,
        "drawdown_from_peak_pct": 0.0,
        "peak_value_usd": total_usd,
        "defensive_mode": False,
        "paused": False,
        "notes": f"Portfolio initialized with ${total_usd:,.2f}",
    }
    try:
        client = get_client()
        response = client.table("portfolio_state_daily").upsert(
            row, on_conflict="date"
        ).execute()
        return response.data[0] if response.data else row
    except Exception as e:
        print(f"⚠️  Failed to persist portfolio state: {e}")
        return row


def load_portfolio() -> Portfolio:
    """Load complete portfolio: state + holdings with computed allocations."""
    try:
        client = get_client()
        state_resp = (
            client.table("portfolio_state_daily")
            .select("*")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        state = state_resp.data[0] if state_resp.data else None
    except Exception:
        state = None

    if not state:
        return Portfolio()

    try:
        client = get_client()
        pos_resp = client.table("positions").select("*").execute()
        pos_rows = pos_resp.data or []
    except Exception:
        pos_rows = []

    total_value = float(state.get("total_value_usd") or 0)
    cash = float(state.get("cash_usd") or 0)

    holdings = []
    invested = 0.0
    sector_allocs: dict[str, float] = {}

    for row in pos_rows:
        shares = float(row.get("shares") or 0)
        if shares <= 0:
            continue
        cost_basis = row.get("cost_basis")
        value = (cost_basis * shares) if cost_basis else 0
        alloc = (value / total_value * 100) if total_value > 0 else 0
        invested += value

        h = PortfolioHolding(
            ticker=row["ticker"],
            shares=shares,
            cost_basis=cost_basis,
            sector=row.get("sector"),
            current_value_usd=value,
            allocation_pct=round(alloc, 2),
        )
        holdings.append(h)

        if h.sector:
            sector_allocs[h.sector] = sector_allocs.get(h.sector, 0) + alloc

    return Portfolio(
        total_value_usd=total_value,
        cash_usd=cash,
        invested_usd=invested,
        holdings=holdings,
        sector_allocations={k: round(v, 2) for k, v in sector_allocs.items()},
        open_positions=len(holdings),
        cash_pct=round(cash / total_value * 100, 2) if total_value > 0 else 100,
    )


def update_cash(action: str, total_value: float, cash: float, alloc_pct: float) -> float:
    """Compute new cash after a trade. Returns new cash amount."""
    trade_value = total_value * (abs(alloc_pct) / 100)
    if action in ("BUY_NEW", "BUY", "ADD"):
        return max(0, cash - trade_value)
    elif action in ("SELL", "TRIM"):
        return cash + trade_value
    return cash


def save_portfolio_state(total_value: float, cash: float, notes: str = "") -> None:
    """Save/update today's portfolio state."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        client = get_client()
        # Get peak for drawdown calc
        prev = (
            client.table("portfolio_state_daily")
            .select("peak_value_usd")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        peak = float(prev.data[0]["peak_value_usd"]) if prev.data else total_value
        peak = max(peak, total_value)
        drawdown = ((total_value - peak) / peak * 100) if peak > 0 else 0

        client.table("portfolio_state_daily").upsert({
            "date": today,
            "total_value_usd": total_value,
            "cash_usd": cash,
            "peak_value_usd": peak,
            "drawdown_from_peak_pct": round(drawdown, 4),
            "notes": notes,
        }, on_conflict="date").execute()
    except Exception as e:
        print(f"⚠️  Failed to save portfolio state: {e}")


# =============================================================================
# Watchlist management
# =============================================================================

def get_watchlist_tickers() -> list[str]:
    """Get tickers currently on the watchlist."""
    try:
        client = get_client()
        response = (
            client.table("discovery_candidates")
            .select("ticker")
            .eq("user_decision", "watchlist")
            .execute()
        )
        return list(set(r["ticker"] for r in (response.data or [])))
    except Exception as e:
        print(f"⚠️  Failed to load watchlist: {e}")
        return []


def save_candidates(candidates: list[dict], status: str = "watchlist") -> int:
    """Save screener candidates to discovery_candidates. Returns count saved."""
    if not candidates:
        return 0
    rows = [{
        "ticker": c["ticker"],
        "setup_type": c.get("setup_type"),
        "screener_score": int(c["score"]) if c.get("score") is not None else None,
        "snapshot_summary": c.get("summary"),
        "user_decision": status,
    } for c in candidates]

    try:
        client = get_client()
        response = client.table("discovery_candidates").insert(rows).execute()
        return len(response.data) if response.data else 0
    except Exception as e:
        print(f"⚠️  Failed to save candidates: {e}")
        return 0


def promote_from_watchlist(ticker: str, debate_id: str, verdict: str) -> None:
    """Update a watchlist candidate after it's been debated."""
    try:
        client = get_client()
        new_status = "promoted" if verdict in ("BUY_NEW",) else "rejected"
        client.table("discovery_candidates").update({
            "debate_id": debate_id,
            "final_verdict": verdict,
            "user_decision": new_status,
            "user_decision_at": datetime.now(timezone.utc).isoformat(),
        }).eq("ticker", ticker).eq("user_decision", "watchlist").execute()
    except Exception as e:
        print(f"⚠️  Failed to update watchlist for {ticker}: {e}")


def clear_old_watchlist(days_old: int = 14) -> int:
    """Remove watchlist entries older than N days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    try:
        client = get_client()
        response = (
            client.table("discovery_candidates")
            .delete()
            .eq("user_decision", "watchlist")
            .lt("scan_timestamp", cutoff)
            .execute()
        )
        return len(response.data) if response.data else 0
    except Exception as e:
        print(f"⚠️  Failed to clear old watchlist: {e}")
        return 0
# =============================================================================
# Position lots — FIFO CRUD (added in migration 002)
# =============================================================================
# These functions operate on the new `position_lots` and `realized_trades`
# tables. The legacy `positions` table is left untouched for backwards
# compatibility with the debate engine and rules engine — those modules
# will be migrated to read from the position_summary view in a later step.
#
# All functions are best-effort: DB failures are logged but never raised.

from datetime import datetime, timezone, date
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_lot(
    ticker: str,
    shares: float,
    buy_price: float,
    purchased_at: str | None = None,
    sector: str | None = None,
    stop_loss_price: float | None = None,
    stop_win_price: float | None = None,
    notes: str | None = None,
) -> dict[str, Any] | None:
    """
    Create a new lot for a ticker. Used both for opening a new position and
    for adding to an existing one (each buy = new lot, FIFO ordering).

    Returns the created lot row (with id) or None on failure.
    """
    if shares <= 0:
        raise ValueError(f"shares must be > 0, got {shares}")
    if buy_price <= 0:
        raise ValueError(f"buy_price must be > 0, got {buy_price}")

    row = {
        "ticker": ticker.upper(),
        "shares": shares,
        "buy_price": buy_price,
        "purchased_at": purchased_at or _now_iso(),
        "sector": sector,
        "stop_loss_price": stop_loss_price,
        "stop_win_price": stop_win_price,
        "notes": notes,
    }

    try:
        client = get_client()
        response = client.table("position_lots").insert(row).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"⚠️  Failed to create lot for {ticker}: {e}")
        return None


def get_active_lots(ticker: str) -> list[dict[str, Any]]:
    """
    Return all active (closed_at IS NULL) lots for a ticker, ordered FIFO
    (oldest first by purchased_at).
    """
    try:
        client = get_client()
        response = (
            client.table("position_lots")
            .select("*")
            .eq("ticker", ticker.upper())
            .is_("closed_at", "null")
            .order("purchased_at", desc=False)
            .execute()
        )
        return response.data or []
    except Exception as e:
        print(f"⚠️  Failed to load lots for {ticker}: {e}")
        return []


def get_position_summary(ticker: str) -> dict[str, Any] | None:
    """
    Return aggregated summary of active lots for a ticker (total shares,
    weighted-avg cost basis, lots count, realized P&L). Reads from the
    position_summary view defined in migration 002.
    """
    try:
        client = get_client()
        response = (
            client.table("position_summary")
            .select("*")
            .eq("ticker", ticker.upper())
            .limit(1)
            .execute()
        )
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"⚠️  Failed to load summary for {ticker}: {e}")
        return None


def get_all_position_summaries() -> list[dict[str, Any]]:
    """Return summary rows for every ticker with at least one active lot."""
    try:
        client = get_client()
        response = client.table("position_summary").select("*").execute()
        return response.data or []
    except Exception as e:
        print(f"⚠️  Failed to load position summaries: {e}")
        return []


def sell_shares_fifo(
    ticker: str,
    shares_to_sell: float,
    sell_price: float,
    sold_at: str | None = None,
    sell_reason: str = "partial",
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Sell shares of a ticker, consuming active lots in FIFO order.

    For each lot consumed (fully or partially), creates a realized_trades
    row recording the buy price, sell price, P&L, and holding period.
    Lots that are fully consumed are marked closed_at=NOW(). Lots that are
    partially consumed have their `shares` reduced.

    Returns:
        {
            "ticker": str,
            "shares_sold": float,
            "shares_remaining_in_position": float,
            "realized_pnl_usd": float,
            "realized_pnl_pct": float,
            "trades": [list of realized_trade rows created],
            "lots_closed": int,
            "lots_partially_sold": int,
            "error": str | None,
        }

    Raises ValueError if shares_to_sell exceeds total active shares.
    """
    if shares_to_sell <= 0:
        raise ValueError(f"shares_to_sell must be > 0, got {shares_to_sell}")
    if sell_price <= 0:
        raise ValueError(f"sell_price must be > 0, got {sell_price}")

    sold_at = sold_at or _now_iso()
    lots = get_active_lots(ticker)

    total_active = sum(float(l["shares"]) for l in lots)
    if shares_to_sell > total_active + 1e-9:
        raise ValueError(
            f"Cannot sell {shares_to_sell} shares of {ticker}: only {total_active} active"
        )

    client = get_client()
    remaining_to_sell = shares_to_sell
    total_pnl_usd = 0.0
    total_cost_basis_consumed = 0.0
    trades_created: list[dict[str, Any]] = []
    lots_closed = 0
    lots_partial = 0

    for lot in lots:
        if remaining_to_sell <= 1e-9:
            break

        lot_shares = float(lot["shares"])
        lot_buy_price = float(lot["buy_price"])
        consume = min(lot_shares, remaining_to_sell)

        pnl_usd = round((sell_price - lot_buy_price) * consume, 4)
        pnl_pct = round((sell_price - lot_buy_price) / lot_buy_price * 100, 4)

        # Holding days
        holding_days = None
        try:
            purchased = datetime.fromisoformat(
                str(lot["purchased_at"]).replace("Z", "+00:00")
            )
            sold = datetime.fromisoformat(sold_at.replace("Z", "+00:00"))
            holding_days = (sold.date() - purchased.date()).days
        except Exception:
            pass

        # Record realized trade
        trade_row = {
            "ticker": ticker.upper(),
            "lot_id": lot["id"],
            "shares_sold": consume,
            "buy_price": lot_buy_price,
            "sell_price": sell_price,
            "pnl_usd": pnl_usd,
            "pnl_pct": pnl_pct,
            "holding_days": holding_days,
            "sold_at": sold_at,
            "sell_reason": sell_reason,
            "notes": notes,
        }
        try:
            tr_resp = client.table("realized_trades").insert(trade_row).execute()
            if tr_resp.data:
                trades_created.append(tr_resp.data[0])
        except Exception as e:
            print(f"⚠️  Failed to record realized trade for lot {lot['id']}: {e}")

        # Update or close the lot
        new_shares = round(lot_shares - consume, 6)
        if new_shares <= 1e-9:
            # Fully consumed → close
            try:
                resp = client.table("position_lots").update({
                    "shares": 0,
                    "closed_at": sold_at,
                    "close_reason": sell_reason if sell_reason != "partial" else "sold",
                }).eq("id", lot["id"]).execute()
                if resp.data:
                    lots_closed += 1
                else:
                    print(f"⚠️  Close update returned no data for lot {lot['id']}")
            except Exception as e:
                print(f"❌ Failed to close lot {lot['id']}: {e}")
                raise  # propagate so the API returns 500 instead of pretending success
        else:
            # Partially consumed → reduce shares
            try:
                client.table("position_lots").update({
                    "shares": new_shares,
                }).eq("id", lot["id"]).execute()
                lots_partial += 1
            except Exception as e:
                print(f"⚠️  Failed to reduce lot {lot['id']}: {e}")

        total_pnl_usd += pnl_usd
        total_cost_basis_consumed += lot_buy_price * consume
        remaining_to_sell -= consume

    realized_pnl_pct = (
        round(total_pnl_usd / total_cost_basis_consumed * 100, 4)
        if total_cost_basis_consumed > 0 else 0.0
    )
    shares_remaining = round(total_active - shares_to_sell, 6)

    return {
        "ticker": ticker.upper(),
        "shares_sold": shares_to_sell,
        "shares_remaining_in_position": shares_remaining,
        "realized_pnl_usd": round(total_pnl_usd, 4),
        "realized_pnl_pct": realized_pnl_pct,
        "trades": trades_created,
        "lots_closed": lots_closed,
        "lots_partially_sold": lots_partial,
        "error": None,
    }


def close_position_lots(
    ticker: str,
    sell_price: float,
    sold_at: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Close all active lots for a ticker at the given sell price. Convenience
    wrapper around sell_shares_fifo() for the "close position" UI action.
    """
    lots = get_active_lots(ticker)
    total_shares = sum(float(l["shares"]) for l in lots)
    if total_shares <= 0:
        return {
            "ticker": ticker.upper(),
            "shares_sold": 0,
            "shares_remaining_in_position": 0,
            "realized_pnl_usd": 0,
            "realized_pnl_pct": 0,
            "trades": [],
            "lots_closed": 0,
            "lots_partially_sold": 0,
            "error": "no_active_lots",
        }
    return sell_shares_fifo(
        ticker=ticker,
        shares_to_sell=total_shares,
        sell_price=sell_price,
        sold_at=sold_at,
        sell_reason="close_position",
        notes=notes,
    )


def delete_position_lots(ticker: str) -> dict[str, Any]:
    """
    HARD delete all lots and realized trades for a ticker. For error
    correction only — does NOT touch cash, on the assumption that the
    position was loaded by mistake and never actually existed.

    Returns counts of deleted rows.
    """
    try:
        client = get_client()
        # realized_trades cascade-delete via FK, but we can also count first
        rt_resp = (
            client.table("realized_trades")
            .select("id")
            .eq("ticker", ticker.upper())
            .execute()
        )
        rt_count = len(rt_resp.data or [])

        # Delete realized_trades first explicitly (in case CASCADE is missing)
        client.table("realized_trades").delete().eq("ticker", ticker.upper()).execute()

        # Then delete the lots
        lot_resp = (
            client.table("position_lots")
            .delete()
            .eq("ticker", ticker.upper())
            .execute()
        )
        lot_count = len(lot_resp.data or [])

        return {
            "ticker": ticker.upper(),
            "lots_deleted": lot_count,
            "realized_trades_deleted": rt_count,
        }
    except Exception as e:
        print(f"⚠️  Failed to delete lots for {ticker}: {e}")
        return {"ticker": ticker.upper(), "error": str(e)}


def apply_manual_trade_to_cash(
    action: str,
    shares: float,
    price: float,
    current_cash: float,
) -> float:
    """
    Compute new cash balance after a manual trade.

    For BUY: cash decreases by (shares * price).
    For SELL/CLOSE: cash increases by (shares * price).

    This is the manual-trade equivalent of update_cash() above, which
    operates on allocation percentages (used by debate-driven trades).
    """
    trade_value = shares * price
    if action.upper() in ("BUY", "BUY_NEW", "ADD"):
        return max(0.0, current_cash - trade_value)
    elif action.upper() in ("SELL", "TRIM", "CLOSE"):
        return current_cash + trade_value
    return current_cash


# =============================================================================
# Live-price enrichment for the FastAPI portfolio endpoints
# =============================================================================

def enrich_summaries_with_live_prices(
    summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Take a list of position_summary rows and enrich each with current_price,
    current_value_usd, and unrealized P&L computed from a batch yfinance
    fetch (cached for 5 minutes).

    Each input row needs at minimum: ticker, total_shares, avg_cost_basis.
    Returns a new list (does not mutate input).
    """
    if not summaries:
        return []

    from src.quotes import get_live_prices

    tickers = [s["ticker"] for s in summaries if s.get("ticker")]
    prices = get_live_prices(tickers)

    enriched: list[dict[str, Any]] = []
    for s in summaries:
        ticker = s.get("ticker")
        shares = float(s.get("total_shares") or 0)
        avg_cost = float(s.get("avg_cost_basis") or 0)
        current_price = prices.get(ticker)

        row = dict(s)
        if current_price is not None:
            current_value = round(current_price * shares, 2)
            invested = round(avg_cost * shares, 2)
            unrealized_pnl_usd = round(current_value - invested, 2)
            unrealized_pnl_pct = (
                round((current_price - avg_cost) / avg_cost * 100, 4)
                if avg_cost > 0 else 0.0
            )
            row.update({
                "current_price": current_price,
                "current_value_usd": current_value,
                "unrealized_pnl_usd": unrealized_pnl_usd,
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "price_source": "yfinance",
            })
        else:
            # Price unavailable — fall back to cost basis
            row.update({
                "current_price": None,
                "current_value_usd": round(avg_cost * shares, 2),
                "unrealized_pnl_usd": 0,
                "unrealized_pnl_pct": 0,
                "price_source": "unavailable",
            })

        enriched.append(row)

    return enriched
