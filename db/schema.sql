-- =============================================================================
-- Stock Advisor — Supabase Schema
-- Run this in the Supabase SQL Editor
-- Requires pgvector extension (enable via Database → Extensions)
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- =============================================================================
-- POSITIONS — Current portfolio holdings
-- =============================================================================
CREATE TABLE IF NOT EXISTS positions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker          VARCHAR(16) NOT NULL UNIQUE,
    sector          VARCHAR(64),
    shares          NUMERIC(18, 6) NOT NULL DEFAULT 0,
    cost_basis      NUMERIC(18, 4),
    current_alloc_pct NUMERIC(6, 2),
    stop_loss_pct   NUMERIC(6, 2),
    take_profit_pct NUMERIC(6, 2),
    flag_review     BOOLEAN NOT NULL DEFAULT FALSE,
    last_debate_at  TIMESTAMPTZ,
    opened_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    notes           TEXT
);

CREATE INDEX idx_positions_ticker ON positions(ticker);
CREATE INDEX idx_positions_sector ON positions(sector);

-- =============================================================================
-- DEBATES — All Bull/Bear/Judge debates run by the system
-- =============================================================================
CREATE TABLE IF NOT EXISTS debates (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    phase               VARCHAR(16) NOT NULL,  -- INITIAL | INTRADAY | EOD | DISCOVERY
    ticker              VARCHAR(16) NOT NULL,
    prompt_version      VARCHAR(16) NOT NULL,

    -- Macro context at time of debate
    regime              VARCHAR(32),           -- BULL | NEUTRAL | HIGH_VOLATILITY | BEAR
    spy_price           NUMERIC(10, 2),
    vix_level           NUMERIC(6, 2),

    -- Trigger
    trigger_type        VARCHAR(32),           -- news | scheduled_eod | manual | discovery
    trigger_data        JSONB,

    -- Inputs (snapshot of context at time of debate)
    snapshot            JSONB NOT NULL,
    position_at_debate  JSONB,
    allowed_actions     TEXT[],
    news_item           JSONB,                 -- only for INTRADAY

    -- Agent responses (full JSON outputs)
    bull_response       JSONB,
    bull_model          VARCHAR(64),
    bull_latency_ms     INTEGER,
    bull_tokens_in      INTEGER,
    bull_tokens_out     INTEGER,
    bull_cost_usd       NUMERIC(8, 5),

    bear_response       JSONB,
    bear_model          VARCHAR(64),
    bear_latency_ms     INTEGER,
    bear_tokens_in      INTEGER,
    bear_tokens_out     INTEGER,
    bear_cost_usd       NUMERIC(8, 5),

    judge_response      JSONB,
    judge_model         VARCHAR(64),
    judge_escalated     BOOLEAN DEFAULT FALSE,
    judge_latency_ms    INTEGER,
    judge_tokens_in     INTEGER,
    judge_tokens_out    INTEGER,
    judge_cost_usd      NUMERIC(8, 5),

    -- Final verdict (after post-validator)
    verdict             VARCHAR(32),
    verdict_pre_validator VARCHAR(32),
    confidence          INTEGER,
    rules_violated      BOOLEAN DEFAULT FALSE,

    -- Aggregates
    total_cost_usd      NUMERIC(8, 5),
    total_latency_ms    INTEGER
);

CREATE INDEX idx_debates_ticker ON debates(ticker);
CREATE INDEX idx_debates_timestamp ON debates(timestamp DESC);
CREATE INDEX idx_debates_phase ON debates(phase);
CREATE INDEX idx_debates_prompt_version ON debates(prompt_version);
CREATE INDEX idx_debates_regime ON debates(regime);
CREATE INDEX idx_debates_verdict ON debates(verdict);

-- =============================================================================
-- PAPER_TRADES — Simulated trades for outcome tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS paper_trades (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    debate_id           UUID REFERENCES debates(id) ON DELETE CASCADE,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker              VARCHAR(16) NOT NULL,
    simulated_action    VARCHAR(32) NOT NULL,
    price_at_decision   NUMERIC(10, 4),
    shares_change       NUMERIC(18, 6),
    allocation_pct      NUMERIC(6, 2),
    macro_regime        VARCHAR(32),

    -- Outcomes computed post-hoc by outcome_tracker job
    outcome_1d_pct      NUMERIC(8, 4),
    outcome_1w_pct      NUMERIC(8, 4),
    outcome_1m_pct      NUMERIC(8, 4),
    was_correct_1d      BOOLEAN,
    was_correct_1w      BOOLEAN,
    was_correct_1m      BOOLEAN,
    was_correct_attributed BOOLEAN,  -- based on declared time_horizon
    outcome_computed_at TIMESTAMPTZ
);

