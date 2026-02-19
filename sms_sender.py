"""
sms_sender.py — Twilio SMS integration and message formatting.

Message format (fits comfortably within 1600 chars):

  📊 STOCK PICKS — Wed Feb 19

  SPY: +0.42%

  PICK 1 (Primary): NVDA
  Catalyst: Earnings Beat — quarterly EPS smashed estimates
  RelVol: 4.2x | Gap: +6.8%
  Entry: $875.50–$884.25
  Stop: $850.10
  Why: Ranked #1 for a high-conviction Earnings Beat catalyst...

  PICK 2 (Backup): MRNA
  Catalyst: FDA Catalyst — BLA approval for new mRNA therapy
  RelVol: 3.1x | Gap: +5.2%
  Entry: $95.20–$96.15
  Stop: $88.40
  Why: Ranked #2 as the strongest backup...

  Generated 9:47 ET
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from config import SMS_MAX_LENGTH
from screener import CandidateStock

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Message formatter
# ---------------------------------------------------------------------------

def _format_pick(pick: CandidateStock) -> str:
    """Build the text block for a single pick."""
    rank_label = "Primary" if pick.rank == 1 else "Backup"

    lines = [
        f"PICK {pick.rank} ({rank_label}): {pick.ticker}",
        f"Catalyst: {pick.catalyst_label} — {pick.catalyst_summary}",
        f"RelVol: {pick.rel_vol_str} | Gap: +{pick.gap_pct_str}",
        f"Entry: {pick.entry_zone_str}",
        f"Stop: ${pick.stop_loss:.2f}",
        f"Why: {pick.rank_reason}",
    ]
    return "\n".join(lines)


def build_sms_body(
    picks: List[CandidateStock],
    spy_pct: float,
    spy_price: float,
    generated_at: Optional[datetime] = None,
) -> str:
    """
    Compose the full SMS body from ranked picks.

    Truncates gracefully if the total exceeds SMS_MAX_LENGTH.
    """
    if generated_at is None:
        generated_at = datetime.now(ET)

    date_str = generated_at.strftime("%a %b %-d")
    time_str = generated_at.strftime("%-I:%M %p ET")
    spy_sign = "+" if spy_pct >= 0 else ""
    spy_str = f"SPY: {spy_sign}{spy_pct * 100:.2f}%"

    header = f"STOCK PICKS — {date_str}\n{spy_str}"

    pick_blocks = [_format_pick(p) for p in picks]
    body_parts = [header] + pick_blocks + [f"Generated {time_str}"]
    body = "\n\n".join(body_parts)

    if len(body) > SMS_MAX_LENGTH:
        log.warning(
            "SMS body (%d chars) exceeds limit (%d) — truncating",
            len(body),
            SMS_MAX_LENGTH,
        )
        body = body[: SMS_MAX_LENGTH - 4] + " ..."

    return body


def build_no_picks_body(spy_pct: float, reason: str) -> str:
    """SMS sent when the screener finds no qualifying candidates."""
    date_str = datetime.now(ET).strftime("%a %b %-d")
    spy_sign = "+" if spy_pct >= 0 else ""
    return (
        f"STOCK PICKS — {date_str}\n\n"
        f"No picks today. {reason}\n"
        f"SPY: {spy_sign}{spy_pct * 100:.2f}%"
    )


# ---------------------------------------------------------------------------
# Twilio sender
# ---------------------------------------------------------------------------

def send_sms(body: str, dry_run: bool = False) -> bool:
    """
    Send *body* via Twilio SMS.

    Reads credentials from environment:
        TWILIO_ACCOUNT_SID
        TWILIO_AUTH_TOKEN
        TWILIO_FROM_NUMBER
        TWILIO_TO_NUMBER

    When *dry_run* is True, prints the message instead of sending.

    Returns True on success, False on failure.
    """
    if dry_run:
        log.info("DRY RUN — SMS not sent. Message:\n%s", body)
        print("\n" + "=" * 60)
        print("DRY RUN — SMS MESSAGE PREVIEW")
        print("=" * 60)
        print(body)
        print("=" * 60 + "\n")
        return True

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_FROM_NUMBER")
    to_number = os.getenv("TWILIO_TO_NUMBER")

    missing = [
        name
        for name, val in {
            "TWILIO_ACCOUNT_SID": account_sid,
            "TWILIO_AUTH_TOKEN": auth_token,
            "TWILIO_FROM_NUMBER": from_number,
            "TWILIO_TO_NUMBER": to_number,
        }.items()
        if not val
    ]

    if missing:
        log.error("Missing Twilio env vars: %s", ", ".join(missing))
        return False

    try:
        from twilio.rest import Client  # imported lazily so the app can run in dry-run mode without Twilio installed

        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=body,
            from_=from_number,
            to=to_number,
        )
        log.info("SMS sent — SID: %s | status: %s", message.sid, message.status)
        return True

    except ImportError:
        log.error(
            "twilio package not installed. Run: pip install twilio"
        )
        return False
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send SMS: %s", exc)
        return False
