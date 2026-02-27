"""
signals.py — Free-source signal aggregation for day trading picks.

Sources (all free, no API keys required):
  1. Reddit     — mention velocity across WSB, r/stocks, r/options, r/pennystocks
  2. StockTwits — trending symbols + bullish/bearish sentiment ratio
  3. SEC EDGAR  — today's 8-K filings (earnings, FDA, acquisitions, deals)
  4. Yahoo Finance news — headline sentiment per candidate ticker

No yfinance / real-time price data required.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from xml.etree import ElementTree

import requests

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# HTTP session with browser-like headers
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
})

_REDDIT_HEADERS = {
    "User-Agent": "StockScreener/1.0 (research bot; contact: user@example.com)",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Ticker extraction helpers
# ---------------------------------------------------------------------------

# Words that look like tickers but are not — add more as needed
NON_TICKERS: set = {
    # Single letters
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    # Two-letter common words
    "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS",
    "IT", "ME", "MY", "NO", "OF", "OH", "OK", "ON", "OR", "SO", "TO",
    "UP", "US", "WE", "HE", "HI",
    # Three-letter common words & abbreviations
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN", "HER",
    "WAS", "ONE", "OUR", "OUT", "DAY", "GET", "HAS", "HIM", "HIS", "HOW",
    "ITS", "NEW", "NOW", "OLD", "SEE", "TWO", "WAY", "WHO", "DID", "PUT",
    "SAY", "SHE", "TOO", "USE", "BAD", "BIG", "FAR", "FEW", "FUN", "GOT",
    "HAD", "LET", "LOT", "LOW", "MAY", "OFF", "OWN", "RAN", "RUN", "SET",
    "SIT", "SIX", "TEN", "TOP", "TRY", "DUE",
    # Four-letter common words
    "THAT", "WITH", "FROM", "THIS", "THEY", "HAVE", "WHAT", "WHEN", "WILL",
    "BEEN", "EACH", "THAN", "MUCH", "WELL", "WERE", "THEN", "MORE", "LIKE",
    "OVER", "INTO", "YOUR", "JUST", "SOME", "ALSO", "BACK", "CAME", "COME",
    "DOES", "DOWN", "EVEN", "FEEL", "FIND", "FULL", "GIVE", "GOES", "GONE",
    "GOOD", "GROW", "HELP", "HERE", "HIGH", "HOLD", "IDEA", "KEEP", "KNEW",
    "KNOW", "LAST", "LONG", "LOOK", "LOST", "MADE", "MAKE", "MANY", "MEAN",
    "MINE", "MISS", "MOST", "MOVE", "MUST", "NEAR", "NEED", "NEXT", "NICE",
    "NONE", "ONLY", "OPEN", "OVER", "PAID", "PART", "PAST", "PICK", "PLAN",
    "PLAY", "PLUS", "POST", "RATE", "READ", "REAL", "RISE", "RISK", "SAID",
    "SAME", "SAVE", "SELL", "SEND", "SHOW", "SIDE", "SIZE", "SLOW", "SOLD",
    "SOME", "SORT", "STAY", "STOP", "SUCH", "SURE", "TAKE", "TALK", "TELL",
    "TEND", "TEST", "THEM", "THEN", "THEY", "TIME", "TOLD", "TOOK", "TURN",
    "TYPE", "USED", "USES", "VERY", "VIEW", "WAIT", "WALK", "WANT", "WEEK",
    "WELL", "WENT", "WERE", "WHAT", "WHEN", "WITH", "WORD", "WORK", "YEAR",
    "ZERO", "CASH", "DEBT", "COST", "BULL", "BEAR", "MOON", "CALL", "PUTS",
    "GAIN", "LOSS", "PUMP", "DUMP", "HUGE", "RATE", "RATE", "DONE", "SAYS",
    "FEEL", "SEND", "SENT", "SHOT", "SIGN", "SPOT", "STEP", "TERM", "THUS",
    "TRUE", "UPON", "VOTE", "WIDE", "WILD", "WISE",
    # Five-letter common words
    "ABOUT", "AFTER", "AGAIN", "BELOW", "BEING", "COULD", "DOING", "EVERY",
    "FIRST", "FOUND", "GOING", "GREAT", "LATER", "LEAST", "MIGHT", "MONEY",
    "MOVED", "NEVER", "OFTEN", "OTHER", "OWNER", "PRICE", "QUICK", "RIGHT",
    "STILL", "STOCK", "STORE", "THEIR", "THERE", "THESE", "THOSE", "THREE",
    "TODAY", "TRADE", "UNDER", "UNTIL", "USING", "WHICH", "WHILE", "WORLD",
    "WOULD", "WRONG", "YIELD",
    # Reddit / finance jargon
    "DD", "OP", "OC", "IMO", "WSB", "YOLO", "HODL", "FOMO", "FWIW",
    "NFA", "CEO", "CFO", "CTO", "COO", "USA", "GDP", "FED", "SEC",
    "IPO", "ATH", "EOD", "EOW", "EOM", "YTD", "ETF", "OTM", "ITM",
    "ATM", "VIX", "EDIT", "NOTE", "LMAO", "TLDR", "TIL", "IRA", "FOMC",
    "CPI", "PPI", "PCE", "NFP", "AMA", "NSFW", "PDF", "URL", "LOL",
    "WTF", "OMG", "EPS", "ROE", "ROI", "TTM", "NTM", "FWD", "FY",
    "YOY", "QOQ", "FCF", "DCF", "EBIT", "BULL", "BEAT", "MISS",
    # Common ETF tickers we don't want as picks
    "SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "TQQQ", "SQQQ",
    "ARKK", "ARKG", "GLD", "SLV", "TLT", "HYG", "LQD", "XLF",
    "XLE", "XLK", "XLV", "XLI", "XLP", "XLU", "UVXY", "VXX",
    # Index tickers
    "SPX", "NDX", "RUT",
}

_TICKER_PATTERN = re.compile(r'\b([A-Z]{1,5})\b')


def extract_tickers(text: str) -> List[str]:
    """Pull plausible ticker symbols out of free-form text."""
    found = _TICKER_PATTERN.findall(text.upper())
    return [t for t in found if t not in NON_TICKERS]


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class SignalData:
    """Aggregated signal data for a single ticker."""
    ticker: str
    company_name: str = ""

    # Reddit
    reddit_mentions: int = 0        # total across all scanned subreddits
    wsb_mentions: int = 0           # WSB-specific (higher-weight)
    reddit_post_score: int = 0      # sum of upvotes on posts that mention this ticker
    reddit_comment_count: int = 0   # sum of comment counts
    reddit_sentiment: float = 0.5   # 0=bearish … 1=bullish (keyword scored)
    reddit_subreddits: List[str] = field(default_factory=list)

    # StockTwits
    stocktwits_rank: int = 0        # 1 = most trending; 0 = not on list
    stocktwits_watchers: int = 0    # watchlist count (popularity proxy)
    stocktwits_bullish_pct: float = 0.5  # fraction of tagged msgs that are bullish

    # SEC EDGAR
    sec_catalyst_type: str = ""     # earnings | fda | acquisition | deal | unknown
    sec_description: str = ""       # short description of the 8-K item

    # News (Yahoo Finance)
    news_headline: str = ""
    news_catalyst_type: str = ""    # earnings | fda | analyst_upgrade | acquisition | unknown
    news_sentiment: float = 0.5     # keyword-scored

    # Meta
    sources: List[str] = field(default_factory=list)   # which sources flagged it

    def add_source(self, name: str) -> None:
        if name not in self.sources:
            self.sources.append(name)

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def best_catalyst(self) -> str:
        """Return the strongest catalyst label available."""
        return self.sec_catalyst_type or self.news_catalyst_type or ""

    @property
    def best_description(self) -> str:
        return self.sec_description or self.news_headline or "No catalyst description"


# ---------------------------------------------------------------------------
# 1. Reddit  (no API key required — uses public JSON endpoint)
# ---------------------------------------------------------------------------

REDDIT_SUBREDDITS = [
    ("wallstreetbets", 3.0),   # (subreddit, mention weight multiplier)
    ("stocks",         1.5),
    ("options",        2.0),
    ("pennystocks",    1.5),
    ("StockMarket",    1.0),
]

_REDDIT_POSITIVE = {"buy", "long", "calls", "moon", "rocket", "bull", "bullish",
                    "squeeze", "breakout", "breakout", "upside", "gains", "yolo",
                    "loaded", "upgrade", "beat", "beat", "strong", "green"}
_REDDIT_NEGATIVE = {"puts", "short", "bear", "bearish", "dump", "crash", "sell",
                    "puts", "overvalued", "avoid", "fade", "miss", "missed"}


def _score_reddit_sentiment(text: str) -> float:
    """Simple keyword scorer: returns 0–1 (0.5 = neutral)."""
    words = set(text.lower().split())
    pos = len(words & _REDDIT_POSITIVE)
    neg = len(words & _REDDIT_NEGATIVE)
    if pos + neg == 0:
        return 0.5
    return pos / (pos + neg)


def get_reddit_mentions(
    hours_back: int = 24,
    post_limit: int = 100,
) -> Dict[str, SignalData]:
    """
    Scan configured subreddits for ticker mentions in the past *hours_back* hours.

    Returns a dict mapping ticker → SignalData.
    No Reddit API key needed — uses the public .json endpoint.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    results: Dict[str, SignalData] = {}

    for subreddit, weight in REDDIT_SUBREDDITS:
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={post_limit}"
        try:
            resp = requests.get(url, headers=_REDDIT_HEADERS, timeout=10)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("Reddit r/%s fetch failed: %s", subreddit, exc)
            continue

        for child in posts:
            post = child.get("data", {})
            created = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc)
            if created < cutoff:
                continue

            title = post.get("title", "")
            body = post.get("selftext", "")
            full_text = f"{title} {body}"
            score = post.get("score", 0)
            comments = post.get("num_comments", 0)

            tickers_in_post = set(extract_tickers(full_text))
            if not tickers_in_post:
                continue

            sentiment = _score_reddit_sentiment(full_text)

            for ticker in tickers_in_post:
                if ticker not in results:
                    results[ticker] = SignalData(ticker=ticker)

                sd = results[ticker]
                sd.reddit_mentions += int(weight)
                sd.reddit_post_score += score
                sd.reddit_comment_count += comments

                if subreddit == "wallstreetbets":
                    sd.wsb_mentions += int(weight)

                if subreddit not in sd.reddit_subreddits:
                    sd.reddit_subreddits.append(subreddit)

                # Running average of sentiment
                alpha = 0.3
                sd.reddit_sentiment = alpha * sentiment + (1 - alpha) * sd.reddit_sentiment

                sd.add_source(f"r/{subreddit}")

        # Brief pause between subreddit requests
        time.sleep(0.5)

    log.info(
        "Reddit: %d unique tickers mentioned across %d subreddits",
        len(results), len(REDDIT_SUBREDDITS),
    )
    return results


