"""
FastAPI app — REST API for the stock advisor system.

Endpoints:
  Portfolio:
    GET  /portfolio              — current portfolio state
    GET  /portfolio/positions    — positions with details
    GET  /portfolio/watchlist    — watchlist tickers

  Debates:
    GET  /debates/recent         — recent debates (last N)
    GET  /debates/{debate_id}    — single debate detail
    POST /debates/trigger        — trigger a debate for a ticker

  Cycles:
    POST /cycles/intraday        — run one intraday cycle
    POST /cycles/eod             — run EOD review (single ticker or all)

  Data:
    GET  /earnings/blackout      — blackout status for portfolio
    GET  /stats                  — hit rates and cost summary
    GET  /health                 — health check

Auth: API key via X-API-Key header (set ADVISOR_API_KEY env var).

Usage:
    # Local
    uv run uvicorn api.main:app --reload --port 8000

    # With scheduler on Railway (future integration)
    See scripts/scheduler.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

load_dotenv()

# =============================================================================
# App setup
# =============================================================================

app = FastAPI(
    title="Stock Advisor API",
    version="0.1.0",
    description="Multi-agent stock advisory system — Bull / Bear / Judge",
)

# Auth
API_KEY = os.getenv("ADVISOR_API_KEY", "")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(key: str | None = Security(api_key_header)) -> str:
    """Validate API key. Skip if ADVISOR_API_KEY not set (dev mode)."""
    if not API_KEY:
        return "dev"  # no auth in dev mode
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return key


# =============================================================================
# Request/Response models
# =============================================================================

class DebateTriggerRequest(BaseModel):
    ticker: str = Field(..., description="Stock ticker (e.g. NVDA)")
    phase: str = Field("INITIAL", description="INITIAL, INTRADAY, or EOD")
    shares: float = Field(0, description="Current shares held (0 for new)")
    cost_basis: float | None = Field(None, description="Average cost per share")
    allocation_pct: float = Field(0, description="Current allocation %")


class CycleRequest(BaseModel):
    dry_run: bool = Field(False, description="Run without executing debates")
    cost_cap: float = Field(3.0, description="Max USD to spend")


class EODRequest(CycleRequest):
    ticker: str | None = Field(None, description="Single ticker or None for all")
    skip_discovery: bool = Field(False, description="Skip phases B and C")


class TaskResponse(BaseModel):
    task_id: str
    status: str
    message: str


class HealthResponse(BaseModel):
    status: str
    timestamp: str
    version: str = "0.1.0"


# =============================================================================
# Background task results (in-memory, simple for personal use)
# =============================================================================

_task_results: dict[str, dict[str, Any]] = {}


# =============================================================================
# Health
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# =============================================================================
# Portfolio
# =============================================================================

@app.get("/portfolio", dependencies=[Depends(verify_api_key)])
async def get_portfolio():
    """Get current portfolio state: value, cash, positions count, sector allocation."""
    from src.portfolio import load_portfolio

    portfolio = load_portfolio()
    return {
        "total_value_usd": portfolio.total_value_usd,
        "cash_usd": portfolio.cash_usd,
        "cash_pct": portfolio.cash_pct,
        "invested_usd": portfolio.invested_usd,
        "open_positions": portfolio.open_positions,
        "can_open_new": portfolio.can_open_new_position,
        "sector_allocations": portfolio.sector_allocations,
        "holdings": [
            {
                "ticker": h.ticker,
                "shares": h.shares,
                "cost_basis": h.cost_basis,
                "sector": h.sector,
                "current_value_usd": h.current_value_usd,
                "allocation_pct": h.allocation_pct,
            }
            for h in portfolio.holdings
        ],
    }





@app.get("/portfolio/watchlist", dependencies=[Depends(verify_api_key)])
async def get_watchlist():
    """Get current watchlist candidates."""
    from src.db import get_client

    try:
        client = get_client()
        response = (
            client.table("discovery_candidates")
            .select("*")
            .eq("user_decision", "watchlist")
            .order("screener_score", desc=True)
            .execute()
        )
        return {
            "watchlist": response.data or [],
            "count": len(response.data or []),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Debates
# =============================================================================

@app.get("/debates/recent", dependencies=[Depends(verify_api_key)])
async def get_recent_debates(
    limit: int = 10,
    ticker: str | None = None,
    phase: str | None = None,
):
    """Get recent debates, optionally filtered by ticker or phase."""
    from src.db import get_client

    try:
        client = get_client()
        query = (
            client.table("debates")
            .select("*")
            .order("timestamp", desc=True)
            .limit(limit)
        )
        if ticker:
            query = query.eq("ticker", ticker.upper())
        if phase:
            query = query.eq("phase", phase.upper())

        response = query.execute()
        return {"debates": response.data or [], "count": len(response.data or [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/debates/{debate_id}", dependencies=[Depends(verify_api_key)])
async def get_debate(debate_id: str):
    """Get full debate detail by ID."""
    from src.db import get_client

    try:
        client = get_client()
        response = (
            client.table("debates")
            .select("*")
            .eq("id", debate_id)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise HTTPException(status_code=404, detail="Debate not found")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/debates/trigger", dependencies=[Depends(verify_api_key)])
async def trigger_debate(req: DebateTriggerRequest, background_tasks: BackgroundTasks):
    """
    Trigger a debate for a ticker. Runs in background.

    Returns a task_id to check status later via /tasks/{task_id}.
    """
    task_id = f"{req.ticker}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _task_results[task_id] = {"status": "running", "started": datetime.now(timezone.utc).isoformat()}

    async def _run():
        try:
            from src.compute_snapshot import compute_snapshot
            from src.debate_engine import run_debate
            from src.macro_context import fetch_macro_snapshot
            from src.rules_engine import Phase, Position, PortfolioState

            macro = fetch_macro_snapshot()
            snapshot = compute_snapshot(
                req.ticker, macro=macro,
                cost_basis=req.cost_basis,
                shares=req.shares,
            )
            position = Position(
                ticker=req.ticker,
                shares=req.shares,
                cost_basis=req.cost_basis,
                allocation_pct=req.allocation_pct,
            )
            portfolio = PortfolioState(total_open_positions=0, sector_allocations={})

            result = await run_debate(
                ticker=req.ticker,
                phase=Phase(req.phase.upper()),
                snapshot=snapshot,
                position=position,
                portfolio=portfolio,
            )
            fv = result.final_verdict or {}
            _task_results[task_id] = {
                "status": "completed",
                "debate_id": result.debate_id,
                "verdict": fv.get("verdict"),
                "confidence": fv.get("confidence"),
                "reasoning": fv.get("reasoning", "")[:300],
                "cost_usd": result.total_cost_usd,
                "latency_ms": result.total_latency_ms,
            }
        except Exception as e:
            _task_results[task_id] = {"status": "failed", "error": str(e)}

    background_tasks.add_task(_run)
    return TaskResponse(task_id=task_id, status="accepted", message=f"Debate for {req.ticker} queued")


@app.get("/tasks/{task_id}", dependencies=[Depends(verify_api_key)])
async def get_task_status(task_id: str):
    """Check status of a background task (debate or cycle)."""
    if task_id not in _task_results:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_results[task_id]


# =============================================================================
# Cycles
# =============================================================================

@app.post("/cycles/intraday", dependencies=[Depends(verify_api_key)])
async def trigger_intraday(req: CycleRequest, background_tasks: BackgroundTasks):
    """Trigger one intraday cycle. Runs in background."""
    task_id = f"intraday-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _task_results[task_id] = {"status": "running"}

    async def _run():
        try:
            from scripts.run_intraday_cycle import run_cycle
            summary = await run_cycle(
                dry_run=req.dry_run,
                hours=2,
                max_debates=5,
                min_score=60,
            )
            _task_results[task_id] = {
                "status": "completed",
                "news_evaluated": summary.news_evaluated,
                "debates_run": summary.debates_run,
                "watchlist_alerts": summary.watchlist_alerts,
                "total_cost": summary.total_cost,
            }
        except Exception as e:
            _task_results[task_id] = {"status": "failed", "error": str(e)}

    background_tasks.add_task(_run)
    return TaskResponse(task_id=task_id, status="accepted", message="Intraday cycle queued")


@app.post("/cycles/eod", dependencies=[Depends(verify_api_key)])
async def trigger_eod(req: EODRequest, background_tasks: BackgroundTasks):
    """Trigger EOD review cycle. Runs in background."""
    task_id = f"eod-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    _task_results[task_id] = {"status": "running"}

    async def _run():
        try:
            from scripts.run_eod_cycle import run_eod_cycle
            await run_eod_cycle(
                cost_cap=req.cost_cap,
                single_ticker=req.ticker,
                skip_discovery=req.skip_discovery,
                dry_run=req.dry_run,
            )
            _task_results[task_id] = {"status": "completed"}
        except Exception as e:
            _task_results[task_id] = {"status": "failed", "error": str(e)}

    background_tasks.add_task(_run)
    msg = f"EOD cycle queued" + (f" for {req.ticker}" if req.ticker else " (all positions)")
    return TaskResponse(task_id=task_id, status="accepted", message=msg)


# =============================================================================
# Earnings / Blackout
# =============================================================================

@app.get("/earnings/blackout", dependencies=[Depends(verify_api_key)])
async def get_blackout_status():
    """Check earnings blackout status for all portfolio tickers."""
    from src.db import get_client
    from src.earnings_calendar import is_in_blackout
    from src.rules_engine import load_rules

    try:
        client = get_client()
        response = client.table("positions").select("ticker").execute()
        tickers = [r["ticker"] for r in (response.data or []) if r.get("ticker")]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load positions: {e}")

    rules = load_rules()
    blackouts_config = rules.get("blackouts", {})
    before_days = blackouts_config.get("before_earnings_days", 3)
    after_days = blackouts_config.get("after_earnings_days", 1)

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

    active_count = sum(1 for r in results if r["blackout_active"])
    return {
        "tickers": results,
        "active_blackouts": active_count,
        "total_positions": len(results),
    }


# =============================================================================
# Stats
# =============================================================================

@app.get("/stats", dependencies=[Depends(verify_api_key)])
async def get_stats(days: int = 30):
    """
    Aggregated stats: debate counts, verdicts, costs, hit rates.
    """
    from datetime import timedelta

    from src.db import get_client

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    try:
        client = get_client()

        # Debate stats
        debates_resp = (
            client.table("debates")
            .select("verdict, confidence, total_cost_usd, phase, judge_escalated")
            .gte("timestamp", cutoff)
            .execute()
        )
        debates = debates_resp.data or []

        # Outcome stats
        outcomes_resp = (
            client.table("paper_trades")
            .select("was_correct_1d, was_correct_1w, was_correct_1m")
            .gte("timestamp", cutoff)
            .execute()
        )
        outcomes = outcomes_resp.data or []

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Compute aggregates
    total_debates = len(debates)
    total_cost = sum(float(d.get("total_cost_usd") or 0) for d in debates)
    escalated = sum(1 for d in debates if d.get("judge_escalated"))

    verdict_counts: dict[str, int] = {}
    phase_counts: dict[str, int] = {}
    for d in debates:
        v = d.get("verdict", "UNKNOWN")
        p = d.get("phase", "UNKNOWN")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        phase_counts[p] = phase_counts.get(p, 0) + 1

    # Hit rates
    def hit_rate(field: str) -> float | None:
        evaluated = [o for o in outcomes if o.get(field) is not None]
        if not evaluated:
            return None
        correct = sum(1 for o in evaluated if o[field])
        return round(correct / len(evaluated) * 100, 1)

    return {
        "period_days": days,
        "total_debates": total_debates,
        "total_cost_usd": round(total_cost, 4),
        "escalation_rate_pct": round(escalated / total_debates * 100, 1) if total_debates else 0,
        "verdicts": verdict_counts,
        "phases": phase_counts,
        "hit_rates": {
            "1d": hit_rate("was_correct_1d"),
            "1w": hit_rate("was_correct_1w"),
            "1m": hit_rate("was_correct_1m"),
        },
        "total_outcomes": len(outcomes),
    }
# =============================================================================
# Portfolio CRUD — position lots (added in migration 002)
# =============================================================================
# These endpoints operate on the new position_lots / realized_trades tables
# via the helper functions in src/portfolio.py. They expect the migration
# 002_position_lots.sql to have been applied.
#
# Behavior matrix:
#   POST   /portfolio/positions               → create lot (new buy)
#   GET    /portfolio/positions/{ticker}      → summary + lots + realized P&L
#   GET    /portfolio/positions/{ticker}/lots → just the active lots
#   PATCH  /portfolio/positions/{ticker}      → buy more OR sell partial
#   POST   /portfolio/positions/{ticker}/close → sell all active lots
#   DELETE /portfolio/positions/{ticker}?confirm=TICKER → hard delete (error correction)
#
# All endpoints update cash and persist a new portfolio_state_daily row.

from pydantic import BaseModel, Field, field_validator


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------

class CreatePositionRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=16)
    shares: float = Field(..., gt=0)
    buy_price: float = Field(..., gt=0)
    purchased_at: str | None = Field(None, description="ISO timestamp; defaults to now")
    sector: str | None = None
    stop_loss_price: float | None = Field(None, gt=0)
    stop_win_price: float | None = Field(None, gt=0)
    notes: str | None = None

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, v: str) -> str:
        return v.strip().upper()


class PatchPositionRequest(BaseModel):
    action: str = Field(..., description="'buy' to add a lot, 'sell' to consume FIFO")
    shares: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    executed_at: str | None = None
    notes: str | None = None

    @field_validator("action")
    @classmethod
    def valid_action(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("buy", "sell"):
            raise ValueError("action must be 'buy' or 'sell'")
        return v


class ClosePositionRequest(BaseModel):
    sell_price: float = Field(..., gt=0)
    sold_at: str | None = None
    notes: str | None = None


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _persist_cash_change(new_cash: float, note: str) -> None:
    """Update today's portfolio_state_daily row with new cash and recomputed total."""
    from src.portfolio import (
        get_all_position_summaries,
        enrich_summaries_with_live_prices,
        save_portfolio_state,
    )
    summaries = get_all_position_summaries()
    enriched = enrich_summaries_with_live_prices(summaries)
    invested_value = sum(float(s.get("current_value_usd") or 0) for s in enriched)
    total_value = invested_value + new_cash
    save_portfolio_state(total_value=total_value, cash=new_cash, notes=note)


