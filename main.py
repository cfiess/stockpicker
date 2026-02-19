"""
main.py — Entry point and scheduler for the stock screener.

Usage
-----
  # Run once immediately (useful for testing / manual trigger)
  python main.py --now

  # Run in dry-run mode (no SMS sent, output printed to stdout)
  python main.py --now --dry-run

  # Start the scheduler (runs every weekday at 9:45 ET)
  python main.py --schedule

  # Override number of picks
  python main.py --now --picks 3

Scheduling note
---------------
The built-in scheduler keeps the process alive and fires the job at 9:45 ET
every weekday.  For production deployments, a system cron entry is simpler
and more reliable (see README.md for the recommended cron setup).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Load .env before importing anything that reads env vars
load_dotenv()

from config import NUM_PICKS, WEIGHTS
from scorer import rank_candidates
from screener import run_screen
from sms_sender import build_no_picks_body, build_sms_body, send_sms

ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Core job
# ---------------------------------------------------------------------------

def run_job(dry_run: bool = False, num_picks: int = NUM_PICKS) -> None:
    """
    Execute the full screen → rank → SMS pipeline.

    This function is idempotent: safe to call multiple times.
    """
    log.info("=" * 60)
    log.info("Stock screener starting — %s", datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"))
    log.info("=" * 60)

    # ── 1. Screen ─────────────────────────────────────────────────────────
    candidates, spy_pct, spy_price = run_screen()

    # ── 2. Handle no-candidates cases ─────────────────────────────────────
    if not candidates:
        if spy_pct < 0:
            reason = f"SPY is down {spy_pct * 100:.2f}% — unfavourable tape for long setups."
        else:
            reason = "No stocks met all three criteria (gap 2%+, catalyst, 2× volume)."

        body = build_no_picks_body(spy_pct, reason)
        log.info("No qualified candidates — sending no-picks SMS")
        send_sms(body, dry_run=dry_run)
        return

    # ── 3. Score and rank ─────────────────────────────────────────────────
    picks = rank_candidates(candidates, num_picks=num_picks, weights=WEIGHTS)

    if not picks:
        log.warning("rank_candidates returned empty list — aborting")
        return

    # ── 4. Format and send SMS ────────────────────────────────────────────
    body = build_sms_body(picks, spy_pct=spy_pct, spy_price=spy_price)
    success = send_sms(body, dry_run=dry_run)

    if success:
        log.info("Job complete — %d pick(s) delivered", len(picks))
    else:
        log.error("Job complete — SMS delivery FAILED")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5  # Mon=0 … Fri=4


def run_scheduler(
    run_hour: int = 9,
    run_minute: int = 45,
    dry_run: bool = False,
    num_picks: int = NUM_PICKS,
) -> None:
    """
    Block indefinitely, executing run_job() at *run_hour*:*run_minute* ET
    on each weekday.

    The scheduler polls once per minute to keep resource usage minimal.
    """
    log.info(
        "Scheduler started — will fire at %02d:%02d ET on weekdays",
        run_hour,
        run_minute,
    )

    last_run_date = None

    while True:
        now = datetime.now(ET)

        if (
            _is_weekday(now)
            and now.hour == run_hour
            and now.minute == run_minute
            and now.date() != last_run_date
        ):
            last_run_date = now.date()
            try:
                run_job(dry_run=dry_run, num_picks=num_picks)
            except Exception as exc:  # noqa: BLE001
                log.exception("Unhandled error in run_job: %s", exc)

        # Sleep until the next minute boundary
        sleep_seconds = 60 - now.second
        time.sleep(sleep_seconds)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Intraday stock screener — runs at 9:45 ET and sends SMS picks"
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--now",
        action="store_true",
        help="Run the screener immediately (once) and exit",
    )
    mode.add_argument(
        "--schedule",
        action="store_true",
        help="Start the scheduler (blocks until Ctrl-C)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.getenv("DRY_RUN", "false").lower() == "true",
        help="Print SMS to stdout instead of sending via Twilio",
    )
    parser.add_argument(
        "--picks",
        type=int,
        default=NUM_PICKS,
        metavar="N",
        help=f"Number of picks to include (default: {NUM_PICKS})",
    )
    parser.add_argument(
        "--run-hour",
        type=int,
        default=9,
        metavar="HH",
        help="Scheduler fire hour in ET (default: 9)",
    )
    parser.add_argument(
        "--run-minute",
        type=int,
        default=45,
        metavar="MM",
        help="Scheduler fire minute in ET (default: 45)",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.now:
        run_job(dry_run=args.dry_run, num_picks=args.picks)
    elif args.schedule:
        try:
            run_scheduler(
                run_hour=args.run_hour,
                run_minute=args.run_minute,
                dry_run=args.dry_run,
                num_picks=args.picks,
            )
        except KeyboardInterrupt:
            log.info("Scheduler stopped by user")


if __name__ == "__main__":
    main()