# ---------------------------------------------------------------------------
# 2. StockTwits  (public API — no key required for trending endpoint)
# ---------------------------------------------------------------------------

def get_stocktwits_trending(max_symbols: int = 30) -> Dict[str, SignalData]:
    """
    Return trending symbols from StockTwits public API.

    Returns dict of ticker → SignalData with rank and watcher count.
    """
    url = "https://api.stocktwits.com/api/2/trending/symbols.json"
    results: Dict[str, SignalData] = {}

    try:
        resp = _SESSION.get(url, timeout=10)
        resp.raise_for_status()
        symbols = resp.json().get("symbols", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("StockTwits trending fetch failed: %s", exc)
        return results

    for rank, sym in enumerate(symbols[:max_symbols], start=1):
        ticker = sym.get("symbol", "").upper()
        if not ticker or not _TICKER_PATTERN.match(ticker) or ticker in NON_TICKERS:
            continue

        sd = SignalData(
            ticker=ticker,
            company_name=sym.get("title", ""),
            stocktwits_rank=rank,
            stocktwits_watchers=sym.get("watchlist_count", 0),
        )
        sd.add_source("StockTwits")
        results[ticker] = sd

    log.info("StockTwits: %d trending symbols", len(results))
    return results


def get_stocktwits_sentiment(ticker: str) -> Tuple[float, int]:
    """
    Fetch recent StockTwits messages for *ticker* and compute bullish ratio.

    Returns (bullish_pct, message_count).  Falls back to (0.5, 0) on error.
    """
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    try:
        resp = _SESSION.get(url, timeout=10)
        resp.raise_for_status()
        messages = resp.json().get("messages", [])
    except Exception as exc:  # noqa: BLE001
        log.debug("StockTwits sentiment for %s failed: %s", ticker, exc)
        return 0.5, 0

    bullish, bearish = 0, 0
    for msg in messages:
        sentiment = msg.get("entities", {}).get("sentiment")
        if sentiment:
            basic = sentiment.get("basic", "")
            if basic == "Bullish":
                bullish += 1
            elif basic == "Bearish":
                bearish += 1

    total = bullish + bearish
    if total == 0:
        return 0.5, len(messages)
    return bullish / total, len(messages)


# ---------------------------------------------------------------------------
# 3. SEC EDGAR 8-K filings  (completely free, government data)
# ---------------------------------------------------------------------------

_CIK_TICKER_MAP: Optional[Dict[str, str]] = None  # CIK str → ticker


def _load_cik_ticker_map() -> Dict[str, str]:
    """
    Download SEC's company_tickers.json and build a CIK → ticker dict.
    Cached in module-level variable after first call.
    """
    global _CIK_TICKER_MAP
    if _CIK_TICKER_MAP is not None:
        return _CIK_TICKER_MAP

    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        resp = _SESSION.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _CIK_TICKER_MAP = {
            str(v["cik_str"]): v["ticker"].upper()
            for v in data.values()
            if "cik_str" in v and "ticker" in v
        }
        log.info("SEC CIK map loaded: %d companies", len(_CIK_TICKER_MAP))
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load SEC CIK ticker map: %s", exc)
        _CIK_TICKER_MAP = {}

    return _CIK_TICKER_MAP


_SEC_CATALYST_KEYWORDS = {
    "earnings": ["results of operations", "financial results", "earnings", "quarterly",
                 "revenue", "eps", "beat", "guidance"],
    "fda": ["fda", "approval", "clearance", "clinical", "trial", "nda", "bla", "pdufa",
            "drug", "therapy", "phase"],
    "acquisition": ["acquisition", "merger", "definitive agreement", "buyout",
                    "to acquire", "to be acquired", "business combination"],
    "deal": ["agreement", "contract", "partnership", "license", "collaboration",
             "joint venture", "strategic"],
}


def _classify_sec_catalyst(text: str) -> Tuple[str, str]:
    """Return (catalyst_type, short_description) from 8-K filing summary text."""
    text_lower = text.lower()
    for ctype, keywords in _SEC_CATALYST_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            # Truncate description to first 120 chars
            return ctype, text[:120].strip()
    return "unknown", text[:120].strip()


_SEC_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "edgar": "https://www.sec.gov/Archives/edgar/data/",
}

