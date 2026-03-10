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

from config import NUM_PICKS, SIGNAL_WEIGHTS, SIGNAL_WEIGHTS_WAY2
from email_sender import send_email
from scorer import rank_candidates
from screener import CandidateStock, run_screen, run_screen_way2

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


def print_picks(picks1: list, picks2: list, verbose: bool = False) -> None:
    now = datetime.now(ET)
    date_str = now.strftime("%A %b %-d, %Y")
    time_str = now.strftime("%-I:%M %p ET")

    print()
    print(_divider("═"))
    print(f"  STOCK PICKS — {date_str}  |  {time_str}")
    print(_divider("═"))

    print(f"\n  WAY 1 — Social Sentiment  (Reddit · StockTwits · SEC EDGAR)")
    print(_divider("─"))
    if picks1:
        for pick in picks1:
            print()
            print(format_pick(pick, verbose=verbose))
        print()
    else:
        print("\n  No picks — Reddit/StockTwits unavailable in this environment.\n")

    print(_divider("═"))
    print(f"\n  WAY 2 — Catalyst + Gappers  (SEC EDGAR · Finviz · Yahoo News)")
    print(_divider("─"))
    if picks2:
        for pick in picks2:
            print()
            print(format_pick(pick, verbose=verbose))
        print()
    else:
        print("\n  No picks today.\n")

    print(_divider("─"))
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

    # Way 1: social sentiment + SEC (Reddit/StockTwits may be blocked in GitHub Actions)
    candidates1 = run_screen()
    picks1 = rank_candidates(candidates1, num_picks=num_picks, weights=SIGNAL_WEIGHTS) if candidates1 else []

    # Way 2: SEC + Finviz gappers + Yahoo News (works in all environments)
    candidates2 = run_screen_way2()
    picks2 = rank_candidates(candidates2, num_picks=num_picks, weights=SIGNAL_WEIGHTS_WAY2) if candidates2 else []

    print_picks(picks1, picks2, verbose=verbose)
    send_email(picks1, picks2, generated_at=generated_at, dry_run=dry_run)
    log.info("Done — Way 1: %d pick(s), Way 2: %d pick(s)", len(picks1), len(picks2))


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