def _current_cash() -> float:
    """Read latest cash balance from portfolio_state_daily."""
    from src.db import get_client
    try:
        client = get_client()
        resp = (
            client.table("portfolio_state_daily")
            .select("cash_usd")
            .order("date", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            return float(resp.data[0].get("cash_usd") or 0)
    except Exception as e:
        print(f"⚠️  Failed to read current cash: {e}")
    return 0.0


def _validate_allocation_caps(
    ticker: str,
    new_position_value_usd: float,
    sector: str | None,
) -> None:
    """
    Raise HTTPException(400) if the proposed buy would breach allocation
    rules from rules.yaml. Soft check — warns on the API layer before
    persisting. The rules engine will catch it again at debate time.
    """
    from src.portfolio import (
        get_all_position_summaries,
        enrich_summaries_with_live_prices,
    )
    from src.rules_engine import load_rules

    rules = load_rules()
    pos_rules = rules.get("position_sizing", {})
    sector_rules = rules.get("sector_limits", {})
    max_ticker_pct = pos_rules.get("max_allocation_per_ticker_pct", 100.0)
    max_sector_pct = sector_rules.get("max_allocation_per_sector_pct", 100.0)

    summaries = get_all_position_summaries()
    enriched = enrich_summaries_with_live_prices(summaries)
    invested_value = sum(float(s.get("current_value_usd") or 0) for s in enriched)
    cash = _current_cash()
    total_value = invested_value + cash

    # Cash sufficient?
    if new_position_value_usd > cash + 1e-6:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient cash: need ${new_position_value_usd:,.2f}, have ${cash:,.2f}",
        )

    if total_value <= 0:
        return  # nothing to validate against

    # Per-ticker cap (existing position + new buy)
    existing_value = sum(
        float(s.get("current_value_usd") or 0)
        for s in enriched
        if s.get("ticker") == ticker.upper()
    )
    new_ticker_pct = (existing_value + new_position_value_usd) / total_value * 100
    if new_ticker_pct > max_ticker_pct:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Allocation cap exceeded for {ticker}: "
                f"{new_ticker_pct:.1f}% > {max_ticker_pct}%"
            ),
        )

    # Per-sector cap
    if sector:
        existing_sector_value = sum(
            float(s.get("current_value_usd") or 0)
            for s in enriched
            if s.get("sector") == sector
        )
        new_sector_pct = (existing_sector_value + new_position_value_usd) / total_value * 100
        if new_sector_pct > max_sector_pct:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Sector cap exceeded for {sector}: "
                    f"{new_sector_pct:.1f}% > {max_sector_pct}%"
                ),
            )


