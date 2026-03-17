"""
email_sender.py — Gmail SMTP email delivery for daily stock picks.

Setup (one-time)
----------------
1. Enable 2-Step Verification on your Google account.
2. Go to https://myaccount.google.com/apppasswords and create an App Password
   (name it "Stock Screener" or anything you like).
3. Add to your .env file:
       GMAIL_USER=your.address@gmail.com
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

The email is sent as multipart/alternative (HTML + plain-text fallback).
"""

from __future__ import annotations

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional
from zoneinfo import ZoneInfo

from config import EMAIL_TO
from screener import CandidateStock

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587


# ---------------------------------------------------------------------------
# Plain-text formatter
# ---------------------------------------------------------------------------

def _format_pick_text(pick: CandidateStock) -> str:
    rank_label = "Primary" if pick.rank == 1 else "Backup"
    company = pick.company_name or "Unknown Company"
    lines = [
        f"PICK {pick.rank} ({rank_label}): {pick.ticker} — {company}",
        f"  Catalyst  : {pick.best_catalyst_label} — {pick.best_description}",
        f"  Social    : {pick.reddit_summary}",
        f"  StockTwits: {pick.stocktwits_summary}",
        f"  Sources   : {pick.sources_str}",
        f"  Why       : {pick.rank_reason}",
        f"  Score     : {pick.score:.3f}",
    ]
    return "\n".join(lines)


