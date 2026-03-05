"""
screener.py — Signal-based screening pipeline.

Pipeline
--------
1. Gather Reddit mentions  (WSB + r/stocks + r/options + r/pennystocks)
2. Gather StockTwits trending symbols
3. Gather SEC EDGAR 8-K filings from the last 24 hours (Reddit: REDDIT_LOOKBACK_HOURS)
4. Merge all three into a unified candidate pool
5. Enrich each candidate: StockTwits sentiment + Yahoo Finance news
6. Return CandidateStock objects ready for scoring

No real-time price data required.  No yfinance calls in this module.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Tuple

from config import (
    CATALYST_LABELS,
    MAX_CANDIDATES,
    MIN_REDDIT_MENTIONS,
    MIN_SOURCES,
    REDDIT_LOOKBACK_HOURS,
    REQUEST_DELAY,
)
from signals import (
    SignalData,
    aggregate_signals,
    get_reddit_mentions,
    get_sec_catalysts,
    get_stocktwits_sentiment,
    get_stocktwits_trending,
    get_yahoo_news,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CandidateStock:
    """All signal data needed for scoring and output formatting."""

    ticker: str
    company_name: str = ""

    # Reddit signals
    reddit_mentions: int = 0
    wsb_mentions: int = 0
    reddit_post_score: int = 0
    reddit_subreddits: List[str] = field(default_factory=list)
    reddit_sentiment: float = 0.5

    # StockTwits signals
    stocktwits_rank: int = 0
    stocktwits_watchers: int = 0
    stocktwits_bullish_pct: float = 0.5
    stocktwits_message_count: int = 0

    # SEC catalyst
    sec_catalyst_type: str = ""     # earnings | fda | acquisition | deal | unknown
    sec_description: str = ""

    # News
    news_headline: str = ""
    news_catalyst_type: str = ""
    news_sentiment: float = 0.5

    # Meta
    sources: List[str] = field(default_factory=list)
    score: float = 0.0
    rank: int = 0
    rank_reason: str = ""

    # ── Derived helpers ────────────────────────────────────────────────────

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def best_catalyst_type(self) -> str:
        return self.sec_catalyst_type or self.news_catalyst_type or "unknown"

    @property
    def best_catalyst_label(self) -> str:
        return CATALYST_LABELS.get(self.best_catalyst_type, "News Catalyst")

    @property
    def best_description(self) -> str:
        return self.sec_description or self.news_headline or "No catalyst description available"

    @property
    def sources_str(self) -> str:
        return ", ".join(self.sources) if self.sources else "—"

    @property
    def reddit_summary(self) -> str:
        if self.reddit_mentions == 0:
            return "No Reddit mentions"
        subs = " + ".join(f"r/{s.replace('r/', '')}" for s in self.reddit_subreddits[:3])
        return f"{self.reddit_mentions} mentions ({subs})"

    @property
    def stocktwits_summary(self) -> str:
        if self.stocktwits_rank == 0:
            return "Not trending"
        bullish_pct = int(self.stocktwits_bullish_pct * 100)
        return f"Trending #{self.stocktwits_rank}, {bullish_pct}% bullish"


# ---------------------------------------------------------------------------
# Helper: build CandidateStock from SignalData
# ---------------------------------------------------------------------------

def _signal_to_candidate(sd: SignalData) -> CandidateStock:
    return CandidateStock(
        ticker=sd.ticker,
        company_name=sd.company_name,
        reddit_mentions=sd.reddit_mentions,
        wsb_mentions=sd.wsb_mentions,
        reddit_post_score=sd.reddit_post_score,
        reddit_subreddits=list(sd.reddit_subreddits),
        reddit_sentiment=sd.reddit_sentiment,
        stocktwits_rank=sd.stocktwits_rank,
        stocktwits_watchers=sd.stocktwits_watchers,
        sec_catalyst_type=sd.sec_catalyst_type,
        sec_description=sd.sec_description,
        sources=list(sd.sources),
    )


# ---------------------------------------------------------------------------
# Main screening function
# ---------------------------------------------------------------------------

def run_screen() -> List[CandidateStock]:
    """
    Run the full signal-based screening pipeline.

    Returns a list of CandidateStock objects, pre-sorted by source count
    descending.  scorer.py will re-rank by composite conviction score.
    """

    # ── Step 1: Gather signals in parallel (sequential here, fast enough) ──
    log.info("Gathering Reddit mentions…")
    reddit_signals = get_reddit_mentions(hours_back=REDDIT_LOOKBACK_HOURS)

    log.info("Gathering StockTwits trending…")
    st_signals = get_stocktwits_trending(max_symbols=30)

    log.info("Gathering SEC EDGAR 8-K filings…")
    sec_signals = get_sec_catalysts(hours_back=24)

    # ── Step 2: Merge into unified candidate pool ──────────────────────────
    merged = aggregate_signals(
        reddit=reddit_signals,
        stocktwits=st_signals,
        sec=sec_signals,
        min_sources=MIN_SOURCES,
    )

    if not merged:
        log.warning("No candidates after signal aggregation")
        return []

    # ── Step 3: Enrich with StockTwits sentiment + news (top N only) ───────
    # Sort by rough signal strength before enrichment to limit API calls
    top_candidates = sorted(
        merged.values(),
        key=lambda sd: (sd.source_count, sd.wsb_mentions + sd.stocktwits_rank * 2),
        reverse=True,
    )[:MAX_CANDIDATES]

    candidates: List[CandidateStock] = []

    for sd in top_candidates:
        c = _signal_to_candidate(sd)

        # StockTwits per-ticker sentiment (skipped if not trending)
        if sd.stocktwits_rank > 0:
            time.sleep(REQUEST_DELAY)
            bullish_pct, msg_count = get_stocktwits_sentiment(sd.ticker)
            c.stocktwits_bullish_pct = bullish_pct
            c.stocktwits_message_count = msg_count

        # Yahoo Finance news + company name (same API call)
        time.sleep(REQUEST_DELAY)
        company_name, headline, catalyst_type, sentiment = get_yahoo_news(sd.ticker)
        if company_name and not c.company_name:
            c.company_name = company_name
        c.news_headline = headline
        c.news_catalyst_type = catalyst_type
        c.news_sentiment = sentiment

        # News adds a source if it has a known catalyst
        if catalyst_type != "unknown" and "News" not in c.sources:
            c.sources.append("News")

        # Reality check: if Yahoo can't identify it as a company AND
        # it has no hard SEC catalyst AND it's not on StockTwits trending,
        # it's likely a false ticker extracted from general text (e.g. "AI")
        if (not c.company_name
                and not c.sec_catalyst_type
                and c.stocktwits_rank == 0):
            log.debug("%s: skipped — unverified ticker (no company name, no hard signal)", c.ticker)
            continue

        # Reddit mention threshold filter
        if c.reddit_mentions < MIN_REDDIT_MENTIONS and c.stocktwits_rank == 0 and not c.sec_catalyst_type:
            log.debug("%s: skipped — below all signal thresholds", c.ticker)
            continue

        log.info(
            "%s: sources=%s  wsb=%d  st_rank=%s  catalyst=%s",
            c.ticker,
            c.sources_str,
            c.wsb_mentions,
            c.stocktwits_rank or "—",
            c.best_catalyst_type,
        )
        candidates.append(c)

    log.info("Screening complete: %d qualified candidates", len(candidates))
    candidates.sort(key=lambda c: (c.source_count, c.wsb_mentions), reverse=True)
    return candidates