CREATE INDEX idx_paper_trades_debate ON paper_trades(debate_id);
CREATE INDEX idx_paper_trades_ticker ON paper_trades(ticker);
CREATE INDEX idx_paper_trades_timestamp ON paper_trades(timestamp DESC);

-- =============================================================================
-- RULE_VIOLATIONS — Audit log of post-validator catches
-- =============================================================================
CREATE TABLE IF NOT EXISTS rule_violations (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    debate_id           UUID REFERENCES debates(id) ON DELETE CASCADE,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    violation_type      VARCHAR(64) NOT NULL,
    original_verdict    VARCHAR(32),
    downgraded_to       VARCHAR(32),
    rule_breached       VARCHAR(128),
    details             JSONB
);

CREATE INDEX idx_rule_violations_debate ON rule_violations(debate_id);
CREATE INDEX idx_rule_violations_timestamp ON rule_violations(timestamp DESC);
CREATE INDEX idx_rule_violations_type ON rule_violations(violation_type);

-- =============================================================================
-- OPINIONS — Memory of past debate outcomes for memory injection
-- =============================================================================
CREATE TABLE IF NOT EXISTS opinions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker              VARCHAR(16) NOT NULL,
    debate_id           UUID REFERENCES debates(id) ON DELETE CASCADE,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verdict             VARCHAR(32),
    confidence          INTEGER,
    time_horizon        VARCHAR(32),
    outcome_pct         NUMERIC(8, 4),
    was_correct         BOOLEAN,
    weight              NUMERIC(4, 3) DEFAULT 1.0,  -- decreases by age
    summary             TEXT,                        -- 1-2 line summary
    invalidated_at      TIMESTAMPTZ,                 -- set when memory invalidation event fires
    invalidation_reason VARCHAR(64)
);

CREATE INDEX idx_opinions_ticker ON opinions(ticker);
CREATE INDEX idx_opinions_timestamp ON opinions(timestamp DESC);
CREATE INDEX idx_opinions_active ON opinions(ticker, invalidated_at) WHERE invalidated_at IS NULL;

-- =============================================================================
-- FACTS_TRANSACTIONS — Immutable transaction history
-- =============================================================================
CREATE TABLE IF NOT EXISTS facts_transactions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker              VARCHAR(16) NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    action              VARCHAR(32) NOT NULL,  -- buy | sell | dividend | split
    shares              NUMERIC(18, 6),
    price               NUMERIC(10, 4),
    notes               TEXT
);

CREATE INDEX idx_facts_tx_ticker ON facts_transactions(ticker);
CREATE INDEX idx_facts_tx_timestamp ON facts_transactions(timestamp DESC);

