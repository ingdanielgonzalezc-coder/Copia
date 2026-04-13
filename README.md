# Stock Advisor System

Multi-agent stock advisory system with Bull/Bear/Judge debate engine.
**Generates alerts only — never executes trades.**

- **Stack:** Python 3.12 + FastAPI + Pydantic v2 + Supabase
- **Hosting:** Railway (backend) + Vercel (frontend, phase 2)
- **LLMs:** Grok 4.2 (Bull) + Claude Sonnet 4.6 (Bear, Judge default) + Claude Opus 4.6 (Judge escalation)
- **Data:** Polygon.io (Massive) Starter + yfinance (fundamentals)

---

## Setup (first time)

### 1. Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- A Supabase project created
- All API keys ready (see `.env.example`)

### 2. Install dependencies

```bash
cd stock-advisor
uv sync
```

This creates a `.venv/` and installs everything from `pyproject.toml`.

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in all keys
```

### 4. Set up Supabase database

1. Open your Supabase project → SQL Editor
2. Enable the `pgvector` extension: Database → Extensions → search "vector" → enable
3. Open `db/schema.sql` from this repo
4. Copy-paste the entire content into the SQL Editor and run it
5. Verify tables exist: Database → Tables (you should see `debates`, `positions`, `paper_trades`, etc.)

### 5. Configure your portfolio and rules

- `config/rules.yaml` — already populated with your accepted rules
- `config/investor_profile.yaml` — already populated (swing trading, S&P 500 universe)
- `config/sector_benchmarks.yaml` — placeholder values, will be refreshed by script

### 6. Verify setup

```bash
uv run python -m src.macro_context
```

This should print the current market regime by fetching SPY and VIX data. If it works, your Polygon key is good.

---

## Project structure

```
stock-advisor/
├── README.md
├── pyproject.toml          # uv-managed dependencies
├── .env.example
├── .gitignore
│
├── config/
│   ├── rules.yaml                  # Hard rules (stops, allocation limits, blackouts)
│   ├── investor_profile.yaml       # Trading style, horizon, universe
│   └── sector_benchmarks.yaml      # Sector medians, refreshed weekly
│
├── prompts/
│   ├── bull.txt        # Bull researcher prompt (Grok 4.2)
│   ├── bear.txt        # Bear researcher prompt (Sonnet 4.6)
│   └── judge.txt       # Synthesizer prompt (Sonnet 4.6 / Opus 4.6 escalation)
│
├── db/
│   └── schema.sql      # Supabase tables, indexes, views
│
├── src/
│   ├── __init__.py
│   ├── macro_context.py            # 4-regime classifier
│   ├── compute_snapshot.py         # [TO BE ADDED day 3] Technical + fundamentals snapshot
│   ├── rules_engine.py             # [TO BE ADDED day 3] Pre-filter + post-validator
│   ├── news_pipeline.py            # [TO BE ADDED day 5] Heuristic + dedup + Haiku scoring
│   └── debate_engine.py            # [TO BE ADDED day 5] Bull + Bear + Judge orchestration
│
├── scripts/
│   └── refresh_sector_benchmarks.py  # [TO BE ADDED day 3] Weekly cron
│
└── tests/
    └── (added incrementally)
```

---

## Implementation roadmap

| Day | Owner | Deliverable |
|---|---|---|
| 1 | You | API keys + Supabase + Railway/Vercel accounts ✅ |
| 1-2 | Claude | This base structure ✅ |
| 2 | You | `uv sync`, run `db/schema.sql`, fill `.env`, validate setup |
| 3 | Claude | `compute_snapshot.py` + `rules_engine.py` + `refresh_sector_benchmarks.py` + tests |
| 3-4 | You | Run snapshot on a real ticker, run benchmark refresh, share outputs |
| 5 | Claude | `news_pipeline.py` + `debate_engine.py` + first FastAPI endpoint |
| 5-7 | Both | Smoke test on 5 tickers, end-to-end debate validation |
| Week 2 | Claude | Cron jobs, Telegram bot, observability layer |
| Week 3 | Claude | DISCOVERY phase + screener (S&P 500 scanning) |
| Week 4 | Both | Dashboard React + go-live for paper trade |

---

## Important notes

- **Never commit `.env`** — it's in `.gitignore` but double-check.
- **Supabase key:** use `service_role`, NOT `anon`/`publishable`. The backend needs full permissions.
- **The system never executes trades.** It generates alerts. Final decision is always yours.
- **All debates are logged** for audit trail and outcome tracking.
