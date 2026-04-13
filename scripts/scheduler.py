"""
Scheduler — single long-lived process for Railway deployment.

Replaces manual cron jobs with APScheduler. Runs all cycles on schedule
and exposes a /health endpoint for Railway health checks.

Schedule (all times US/Eastern):
  - Intraday cycle: every 30 min, 09:00-17:00, Mon-Fri
  - Monday pre-open: Monday 08:00 (60h lookback for weekend news)
  - EOD cycle: 21:00, Mon-Fri
  - Sector benchmarks refresh: Monday 06:00
  - Outcome tracker: daily 00:30
  - Watchlist cleanup: daily 06:30

Usage:
    # Local test
    uv run python -m scripts.scheduler

    # Railway (via railway.toml startCommand)
    python -m scripts.scheduler

Environment:
    PORT: HTTP port for health check (default 8080, Railway sets this)
    SCHEDULER_DRY_RUN: if "true", log but don't execute jobs
    INTRADAY_INTERVAL_MIN: override intraday interval (default 30)
    COST_CAP_DAILY: max USD per day on debates (default 5.0)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from threading import Thread

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

PORT = int(os.getenv("PORT", "8080"))
DRY_RUN = os.getenv("SCHEDULER_DRY_RUN", "false").lower() == "true"
INTRADAY_INTERVAL = int(os.getenv("INTRADAY_INTERVAL_MIN", "30"))
COST_CAP_DAILY = float(os.getenv("COST_CAP_DAILY", "5.0"))
TIMEZONE = "US/Eastern"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")


# =============================================================================
# Health check / API server (FastAPI via uvicorn)
# =============================================================================

# Track scheduler state for the health endpoint
_scheduler_state = {
    "jobs_run": 0,
    "last_job": "never",
    "last_error": None,
}


def start_api_server(port: int) -> None:
    """Start FastAPI server in a background thread."""
    import uvicorn
    from api.main import app

    config = uvicorn.Config(
        app, host="0.0.0.0", port=port,
        log_level="warning",  # reduce noise, scheduler has its own logs
    )
    server = uvicorn.Server(config)
    thread = Thread(target=server.run, daemon=True)
    thread.start()
    logger.info(f"API server on port {port} (FastAPI + health check)")


# =============================================================================
# Job wrappers
# =============================================================================

def _record_job(name: str) -> None:
    """Update state after a job runs."""
    _scheduler_state["jobs_run"] += 1
    _scheduler_state["last_job"] = f"{name} @ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"


async def _run_command(name: str, cmd: list[str], timeout: int = 600) -> bool:
    """Run a command as subprocess. Returns True on success."""
    logger.info(f"▶ {name} starting: {' '.join(cmd)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        output = stdout.decode() if stdout else ""
        # Log last 20 lines of output
        lines = output.strip().split("\n")
        for line in lines[-20:]:
            logger.info(f"  {line}")

        if proc.returncode == 0:
            logger.info(f"✅ {name} completed (exit 0)")
            _record_job(name)
            return True
        else:
            logger.error(f"❌ {name} failed (exit {proc.returncode})")
            _scheduler_state["last_error"] = f"{name} exit {proc.returncode}"
            return False
    except asyncio.TimeoutError:
        logger.error(f"❌ {name} timed out after {timeout}s")
        proc.kill()
        _scheduler_state["last_error"] = f"{name} timeout"
        return False
    except Exception as e:
        logger.error(f"❌ {name} error: {e}")
        _scheduler_state["last_error"] = str(e)
        return False


PYTHON = sys.executable  # same Python that runs the scheduler


async def job_intraday() -> None:
    """Run one intraday cycle."""
    args = [PYTHON, "-m", "scripts.run_intraday_cycle",
            "--hours", "1", "--max-debates", "5", "--min-score", "60"]
    if DRY_RUN:
        args.append("--dry-run")
    await _run_command("INTRADAY", args, timeout=300)


async def job_monday_preopen() -> None:
    """Monday pre-open scan: 60h lookback to capture weekend news."""
    args = [PYTHON, "-m", "scripts.run_intraday_cycle",
            "--hours", "60", "--max-debates", "5", "--min-score", "55"]
    if DRY_RUN:
        args.append("--dry-run")
    await _run_command("MONDAY_PREOPEN", args, timeout=600)


async def job_eod() -> None:
    """Run EOD review cycle."""
    args = [PYTHON, "-m", "scripts.run_eod_cycle",
            "--cost-cap", str(COST_CAP_DAILY)]
    if DRY_RUN:
        args.append("--dry-run")
    await _run_command("EOD", args, timeout=900)


async def job_sector_benchmarks() -> None:
    """Refresh sector benchmarks."""
    await _run_command(
        "SECTOR_BENCHMARKS",
        [PYTHON, "-m", "scripts.refresh_sector_benchmarks"],
        timeout=300,
    )


async def job_outcomes() -> None:
    """Compute outcomes for past debates."""
    args = [PYTHON, "-m", "scripts.compute_outcomes"]
    if DRY_RUN:
        args.append("--dry-run")
    await _run_command("OUTCOMES", args, timeout=120)


async def job_watchlist_cleanup() -> None:
    """Clean old watchlist entries."""
    await _run_command(
        "WATCHLIST_CLEANUP",
        [PYTHON, "-c",
         "from src.portfolio import clear_old_watchlist; "
         "c = clear_old_watchlist(14); "
         "print(f'Cleared {c} stale entries')"],
        timeout=30,
    )


# =============================================================================
# Market hours check
# =============================================================================

def is_market_day() -> bool:
    """Quick check: is today a weekday? (Doesn't check holidays.)"""
    from datetime import datetime
    try:
        import pytz
        et = pytz.timezone(TIMEZONE)
        now_et = datetime.now(et)
    except ImportError:
        # Fallback: approximate ET as UTC-4
        from datetime import timedelta
        now_et = datetime.now(timezone.utc) - timedelta(hours=4)
    return now_et.weekday() < 5  # Mon=0, Fri=4


# =============================================================================
# Main scheduler
# =============================================================================

def build_scheduler() -> AsyncIOScheduler:
    """Configure all scheduled jobs."""
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)

    # Intraday: every 30 min, 09:00-17:00 ET, Mon-Fri
    scheduler.add_job(
        job_intraday,
        CronTrigger(
            minute=f"*/{INTRADAY_INTERVAL}",
            hour="9-16",
            day_of_week="mon-fri",
            timezone=TIMEZONE,
        ),
        id="intraday",
        name="Intraday Cycle",
        max_instances=1,
        misfire_grace_time=300,
    )

    # Monday pre-open: 08:00 ET, Monday only (60h lookback for weekend news)
    scheduler.add_job(
        job_monday_preopen,
        CronTrigger(
            hour=8, minute=0,
            day_of_week="mon",
            timezone=TIMEZONE,
        ),
        id="monday_preopen",
        name="Monday Pre-Open Scan",
        max_instances=1,
        misfire_grace_time=600,
    )

    # EOD: 21:00 ET, Mon-Fri
    scheduler.add_job(
        job_eod,
        CronTrigger(
            hour=21, minute=0,
            day_of_week="mon-fri",
            timezone=TIMEZONE,
        ),
        id="eod",
        name="EOD Cycle",
        max_instances=1,
        misfire_grace_time=600,
    )

    # Sector benchmarks: Monday 06:00 ET
    scheduler.add_job(
        job_sector_benchmarks,
        CronTrigger(
            hour=6, minute=0,
            day_of_week="mon",
            timezone=TIMEZONE,
        ),
        id="sector_benchmarks",
        name="Sector Benchmarks Refresh",
        max_instances=1,
    )

    # Outcomes: daily 00:30 ET
    scheduler.add_job(
        job_outcomes,
        CronTrigger(
            hour=0, minute=30,
            timezone=TIMEZONE,
        ),
        id="outcomes",
        name="Outcome Tracker",
        max_instances=1,
    )

    # Watchlist cleanup: daily 06:30 ET
    scheduler.add_job(
        job_watchlist_cleanup,
        CronTrigger(
            hour=6, minute=30,
            timezone=TIMEZONE,
        ),
        id="watchlist_cleanup",
        name="Watchlist Cleanup",
        max_instances=1,
    )

    return scheduler


async def run_scheduler() -> None:
    """Main entry point — start scheduler and run forever."""
    logger.info("=" * 60)
    logger.info("STOCK ADVISOR SCHEDULER")
    logger.info("=" * 60)
    logger.info(f"Timezone: {TIMEZONE}")
    logger.info(f"Intraday interval: {INTRADAY_INTERVAL} min")
    logger.info(f"Daily cost cap: ${COST_CAP_DAILY:.2f}")
    logger.info(f"Dry run: {DRY_RUN}")
    logger.info(f"Health check port: {PORT}")
    logger.info("")

    # Start API server (FastAPI — serves endpoints + health check)
    start_api_server(PORT)

    # Build and start scheduler
    scheduler = build_scheduler()
    scheduler.start()

    # Log next run times
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        logger.info(f"  {job.name:30} next: {next_run.strftime('%a %H:%M %Z') if next_run else 'N/A'}")
    logger.info("")
    logger.info("Scheduler running. Ctrl+C to stop.")

    # Keep alive
    stop_event = asyncio.Event()

    def handle_signal(*_):
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    await stop_event.wait()
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