-- =============================================================================
-- FACTS_EVENTS — Material events that invalidate opinions
-- =============================================================================
CREATE TABLE IF NOT EXISTS facts_events (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker              VARCHAR(16) NOT NULL,
    timestamp           TIMESTAMPTZ NOT NULL,
    event_type          VARCHAR(64) NOT NULL,
    -- earnings_report | guidance_change | ceo_change | cfo_change
    -- merger_announced | acquisition_announced | credit_rating_change
    -- dividend_cut | dividend_initiated | stock_split
    -- major_lawsuit_filed | regulatory_approval | regulatory_rejection
    details             JSONB,
    invalidates_memory  BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_facts_events_ticker ON facts_events(ticker);
CREATE INDEX idx_facts_events_timestamp ON facts_events(timestamp DESC);
CREATE INDEX idx_facts_events_type ON facts_events(event_type);

-- =============================================================================
-- NEWS_DEDUP — Embedding cache for news deduplication
-- =============================================================================
CREATE TABLE IF NOT EXISTS news_dedup (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker              VARCHAR(16) NOT NULL,
    news_id             VARCHAR(128),  -- Polygon news id
    title               TEXT,
    embedding           vector(1536),  -- text-embedding-3-small dimensions
    category            VARCHAR(64),
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    relevance_score     INTEGER
);

CREATE INDEX idx_news_dedup_ticker ON news_dedup(ticker);
CREATE INDEX idx_news_dedup_timestamp ON news_dedup(timestamp DESC);
-- HNSW index for vector similarity search
CREATE INDEX idx_news_dedup_embedding ON news_dedup USING hnsw (embedding vector_cosine_ops);

-- =============================================================================
-- MACRO_SNAPSHOTS — Historical macro regime tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS macro_snapshots (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    spy_price           NUMERIC(10, 2),
    spy_ma200           NUMERIC(10, 2),
    spy_ma50            NUMERIC(10, 2),
    vix_level           NUMERIC(6, 2),
    regime              VARCHAR(32) NOT NULL
);

CREATE INDEX idx_macro_timestamp ON macro_snapshots(timestamp DESC);

-- =============================================================================
-- PORTFOLIO_STATE_DAILY — Daily portfolio metrics for circuit breakers
-- =============================================================================
CREATE TABLE IF NOT EXISTS portfolio_state_daily (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date                DATE NOT NULL UNIQUE,
    total_value_usd     NUMERIC(18, 2),
    cash_usd            NUMERIC(18, 2),
    daily_pnl_pct       NUMERIC(8, 4),
    drawdown_from_peak_pct NUMERIC(8, 4),
    peak_value_usd      NUMERIC(18, 2),
    defensive_mode      BOOLEAN DEFAULT FALSE,
    paused              BOOLEAN DEFAULT FALSE,
    notes               TEXT
);

CREATE INDEX idx_portfolio_date ON portfolio_state_daily(date DESC);

-- =============================================================================
-- DISCOVERY_CANDIDATES — Tickers that survived screener but await debate
-- (used in DISCOVERY phase, week 3)
-- =============================================================================
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ticker              VARCHAR(16) NOT NULL,
    scan_timestamp      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    setup_type          VARCHAR(32),  -- momentum_with_pullback | mean_reversion_in_uptrend
    screener_score      INTEGER,
    snapshot_summary    JSONB,
    debate_id           UUID REFERENCES debates(id),
    final_verdict       VARCHAR(32),
    user_decision       VARCHAR(32),  -- accepted | rejected | pending
    user_decision_at    TIMESTAMPTZ
);

CREATE INDEX idx_discovery_scan ON discovery_candidates(scan_timestamp DESC);
CREATE INDEX idx_discovery_ticker ON discovery_candidates(ticker);

-- =============================================================================
-- VIEWS — Audit trail and dashboard queries
-- =============================================================================

CREATE OR REPLACE VIEW audit_trail AS
SELECT
    d.id,
    d.timestamp,
    d.phase,
    d.ticker,
    d.regime,
    d.prompt_version,
    d.verdict,
    d.confidence,
    d.judge_escalated,
    d.bull_response->>'thesis' AS bull_thesis,
    d.bull_response->>'confidence' AS bull_confidence,
    d.bear_response->>'thesis' AS bear_thesis,
    d.bear_response->>'confidence' AS bear_confidence,
    d.judge_response->>'reasoning' AS judge_reasoning,
    d.total_cost_usd,
    pt.outcome_1w_pct,
    pt.was_correct_attributed
FROM debates d
LEFT JOIN paper_trades pt ON d.id = pt.debate_id
ORDER BY d.timestamp DESC;

CREATE OR REPLACE VIEW daily_metrics AS
SELECT
    DATE(timestamp) AS date,
    COUNT(*) AS debates_count,
    SUM(CASE WHEN judge_escalated THEN 1 ELSE 0 END) AS escalations,
    SUM(CASE WHEN rules_violated THEN 1 ELSE 0 END) AS rule_violations,
    AVG(total_cost_usd) AS avg_cost_per_debate,
    SUM(total_cost_usd) AS total_cost,
    AVG(total_latency_ms) AS avg_latency_ms
FROM debates
GROUP BY DATE(timestamp)
ORDER BY date DESC;

-- =============================================================================
-- DONE
-- =============================================================================
