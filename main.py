"""
main.py — Entry point for the signal-based stock screener.

Prints picks to the terminal and sends an email to cfiess@gmail.com.

Usage
-----
  # Run immediately (prints + emails picks)
  python main.py

  # Keep running on a schedule (fires at 8:30 ET on weekdays)
  python main.py --schedule

  # Preview email in terminal without actually sending
  python main.py --dry-run

  # Show verbose scoring detail
  python main.py --verbose

  # Override number of picks
  python main.py --picks 3

Email setup
-----------
  Add to .env:
      GMAIL_USER=your.address@gmail.com
      GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
  (App Password from https://myaccount.google.com/apppasswords)
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
load_dotenv()

from config import NUM_PICKS, SIGNAL_WEIGHTS
from email_sender import send_email
from scorer import rank_candidates
from screener import CandidateStock, run_screen

ET = ZoneInfo("America/New_York")

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Output formatter
# ---------------------------------------------------------------------------

def _divider(char: str = "─", width: int = 60) -> str:
    return char * width


def format_pick(pick: CandidateStock, verbose: bool = False) -> str:
    rank_label = "Primary" if pick.rank == 1 else "Backup"
    company = pick.company_name if pick.company_name else "Unknown Company"
    lines = [
        f"PICK {pick.rank} ({rank_label}): {pick.ticker} — {company}",
        f"Catalyst : {pick.best_catalyst_label} — {pick.best_description}",
        f"Social   : {pick.reddit_summary}",
        f"StockTwits: {pick.stocktwits_summary}",
        f"Sources  : {pick.sources_str}",
        f"Why      : {pick.rank_reason}",
    ]
    if verbose:
        lines.append(f"Score    : {pick.score:.3f}")
    return "\n".join(lines)


def print_picks(picks: list, verbose: bool = False) -> None:
    now = datetime.now(ET)
    date_str = now.strftime("%A %b %-d, %Y")
    time_str = now.strftime("%-I:%M %p ET")

    print()
    print(_divider("═"))
    print(f"  STOCK PICKS — {date_str}  |  {time_str}")
    print(_divider("═"))

    if not picks:
        print("\n  No qualifying picks today.\n")
        print(_divider("═"))
        return

    for pick in picks:
        print()
        print(format_pick(pick, verbose=verbose))

    print()
    print(_divider("─"))
    print("  Signals: Reddit (WSB/stocks/options) · StockTwits · SEC EDGAR · Yahoo News")
    print("  Not financial advice. Do your own due diligence before trading.")
    print(_divider("═"))
    print()


def print_no_picks(reason: str) -> None:
    now = datetime.now(ET)
    print()
    print(_divider("═"))
    print(f"  STOCK PICKS — {now.strftime('%A %b %-d')}  |  No picks today")
    print(_divider("─"))
    print(f"  {reason}")
    print(_divider("═"))
    print()


# ---------------------------------------------------------------------------
# Core job
# ---------------------------------------------------------------------------

def run_job(num_picks: int = NUM_PICKS, verbose: bool = False, dry_run: bool = False) -> None:
    log.info("=" * 60)
    log.info("Signal screener starting — %s", datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"))
    log.info("=" * 60)

    generated_at = datetime.now(ET)
    candidates = run_screen()

    if not candidates:
        reason = "No tickers met the minimum signal thresholds across all sources."
        print_no_picks(reason)
        send_email([], generated_at=generated_at, dry_run=dry_run)
        return

    picks = rank_candidates(candidates, num_picks=num_picks, weights=SIGNAL_WEIGHTS)

    if not picks:
        reason = "Candidates found but scoring returned no picks."
        print_no_picks(reason)
        send_email([], generated_at=generated_at, dry_run=dry_run)
        return

    print_picks(picks, verbose=verbose)
    send_email(picks, generated_at=generated_at, dry_run=dry_run)
    log.info("Done — %d pick(s) printed and emailed", len(picks))


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def _is_weekday(dt: datetime) -> bool:
    return dt.weekday() < 5


def run_scheduler(
    run_hour: int = 8,
    run_minute: int = 30,
    num_picks: int = NUM_PICKS,
    verbose: bool = False,
    dry_run: bool = False,
) -> None:
    log.info("Scheduler started — fires at %02d:%02d ET on weekdays", run_hour, run_minute)
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
                run_job(num_picks=num_picks, verbose=verbose, dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001
                log.exception("Unhandled error in run_job: %s", exc)

        time.sleep(60 - now.second)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Signal-based day-trade screener — Reddit, StockTwits, SEC EDGAR"
    )
    parser.add_argument(
        "--schedule", action="store_true",
        help="Run on a schedule (fires at 9:45 ET weekdays)",
    )
    parser.add_argument(
        "--picks", type=int, default=NUM_PICKS, metavar="N",
        help=f"Number of picks to output (default: {NUM_PICKS})",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show scoring detail for each pick",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print email preview to terminal instead of sending",
    )
    parser.add_argument(
        "--run-hour", type=int, default=8, metavar="HH",
        help="Scheduler hour in ET (default: 8)",
    )
    parser.add_argument(
        "--run-minute", type=int, default=30, metavar="MM",
        help="Scheduler minute in ET (default: 30)",
    )
    args = parser.parse_args()

    if args.schedule:
        try:
            run_scheduler(
                run_hour=args.run_hour,
                run_minute=args.run_minute,
                num_picks=args.picks,
                verbose=args.verbose,
                dry_run=args.dry_run,
            )
        except KeyboardInterrupt:
            log.info("Scheduler stopped")
    else:
        run_job(num_picks=args.picks, verbose=args.verbose, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