# -----------------------------------------------------------------------------
# READ — overrides the existing GET /portfolio/positions
# -----------------------------------------------------------------------------
# Note: this REPLACES the existing GET /portfolio/positions handler. Delete
# the old one in main.py before adding this section, or rename one of them.

@app.get("/portfolio/positions", dependencies=[Depends(verify_api_key)])
async def get_positions_v2():
    """
    Return all active positions (one row per ticker), enriched with current
    price and unrealized P&L from a batched yfinance fetch (5-min cache).
    """
    from src.portfolio import (
        get_all_position_summaries,
        enrich_summaries_with_live_prices,
    )
    summaries = get_all_position_summaries()
    enriched = enrich_summaries_with_live_prices(summaries)
    return {
        "positions": enriched,
        "count": len(enriched),
    }


@app.get("/portfolio/positions/{ticker}", dependencies=[Depends(verify_api_key)])
async def get_position_detail(ticker: str):
    """
    Return everything about a single ticker: summary, active lots, recent
    realized trades, and live price.
    """
    from src.db import get_client
    from src.portfolio import (
        enrich_summaries_with_live_prices,
        get_active_lots,
        get_position_summary,
    )

    summary = get_position_summary(ticker)
    if not summary:
        raise HTTPException(status_code=404, detail=f"No active position for {ticker.upper()}")

    enriched = enrich_summaries_with_live_prices([summary])[0]
    lots = get_active_lots(ticker)

    try:
        client = get_client()
        rt_resp = (
            client.table("realized_trades")
            .select("*")
            .eq("ticker", ticker.upper())
            .order("sold_at", desc=True)
            .limit(20)
            .execute()
        )
        realized = rt_resp.data or []
    except Exception:
        realized = []

    return {
        "summary": enriched,
        "lots": lots,
        "realized_trades": realized,
    }