_CIK_RE = re.compile(r'/Archives/edgar/data/(\d+)/')


def get_sec_catalysts(hours_back: int = 24) -> Dict[str, SignalData]:
    """
    Fetch the most recent 8-K filings from SEC EDGAR and map them to tickers.

    Returns dict of ticker → SignalData with catalyst information.
    """
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        "?action=getcurrent&type=8-K&dateb=&owner=include&count=40&output=atom"
    )
    results: Dict[str, SignalData] = {}
    cik_map = _load_cik_ticker_map()

    try:
        resp = _SESSION.get(url, timeout=15)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
    except Exception as exc:  # noqa: BLE001
        log.warning("SEC EDGAR 8-K feed failed: %s", exc)
        return results

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    ns = "http://www.w3.org/2005/Atom"

    for entry in root.findall(f"{{{ns}}}entry"):
        try:
            # Parse filing date
            updated_el = entry.find(f"{{{ns}}}updated")
            if updated_el is None:
                continue
            updated = datetime.fromisoformat(updated_el.text.replace("Z", "+00:00"))
            if updated < cutoff:
                continue

            # Extract CIK from the filing URL
            link_el = entry.find(f"{{{ns}}}link")
            href = link_el.get("href", "") if link_el is not None else ""
            cik_match = _CIK_RE.search(href)
            if not cik_match:
                continue
            cik = cik_match.group(1).lstrip("0")

            ticker = cik_map.get(cik)
            if not ticker or ticker in NON_TICKERS:
                continue

            # Filing description from title or summary
            title_el = entry.find(f"{{{ns}}}title")
            summary_el = entry.find(f"{{{ns}}}summary")
            description = ""
            if summary_el is not None and summary_el.text:
                description = summary_el.text.strip()
            elif title_el is not None and title_el.text:
                description = title_el.text.strip()

            catalyst_type, catalyst_desc = _classify_sec_catalyst(description)

            if ticker not in results:
                results[ticker] = SignalData(ticker=ticker)

            sd = results[ticker]
            sd.sec_catalyst_type = catalyst_type
            sd.sec_description = catalyst_desc
            sd.add_source("SEC EDGAR")

        except Exception as exc:  # noqa: BLE001
            log.debug("SEC entry parse error: %s", exc)

    log.info("SEC EDGAR: %d companies with recent 8-K filings", len(results))
    return results


