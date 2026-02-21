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
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import re

import requests
import yfinance as yf
from bs4 import BeautifulSoup

from config import (
    CATALYST_KEYWORDS,
    FINVIZ_SCREENER_URL,
    MAX_CANDIDATES,
    MIN_GAP_PCT,
    RELVOL_LOOKBACK_DAYS,
    SPY_TICKER,
    YFINANCE_BACKOFF_BASE,
    YFINANCE_RETRIES,
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


def _yf_with_retry(fn, *args, **kwargs):
    """
    Call a yfinance function with exponential backoff on rate-limit errors.

    Retries up to YFINANCE_RETRIES times.  Waits YFINANCE_BACKOFF_BASE,
    then 2×, 4×, 8× that value between attempts.
    """
    last_exc: Exception = RuntimeError("unknown yfinance error")
    for attempt in range(YFINANCE_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            msg = str(exc).lower()
            is_rate_limit = any(
                phrase in msg
                for phrase in ("too many requests", "rate limit", "rate limited", "429")
            )
            if is_rate_limit and attempt < YFINANCE_RETRIES - 1:
                delay = YFINANCE_BACKOFF_BASE * (2 ** attempt)
                log.warning(
                    "yfinance rate limited — waiting %.0fs (attempt %d/%d)",
                    delay,
                    attempt + 1,
                    YFINANCE_RETRIES,
                )
                time.sleep(delay)
            else:
                raise
    raise last_exc


def _yf_download_with_retry(tickers: List[str], **kwargs):
    """
    Wrapper around yf.download() that retries when the result is empty.

    yf.download() does NOT raise an exception on rate-limit errors — it
    silently swallows them and returns an empty DataFrame.  This wrapper
    detects that case and retries with backoff.
    """
    import pandas as pd

    for attempt in range(YFINANCE_RETRIES):
        result = yf.download(tickers=tickers, progress=False, **kwargs)

        # Success if at least one ticker has non-NaN data
        if not result.empty and result.notna().any().any():
            return result

        if attempt < YFINANCE_RETRIES - 1:
            delay = YFINANCE_BACKOFF_BASE * (2 ** attempt)
            log.warning(
                "yf.download returned no data (rate limited?) — "
                "waiting %.0fs before retry %d/%d",
                delay,
                attempt + 1,
                YFINANCE_RETRIES,
            )
            time.sleep(delay)
        else:
            log.error(
                "yf.download failed after %d attempts — still no data", YFINANCE_RETRIES
            )

    return result  # return empty DataFrame as last resort


# ---------------------------------------------------------------------------
# Finviz gapper screener
# ---------------------------------------------------------------------------

_TICKER_RE = re.compile(r'^[A-Z]{1,6}$')


def _is_valid_ticker(text: str) -> bool:
    """Return True if *text* looks like a real stock ticker symbol."""
    return bool(_TICKER_RE.match(text.strip()))


def get_gappers(min_gap_pct: float = MIN_GAP_PCT) -> List[str]:
    """
    Return a list of ticker symbols that gapped up >= *min_gap_pct* today,
    pulled from the Finviz screener.  Falls back to an empty list on error.

    Finviz ticker links reliably carry class="screener-link-primary".
    We also fall back to href pattern matching in case the class changes.
    A final sanity filter rejects anything that isn't 1-6 uppercase letters.
    """
    tickers: List[str] = []

    try:
        resp = _SESSION.get(FINVIZ_SCREENER_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Primary strategy: Finviz ticker links use class="screener-link-primary"
        links = soup.find_all("a", class_="screener-link-primary")

        # Fallback: find by href pattern (?t=TICKER or quote.ashx?t=TICKER)
        if not links:
            log.debug("Finviz: screener-link-primary not found, trying href pattern")
            links = soup.find_all(
                "a", href=re.compile(r"quote\.ashx\?t=[A-Z]")
            )

        for link in links:
            ticker = link.get_text(strip=True)
            if _is_valid_ticker(ticker) and ticker not in tickers:
                tickers.append(ticker)
            if len(tickers) >= MAX_CANDIDATES:
                break

        if not tickers:
            log.warning(
                "Finviz: zero valid tickers extracted — HTML layout may have changed. "
                "Check https://finviz.com/screener.ashx manually."
            )
        else:
            log.info("Finviz returned %d gapper candidates: %s", len(tickers), tickers)

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
        hist = _yf_with_retry(spy.history, period="2d", interval="1m")
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
# Batch price data (replaces per-ticker get_stock_data calls)
# ---------------------------------------------------------------------------

def _extract_ticker_df(raw, ticker: str, n_tickers: int):
    """
    Safely extract a single-ticker sub-DataFrame from a yf.download() result.

    yfinance returns a flat DataFrame for 1 ticker, and a MultiIndex DataFrame
    for multiple tickers where the top level is the ticker symbol.
    """
    if n_tickers == 1:
        return raw
    try:
        sub = raw[ticker]
        # Drop rows where all OHLCV columns are NaN (some tickers have sparse data)
        return sub.dropna(how="all")
    except KeyError:
        return None


def batch_fetch_price_data(tickers: List[str]) -> Dict[str, Dict]:
    """
    Download 5-day 1-minute bars for ALL tickers in a single API call.

    Returns a dict mapping ticker → stock_data_dict with the same keys as
    the old get_stock_data() so the rest of the pipeline is unchanged.
    """
    if not tickers:
        return {}

    unique = list(dict.fromkeys(tickers))  # preserve order, remove dupes
    log.info("Batch downloading 1m intraday data for %d tickers…", len(unique))

    try:
        raw = _yf_download_with_retry(
            unique,
            period="5d",
            interval="1m",
            group_by="ticker",
            auto_adjust=True,
            prepost=False,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Batch 1m download failed: %s", exc)
        return {}

    today_et = datetime.now(ET).date()
    result: Dict[str, Dict] = {}

    for ticker in unique:
        try:
            hist = _extract_ticker_df(raw, ticker, len(unique))
            if hist is None or hist.empty:
                log.debug("%s: no data in batch result", ticker)
                continue

            today_bars = hist[hist.index.date == today_et]
            prev_bars = hist[hist.index.date < today_et]

            if today_bars.empty or prev_bars.empty:
                log.debug("%s: missing today or prev bars", ticker)
                continue

            prev_close = float(prev_bars["Close"].iloc[-1])
            today_open = float(today_bars["Open"].iloc[0])
            current_price = float(today_bars["Close"].iloc[-1])
            high_of_day = float(today_bars["High"].max())
            low_of_day = float(today_bars["Low"].min())
            volume_today = int(today_bars["Volume"].sum())

            gap_pct = (today_open - prev_close) / prev_close if prev_close > 0 else 0.0
            gap_dollars = today_open - prev_close
            gap_held = (current_price > prev_close) and (
                current_price >= prev_close + gap_dollars * 0.5
            )

            result[ticker] = {
                "ticker": ticker,
                "open": today_open,
                "prev_close": prev_close,
                "high": high_of_day,
                "low": low_of_day,
                "current_price": current_price,
                "gap_pct": gap_pct,
                "gap_held": gap_held,
                "float_shares": 0,  # fetched separately via fast_info if needed
                "volume_today": volume_today,
            }
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: batch parse error — %s", ticker, exc)

    log.info(
        "Batch 1m download complete: %d/%d tickers with data",
        len(result), len(unique),
    )
    return result


def batch_fetch_avg_daily_volume(
    tickers: List[str],
    lookback_days: int = RELVOL_LOOKBACK_DAYS,
) -> Dict[str, float]:
    """
    Download 30-day daily bars for ALL tickers in a single API call.

    Returns a dict mapping ticker → average full-day volume over *lookback_days*.
    Used by compute_rel_vol() below.
    """
    if not tickers:
        return {}

    unique = list(dict.fromkeys(tickers))
    log.info("Batch downloading daily volume data for %d tickers…", len(unique))

    try:
        raw = _yf_download_with_retry(
            unique,
            period="30d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Batch daily download failed: %s", exc)
        return {}

    today_et = datetime.now(ET).date()
    result: Dict[str, float] = {}

    for ticker in unique:
        try:
            hist = _extract_ticker_df(raw, ticker, len(unique))
            if hist is None or hist.empty:
                continue

            past = hist[hist.index.date < today_et]
            if past.empty:
                continue

            avg_vol = float(past["Volume"].tail(lookback_days).mean())
            result[ticker] = avg_vol
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: daily batch parse error — %s", ticker, exc)

    log.info(
        "Batch daily download complete: %d/%d tickers with volume",
        len(result), len(unique),
    )
    return result


def compute_rel_vol(volume_today: int, avg_daily_vol: float) -> float:
    """
    Compute relative volume from pre-fetched average daily volume.

    Scales avg_daily_vol by the fraction of the trading session elapsed
    to estimate expected volume at the current time.
    """
    if avg_daily_vol <= 0:
        return 0.0

    now_et = datetime.now(ET)
    minutes_since_open = max((now_et.hour - 9) * 60 + now_et.minute - 30, 1)
    session_fraction = minutes_since_open / 390.0  # 390 min = full session

    expected = avg_daily_vol * session_fraction
    if expected <= 0:
        return 0.0

    rel_vol = volume_today / expected
    log.debug(
        "rel_vol=%.2f  today=%d  expected=%d  (%d min into session)",
        rel_vol, volume_today, int(expected), minutes_since_open,
    )
    return rel_vol


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