@app.get("/portfolio/positions/{ticker}/lots", dependencies=[Depends(verify_api_key)])
async def get_position_lots(ticker: str):
    """Return just the active lots for a ticker (FIFO ordered)."""
    from src.portfolio import get_active_lots
    lots = get_active_lots(ticker)
    return {"ticker": ticker.upper(), "lots": lots, "count": len(lots)}


# -----------------------------------------------------------------------------
# CREATE — new buy
# -----------------------------------------------------------------------------

@app.post("/portfolio/positions", dependencies=[Depends(verify_api_key)], status_code=201)
async def create_position(req: CreatePositionRequest):
    """
    Create a new position lot. If the ticker already has active lots,
    this is treated as adding to the existing position (new lot, FIFO order).
    """
    from src.portfolio import apply_manual_trade_to_cash, create_lot

    trade_value = req.shares * req.buy_price
    _validate_allocation_caps(req.ticker, trade_value, req.sector)

    lot = create_lot(
        ticker=req.ticker,
        shares=req.shares,
        buy_price=req.buy_price,
        purchased_at=req.purchased_at,
        sector=req.sector,
        stop_loss_price=req.stop_loss_price,
        stop_win_price=req.stop_win_price,
        notes=req.notes,
    )
    if not lot:
        raise HTTPException(status_code=500, detail="Failed to create lot")

    cash = _current_cash()
    new_cash = apply_manual_trade_to_cash("BUY", req.shares, req.buy_price, cash)
    _persist_cash_change(new_cash, f"Bought {req.shares} {req.ticker} @ ${req.buy_price}")

    return {
        "lot": lot,
        "cash_after": new_cash,
    }