def build_plain_text(
    picks1: List[CandidateStock],
    picks2: List[CandidateStock],
    generated_at: Optional[datetime] = None,
) -> str:
    if generated_at is None:
        generated_at = datetime.now(ET)
    date_str = generated_at.strftime("%A %b %-d, %Y")
    time_str = generated_at.strftime("%-I:%M %p ET")

    lines = [
        f"STOCK PICKS — {date_str}  |  Generated {time_str}",
        "=" * 62,
        "",
        "WAY 1 — Social Sentiment  (Reddit · StockTwits · SEC EDGAR)",
        "-" * 62,
    ]
    if picks1:
        for pick in picks1:
            lines.append(_format_pick_text(pick))
            lines.append("")
    else:
        lines.append("No picks — Reddit/StockTwits unavailable in this environment.")
        lines.append("")

    lines += [
        "=" * 62,
        "",
        "WAY 2 — Catalyst + Gappers  (SEC EDGAR · Finviz · Yahoo News)",
        "-" * 62,
    ]
    if picks2:
        for pick in picks2:
            lines.append(_format_pick_text(pick))
            lines.append("")
    else:
        lines.append("No picks today.")
        lines.append("")

    lines += [
        "-" * 62,
        "Not financial advice. Do your own due diligence before trading.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML formatter
# ---------------------------------------------------------------------------

_CATALYST_COLORS = {
    "earnings":        "#2ecc71",
    "fda":             "#9b59b6",
    "analyst_upgrade": "#3498db",
    "acquisition":     "#e67e22",
    "deal":            "#1abc9c",
    "unknown":         "#95a5a6",
}


def _pick_html(pick: CandidateStock) -> str:
    rank_label = "Primary" if pick.rank == 1 else "Backup"
    company = pick.company_name or "Unknown Company"
    color = _CATALYST_COLORS.get(pick.best_catalyst_type, "#95a5a6")
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;border-radius:8px;overflow:hidden;border:1px solid #e0e0e0;">
      <tr>
        <td style="background:{color};padding:10px 18px;">
          <span style="color:#fff;font-size:18px;font-weight:700;font-family:monospace;">{pick.ticker}</span>
          <span style="color:rgba(255,255,255,0.85);font-size:13px;margin-left:10px;">
            Pick {pick.rank} &middot; {rank_label} &middot; {company}
          </span>
        </td>
      </tr>
      <tr>
        <td style="background:#fff;padding:14px 18px;font-family:Arial,sans-serif;font-size:14px;color:#333;line-height:1.7;">
          <b>Catalyst</b>&nbsp;&nbsp; {pick.best_catalyst_label} &mdash; {pick.best_description}<br>
          <b>Social</b>&nbsp;&nbsp;&nbsp;&nbsp; {pick.reddit_summary}<br>
          <b>StockTwits</b> {pick.stocktwits_summary}<br>
          <b>Sources</b>&nbsp;&nbsp;&nbsp; {pick.sources_str}<br>
          <b>Why</b>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {pick.rank_reason}<br>
          <b>Score</b>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; {pick.score:.3f}
        </td>
      </tr>
    </table>"""


def _section_header_html(title: str) -> str:
    return f"""
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">
      <tr>
        <td style="background:#2c3e50;padding:8px 18px;border-radius:6px;">
          <span style="color:#ecf0f1;font-size:13px;font-weight:700;font-family:Arial,sans-serif;letter-spacing:0.5px;">
            {title}
          </span>
        </td>
      </tr>
    </table>"""


def _no_picks_html(message: str) -> str:
    return (
        f'<p style="font-family:Arial,sans-serif;color:#999;font-size:14px;'
        f'padding:8px 18px;margin:0 0 20px 0;">{message}</p>'
    )


def build_html(
    picks1: List[CandidateStock],
    picks2: List[CandidateStock],
    generated_at: Optional[datetime] = None,
) -> str:
    if generated_at is None:
        generated_at = datetime.now(ET)
    date_str = generated_at.strftime("%A %b %-d, %Y")
    time_str = generated_at.strftime("%-I:%M %p ET")

    way1_html = (
        "".join(_pick_html(p) for p in picks1)
        if picks1
        else _no_picks_html("No picks &mdash; Reddit/StockTwits unavailable in this environment.")
    )
    way2_html = (
        "".join(_pick_html(p) for p in picks2)
        if picks2
        else _no_picks_html("No picks today.")
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f6f8;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:30px 0;">
    <tr>
      <td align="center">
        <table width="620" cellpadding="0" cellspacing="0">

          <!-- Header -->
          <tr>
            <td style="background:#1a1a2e;border-radius:8px 8px 0 0;padding:20px 24px;">
              <span style="color:#fff;font-size:22px;font-weight:700;font-family:Arial,sans-serif;">
                Stock Picks
              </span>
              <span style="color:#aaa;font-size:13px;font-family:Arial,sans-serif;margin-left:12px;">
                {date_str} &middot; Generated {time_str}
              </span>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="background:#f4f6f8;padding:20px 0;">
              {_section_header_html("WAY 1 &mdash; Social Sentiment &nbsp;&middot;&nbsp; Reddit &middot; StockTwits &middot; SEC EDGAR")}
              {way1_html}
              {_section_header_html("WAY 2 &mdash; Catalyst + Gappers &nbsp;&middot;&nbsp; SEC EDGAR &middot; Finviz &middot; Yahoo News")}
              {way2_html}
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#fff;border-radius:0 0 8px 8px;border:1px solid #e0e0e0;
                        padding:14px 18px;font-family:Arial,sans-serif;font-size:12px;color:#999;line-height:1.6;">
              <b>Not financial advice.</b> Do your own due diligence before trading.
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------

def send_email(
    picks1: List[CandidateStock],
    picks2: List[CandidateStock],
    generated_at: Optional[datetime] = None,
    dry_run: bool = False,
) -> bool:
    """
    Send the picks email to EMAIL_TO via Gmail SMTP.

    Requires env vars:
        GMAIL_USER          -- your Gmail address
        GMAIL_APP_PASSWORD  -- 16-char App Password (not your account password)

    When dry_run=True prints the plain-text preview instead of sending.
    Returns True on success, False on failure.
    """
    if generated_at is None:
        generated_at = datetime.now(ET)

    date_str = generated_at.strftime("%a %b %-d, %Y")
    subject = f"Stock Picks -- {date_str}"

    plain = build_plain_text(picks1, picks2, generated_at)
    html = build_html(picks1, picks2, generated_at)

    if dry_run:
        log.info("DRY RUN -- email not sent. Preview:\n%s", plain)
        print("\n" + "=" * 62)
        print("DRY RUN -- EMAIL PREVIEW")
        print("=" * 62)
        print(plain)
        print("=" * 62 + "\n")
        return True

    gmail_user = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")

    missing = [
        name for name, val in {
            "GMAIL_USER": gmail_user,
            "GMAIL_APP_PASSWORD": gmail_password,
        }.items() if not val
    ]
    if missing:
        log.error("Missing env vars for email: %s", ", ".join(missing))
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, EMAIL_TO, msg.as_string())
        log.info("Email sent to %s | subject: %s", EMAIL_TO, subject)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send email: %s", exc)
        return False