# ---------------------------------------------------------------------------
# 4. Yahoo Finance news sentiment  (per-ticker, light request)
# ---------------------------------------------------------------------------

_NEWS_CATALYST_KEYWORDS = {
    "earnings": ["earnings", "eps", "beat", "revenue", "quarterly", "guidance",
                 "q1", "q2", "q3", "q4", "fiscal", "profit", "results"],
    "fda": ["fda", "approval", "approved", "clearance", "nda", "bla", "trial",
            "phase", "data readout", "pdufa"],
    "analyst_upgrade": ["upgrade", "upgraded", "outperform", "buy rating",
                        "overweight", "price target raised", "pt raised",
                        "initiates", "raised to buy"],
    "acquisition": ["acquisition", "acquired", "merger", "buyout", "takeover",
                    "deal", "agreement to acquire", "strategic"],
}

_NEWS_POSITIVE_WORDS = {
    "beat", "beats", "raised", "upgrade", "approval", "approved", "surge",
    "jump", "gain", "strong", "record", "exceed", "top", "better", "positive",
    "buy", "outperform", "bullish", "higher", "up", "growth", "profit",
}
_NEWS_NEGATIVE_WORDS = {
    "miss", "missed", "cut", "downgrade", "deny", "denied", "decline", "fall",
    "below", "weak", "disappoint", "loss", "bearish", "lower", "sell",
    "warning", "concern", "risk", "delay",
}