# -----------------------------------------------------------------------------
# PATCH — buy more OR sell partial
# -----------------------------------------------------------------------------

@app.patch("/portfolio/positions/{ticker}", dependencies=[Depends(verify_api_key)])
async def patch_position(ticker: str, req: PatchPositionRequest):
    """
    Modify an existing position via partial buy or sell.

      action='buy'  → creates a new lot at the given price (FIFO order preserved)
      action='sell' → consumes lots in FIFO order, recording realized trades
    """
    from src.portfolio import (
        apply_manual_trade_to_cash,
        create_lot,
        get_position_summary,
        sell_shares_fifo,
    )

    summary = get_position_summary(ticker)

    if req.action == "buy":
        # Need sector for cap check; pull from existing lots if available
        sector = summary.get("sector") if summary else None
        trade_value = req.shares * req.price
        _validate_allocation_caps(ticker, trade_value, sector)

        lot = create_lot(
            ticker=ticker,
            shares=req.shares,
            buy_price=req.price,
            purchased_at=req.executed_at,
            sector=sector,
            notes=req.notes,
        )
        if not lot:
            raise HTTPException(status_code=500, detail="Failed to create lot")

        cash = _current_cash()
        new_cash = apply_manual_trade_to_cash("BUY", req.shares, req.price, cash)
        _persist_cash_change(new_cash, f"Bought {req.shares} {ticker} @ ${req.price}")

        return {
            "action": "buy",
            "lot_created": lot,
            "cash_after": new_cash,
        }

    # action == 'sell'
    if not summary:
        raise HTTPException(status_code=404, detail=f"No active position for {ticker.upper()}")

    try:
        result = sell_shares_fifo(
            ticker=ticker,
            shares_to_sell=req.shares,
            sell_price=req.price,
            sold_at=req.executed_at,
            sell_reason="partial",
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cash = _current_cash()
    new_cash = apply_manual_trade_to_cash("SELL", req.shares, req.price, cash)
    _persist_cash_change(
        new_cash,
        f"Sold {req.shares} {ticker} @ ${req.price} (P&L ${result['realized_pnl_usd']:,.2f})",
    )

    return {
        "action": "sell",
        **result,
        "cash_after": new_cash,
    }


# -----------------------------------------------------------------------------
# CLOSE — sell all active lots
# -----------------------------------------------------------------------------

@app.post("/portfolio/positions/{ticker}/close", dependencies=[Depends(verify_api_key)])
async def close_position(ticker: str, req: ClosePositionRequest):
    """
    Close all active lots for a ticker at the given sell price. Returns the
    realized P&L summary across all consumed lots.
    """
    from src.portfolio import apply_manual_trade_to_cash, close_position_lots

    result = close_position_lots(
        ticker=ticker,
        sell_price=req.sell_price,
        sold_at=req.sold_at,
        notes=req.notes,
    )
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])

    cash = _current_cash()
    new_cash = apply_manual_trade_to_cash(
        "CLOSE", result["shares_sold"], req.sell_price, cash
    )
    _persist_cash_change(
        new_cash,
        f"Closed {ticker} ({result['shares_sold']} sh @ ${req.sell_price}, P&L ${result['realized_pnl_usd']:,.2f})",
    )

    return {**result, "cash_after": new_cash}


# -----------------------------------------------------------------------------
# DELETE — hard delete (error correction)
# -----------------------------------------------------------------------------

@app.delete("/portfolio/positions/{ticker}", dependencies=[Depends(verify_api_key)])
async def delete_position(ticker: str, confirm: str = ""):
    """
    HARD delete all lots and realized trades for a ticker. Requires
    ?confirm=TICKER as a query param to prevent accidents. Does NOT touch
    cash, on the assumption the position was loaded by mistake.
    """
    if confirm.upper() != ticker.upper():
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation required: pass ?confirm={ticker.upper()} to delete",
        )

    from src.portfolio import delete_position_lots
    result = delete_position_lots(ticker)
    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    return result
