-- =============================================================================
-- Migration 002 — Position lots and realized trades
-- =============================================================================
-- Adds FIFO lot tracking to the positions system. Each "buy" creates a new
-- lot row. Sells consume lots in FIFO order, recording each consumption as
-- a realized trade for P&L tracking.
--
-- The legacy `positions` table is kept for backwards compatibility with the
-- existing debate engine and rules engine, but all new queries should use
-- `position_lots` (active lots, closed_at IS NULL) and the helper view
-- `position_summary` defined at the bottom.
--
-- Run order: after db/schema.sql.
-- Idempotent: uses IF NOT EXISTS / IF EXISTS guards everywhere.
-- =============================================================================

-- =============================================================================
-- POSITION_LOTS — One row per buy event
-- =============================================================================
CREATE TABLE IF NOT EXISTS position_lots (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker              VARCHAR(16) NOT NULL,
    sector              VARCHAR(64),

    shares              NUMERIC(18, 6) NOT NULL CHECK (shares > 0),
    buy_price           NUMERIC(18, 4) NOT NULL CHECK (buy_price > 0),
    purchased_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    stop_loss_price     NUMERIC(18, 4),
    stop_win_price      NUMERIC(18, 4),

    -- Lifecycle
    closed_at           TIMESTAMPTZ,                       -- NULL = active lot
    close_reason        VARCHAR(32),                       -- 'sold' | 'closed_position' | 'partial_sold'

    -- Audit
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_lots_ticker             ON position_lots(ticker);
CREATE INDEX IF NOT EXISTS idx_lots_active             ON position_lots(ticker, purchased_at) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_lots_purchased_at       ON position_lots(purchased_at DESC);
CREATE INDEX IF NOT EXISTS idx_lots_sector             ON position_lots(sector) WHERE closed_at IS NULL;


-- =============================================================================
-- REALIZED_TRADES — One row per (partial or full) sell event against a lot
-- =============================================================================
-- A single user "sell 10 shares" can produce multiple realized_trades rows
-- if FIFO consumes more than one lot. Each row references the lot it came
-- from so you can audit exactly what was sold against what.
CREATE TABLE IF NOT EXISTS realized_trades (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker              VARCHAR(16) NOT NULL,
    lot_id              UUID NOT NULL REFERENCES position_lots(id) ON DELETE CASCADE,

    shares_sold         NUMERIC(18, 6) NOT NULL CHECK (shares_sold > 0),
    buy_price           NUMERIC(18, 4) NOT NULL,           -- copied from the lot for audit
    sell_price          NUMERIC(18, 4) NOT NULL CHECK (sell_price > 0),

    pnl_usd             NUMERIC(18, 4) NOT NULL,
    pnl_pct             NUMERIC(10, 4) NOT NULL,
    holding_days        INTEGER,

    sold_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sell_reason         VARCHAR(32),                       -- 'partial' | 'close_position'
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_realized_ticker         ON realized_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_realized_sold_at        ON realized_trades(sold_at DESC);
CREATE INDEX IF NOT EXISTS idx_realized_lot            ON realized_trades(lot_id);


-- =============================================================================
-- POSITION_SUMMARY — Aggregated view of active lots per ticker
-- =============================================================================
-- This is what new code should query when it needs "the current state of NVDA"
-- as a single record. Aggregates active lots into shares + weighted-avg cost
-- basis. Realized P&L is summed across all closed trades for the same ticker.
CREATE OR REPLACE VIEW position_summary AS
SELECT
    pl.ticker,
    MAX(pl.sector)                                                                       AS sector,
    COUNT(*)::int                                                                        AS lots_count,
    SUM(pl.shares)                                                                       AS total_shares,
    SUM(pl.shares * pl.buy_price) / NULLIF(SUM(pl.shares), 0)                            AS avg_cost_basis,
    SUM(pl.shares * pl.buy_price)                                                        AS total_invested_usd,
    MIN(pl.purchased_at)                                                                 AS first_purchase_at,
    MAX(pl.purchased_at)                                                                 AS last_purchase_at,
    COALESCE((SELECT SUM(rt.pnl_usd) FROM realized_trades rt WHERE rt.ticker = pl.ticker), 0)  AS realized_pnl_usd
FROM position_lots pl
WHERE pl.closed_at IS NULL
GROUP BY pl.ticker;


-- =============================================================================
-- Backfill: migrate existing `positions` rows into single lots
-- =============================================================================
-- Each existing row in `positions` becomes a single lot in `position_lots`,
-- using opened_at as purchased_at and cost_basis as buy_price. Skipped if
-- a lot for the ticker already exists (idempotent).
INSERT INTO position_lots (
    ticker, sector, shares, buy_price, purchased_at,
    stop_loss_price, stop_win_price, created_at, notes
)
SELECT
    p.ticker,
    p.sector,
    p.shares,
    p.cost_basis,
    COALESCE(p.opened_at, NOW()),
    NULL,                              -- legacy stop_loss_pct cannot be converted to absolute price without a reference price
    NULL,
    COALESCE(p.opened_at, NOW()),
    'Backfilled from legacy positions table'
FROM positions p
WHERE p.shares > 0
  AND p.cost_basis IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM position_lots pl
      WHERE pl.ticker = p.ticker AND pl.closed_at IS NULL
  );


-- =============================================================================
-- Trigger: keep updated_at fresh on position_lots
-- =============================================================================
CREATE OR REPLACE FUNCTION touch_position_lots_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_position_lots_updated_at ON position_lots;
CREATE TRIGGER trg_position_lots_updated_at
    BEFORE UPDATE ON position_lots
    FOR EACH ROW
    EXECUTE FUNCTION touch_position_lots_updated_at();


-- =============================================================================
-- DONE
-- =============================================================================
