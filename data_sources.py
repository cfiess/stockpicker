"""
data_sources.py — Market data retrieval layer.

Provides:
  - get_gappers()          Gap-up candidates from Finviz screener
  - get_spy_status()       SPY % change on the day
  - get_stock_data()       Price, OHLCV, float, and gap info for a ticker
  - get_news_headlines()   Recent headlines for catalyst detection
  - get_relative_volume()  Today's relvol vs historical average at same time

Primary sources:  Yahoo Finance (yfinance), Finviz (HTML scrape)
Optional source:  Alpaca Markets (real-time quotes)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from bs4 import BeautifulSoup

from config import (
    CATALYST_KEYWORDS,
    FINVIZ_SCREENER_URL,
    MAX_CANDIDATES,
    MIN_GAP_PCT,
    RELVOL_LOOKBACK_DAYS,
    RELVOL_SNAPSHOT_HOUR,
    RELVOL_SNAPSHOT_MINUTE,
    SPY_TICKER,
)

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
)


def _safe_float(value, default: float = 0.0) -> float:
    """Convert a value to float, returning *default* on failure."""
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Finviz gapper screener
# ---------------------------------------------------------------------------

def get_gappers(min_gap_pct: float = MIN_GAP_PCT) -> List[str]:
    """
    Return a list of ticker symbols that gapped up >= *min_gap_pct* today,
    pulled from the Finviz screener.  Falls back to an empty list on error.
    """
    url = FINVIZ_SCREENER_URL
    tickers: List[str] = []

    try:
        resp = _SESSION.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Finviz results table rows contain ticker links
        table = soup.find("table", {"id": "screener-views-table"})
        if table is None:
            # Try the newer Finviz layout
            table = soup.find("table", class_="screener_table")

        if table is None:
            log.warning("Finviz: could not locate results table — HTML layout may have changed")
            return []

        rows = table.find_all("tr")[1:]  # skip header row
        for row in rows[:MAX_CANDIDATES]:
            cells = row.find_all("td")
            if not cells:
                continue
            ticker_cell = cells[1] if len(cells) > 1 else cells[0]
            ticker = ticker_cell.get_text(strip=True)
            if ticker:
                tickers.append(ticker)

        log.info("Finviz returned %d gapper candidates", len(tickers))

    except requests.RequestException as exc:
        log.error("Finviz request failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.error("Finviz parse error: %s", exc)

    return tickers


# ---------------------------------------------------------------------------
# SPY status
# ---------------------------------------------------------------------------

def get_spy_status() -> Tuple[float, float]:
    """
    Return (spy_pct_change, spy_price) for today's session.

    spy_pct_change is positive when SPY is up on the day.
    """
    try:
        spy = yf.Ticker(SPY_TICKER)
        hist = spy.history(period="2d", interval="1m")
        if hist.empty:
            log.warning("SPY: empty history returned")
            return 0.0, 0.0

        # Previous trading day's close
        today_et = datetime.now(ET).date()
        today_bars = hist[hist.index.date == today_et]
        prev_bars = hist[hist.index.date < today_et]

        if today_bars.empty or prev_bars.empty:
            return 0.0, 0.0

        prev_close = float(prev_bars["Close"].iloc[-1])
        current_price = float(today_bars["Close"].iloc[-1])
        pct_change = (current_price - prev_close) / prev_close

        log.info("SPY: %.2f%% (price $%.2f)", pct_change * 100, current_price)
        return pct_change, current_price

    except Exception as exc:  # noqa: BLE001
        log.error("SPY data error: %s", exc)
        return 0.0, 0.0


# ---------------------------------------------------------------------------
# Per-stock data
# ---------------------------------------------------------------------------

def get_stock_data(ticker: str) -> Optional[Dict]:
    """
    Fetch intraday price data for *ticker* and compute gap metrics.

    Returns a dict with keys:
        ticker, open, prev_close, high, low, current_price,
        gap_pct, gap_held (bool), float_shares, volume_today,
        avg_volume_at_snapshot (float)

    Returns None if data is unavailable.
    """
    try:
        tk = yf.Ticker(ticker)
        info = tk.fast_info  # lightweight version — avoids heavy scraping

        # Historical data for previous close
        hist_daily = tk.history(period="2d", interval="1d")
        if len(hist_daily) < 2:
            log.debug("%s: insufficient daily history", ticker)
            return None

        prev_close = float(hist_daily["Close"].iloc[-2])
        today_open = float(hist_daily["Open"].iloc[-1])

        # Intraday 1-minute bars for today
        today_1m = tk.history(period="1d", interval="1m")
        if today_1m.empty:
            return None

        current_price = float(today_1m["Close"].iloc[-1])
        high_of_day = float(today_1m["High"].max())
        low_of_day = float(today_1m["Low"].min())
        volume_today = int(today_1m["Volume"].sum())

        gap_pct = (today_open - prev_close) / prev_close if prev_close > 0 else 0.0

        # "Gap held" = current price is still above the previous close
        # and hasn't filled more than half the gap
        gap_dollars = today_open - prev_close
        gap_held = (current_price > prev_close) and (
            current_price >= prev_close + gap_dollars * 0.5
        )

        # Float shares (may be None for some tickers)
        try:
            float_shares = getattr(info, "shares_outstanding", None) or 0
            float_shares = int(float_shares)
        except Exception:
            float_shares = 0

        return {
            "ticker": ticker,
            "open": today_open,
            "prev_close": prev_close,
            "high": high_of_day,
            "low": low_of_day,
            "current_price": current_price,
            "gap_pct": gap_pct,
            "gap_held": gap_held,
            "float_shares": float_shares,
            "volume_today": volume_today,
        }

    except Exception as exc:  # noqa: BLE001
        log.error("%s: data fetch error — %s", ticker, exc)
        return None


# ---------------------------------------------------------------------------
# Relative volume
# ---------------------------------------------------------------------------

def get_relative_volume(
    ticker: str,
    volume_today: int,
    lookback_days: int = RELVOL_LOOKBACK_DAYS,
) -> float:
    """
    Return the relative volume ratio:
        today's volume (since open) / avg volume at same elapsed time
        over the past *lookback_days* trading days.

    A value of 2.0 means 2× average volume — our minimum threshold.
    """
    try:
        now_et = datetime.now(ET)
        snapshot_minutes_since_open = (
            (RELVOL_SNAPSHOT_HOUR - 9) * 60
            + RELVOL_SNAPSHOT_MINUTE
            - 30  # market opens at 9:30
        )

        # Fetch enough historical daily data
        tk = yf.Ticker(ticker)
        hist_1m = tk.history(
            period=f"{lookback_days + 5}d",
            interval="1m",
            prepost=False,
        )
        if hist_1m.empty:
            return 0.0

        today_date = now_et.date()
        past_volumes: List[float] = []

        # For each past trading day, sum volume from open to snapshot time
        trading_days = sorted(
            {d for d in hist_1m.index.date if d < today_date},
            reverse=True,
        )

        for day in trading_days[:lookback_days]:
            day_bars = hist_1m[hist_1m.index.date == day]
            if day_bars.empty:
                continue

            open_time = day_bars.index[0]
            cutoff = open_time + timedelta(minutes=snapshot_minutes_since_open)
            window_bars = day_bars[day_bars.index <= cutoff]

            if not window_bars.empty:
                past_volumes.append(float(window_bars["Volume"].sum()))

        if not past_volumes:
            return 0.0

        avg_vol = sum(past_volumes) / len(past_volumes)
        if avg_vol == 0:
            return 0.0

        rel_vol = volume_today / avg_vol
        log.debug("%s: rel_vol=%.2f (today=%d, avg=%d)", ticker, rel_vol, volume_today, avg_vol)
        return rel_vol

    except Exception as exc:  # noqa: BLE001
        log.error("%s: rel_vol error — %s", ticker, exc)
        return 0.0


# ---------------------------------------------------------------------------
# News / catalyst detection
# ---------------------------------------------------------------------------

def get_news_headlines(ticker: str, max_headlines: int = 5) -> List[Dict]:
    """
    Return a list of recent news items from Yahoo Finance for *ticker*.

    Each item: {"title": str, "publisher": str, "link": str}
    """
    try:
        tk = yf.Ticker(ticker)
        news = tk.news or []
        results = []
        for item in news[:max_headlines]:
            results.append(
                {
                    "title": item.get("content", {}).get("title", ""),
                    "publisher": item.get("content", {}).get("provider", {}).get("displayName", ""),
                    "link": item.get("content", {}).get("canonicalUrl", {}).get("url", ""),
                }
            )
        return results
    except Exception as exc:  # noqa: BLE001
        log.error("%s: news fetch error — %s", ticker, exc)
        return []


def classify_catalyst(headlines: List[Dict]) -> Tuple[str, str]:
    """
    Given a list of headline dicts, return (catalyst_type, summary).

    catalyst_type is one of: earnings | fda | analyst_upgrade | acquisition | unknown
    summary is the best-match headline title (truncated).
    """
    for item in headlines:
        title_lower = item["title"].lower()
        for catalyst_type, keywords in CATALYST_KEYWORDS.items():
            if any(kw in title_lower for kw in keywords):
                summary = item["title"][:120]
                return catalyst_type, summary

    # Fallback: return first headline
    if headlines:
        return "unknown", headlines[0]["title"][:120]
    return "unknown", "No recent headline found"


# ---------------------------------------------------------------------------
# Alpaca optional integration
# ---------------------------------------------------------------------------

def get_alpaca_quote(ticker: str) -> Optional[Dict]:
    """
    Fetch real-time quote from Alpaca if credentials are configured.
    Returns None if Alpaca is not configured or on error.

    Returned dict: {"ask": float, "bid": float, "last": float}
    """
    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    if not api_key or not secret_key:
        return None

    data_url = "https://data.alpaca.markets/v2/stocks/{}/quotes/latest".format(ticker)
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}

    try:
        resp = requests.get(data_url, headers=headers, timeout=10)
        resp.raise_for_status()
        payload = resp.json().get("quote", {})
        return {
            "ask": _safe_float(payload.get("ap")),
            "bid": _safe_float(payload.get("bp")),
            "last": _safe_float(payload.get("ap")),  # use ask as proxy for last
        }
    except Exception as exc:  # noqa: BLE001
        log.debug("Alpaca quote error for %s: %s", ticker, exc)
        return None