def _score_news_sentiment(title: str) -> float:
    words = set(title.lower().split())
    pos = len(words & _NEWS_POSITIVE_WORDS)
    neg = len(words & _NEWS_NEGATIVE_WORDS)
    if pos + neg == 0:
        return 0.5
    return pos / (pos + neg)


def _classify_news_catalyst(title: str) -> str:
    title_lower = title.lower()
    for ctype, keywords in _NEWS_CATALYST_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            return ctype
    return "unknown"


def get_yahoo_news(ticker: str, max_items: int = 5) -> Tuple[str, str, float]:
    """
    Fetch Yahoo Finance news for *ticker*.

    Returns (best_headline, catalyst_type, sentiment_score).
    Uses a direct Yahoo Finance RSS-style API — no yfinance Ticker object.
    """
    url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}&newsCount={max_items}"
    try:
        resp = _SESSION.get(url, timeout=8)
        resp.raise_for_status()
        news_items = resp.json().get("news", [])
    except Exception as exc:  # noqa: BLE001
        log.debug("%s: Yahoo news fetch error — %s", ticker, exc)
        return "", "unknown", 0.5

    best_headline = ""
    best_catalyst = "unknown"
    best_sentiment = 0.5

    for item in news_items:
        title = item.get("title", "")
        if not title:
            continue

        catalyst = _classify_news_catalyst(title)
        sentiment = _score_news_sentiment(title)

        # Prefer items with known catalyst types
        if best_catalyst == "unknown" or catalyst != "unknown":
            best_headline = title
            best_catalyst = catalyst
            best_sentiment = sentiment

    return best_headline, best_catalyst, best_sentiment


# ---------------------------------------------------------------------------
# Aggregation — merge all signal dicts into one unified dict
# ---------------------------------------------------------------------------

def aggregate_signals(
    reddit: Dict[str, SignalData],
    stocktwits: Dict[str, SignalData],
    sec: Dict[str, SignalData],
    min_sources: int = 1,
) -> Dict[str, SignalData]:
    """
    Merge signal dicts from all sources into a single dict.

    Tickers appearing in fewer than *min_sources* sources are dropped
    (set min_sources=1 to keep everything, 2 to require cross-confirmation).
    """
    all_tickers: set = set(reddit) | set(stocktwits) | set(sec)
    merged: Dict[str, SignalData] = {}

    for ticker in all_tickers:
        # Start with whichever source has the most info
        base = (
            sec.get(ticker)
            or stocktwits.get(ticker)
            or reddit.get(ticker)
            or SignalData(ticker=ticker)
        )

        # Merge Reddit data
        if ticker in reddit:
            r = reddit[ticker]
            base.reddit_mentions = r.reddit_mentions
            base.wsb_mentions = r.wsb_mentions
            base.reddit_post_score = r.reddit_post_score
            base.reddit_comment_count = r.reddit_comment_count
            base.reddit_sentiment = r.reddit_sentiment
            base.reddit_subreddits = r.reddit_subreddits
            for s in r.sources:
                base.add_source(s)

        # Merge StockTwits data
        if ticker in stocktwits:
            st = stocktwits[ticker]
            base.stocktwits_rank = st.stocktwits_rank
            base.stocktwits_watchers = st.stocktwits_watchers
            for s in st.sources:
                base.add_source(s)

        # Merge SEC data
        if ticker in sec:
            s = sec[ticker]
            base.sec_catalyst_type = s.sec_catalyst_type
            base.sec_description = s.sec_description
            for src in s.sources:
                base.add_source(src)

        if base.source_count >= min_sources:
            merged[ticker] = base

    log.info(
        "Signal aggregation: %d unique tickers (reddit=%d, stocktwits=%d, sec=%d)",
        len(merged), len(reddit), len(stocktwits), len(sec),
    )
    return merged
