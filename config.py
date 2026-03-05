"""
config.py — All thresholds and scoring weights for the signal-based screener.

Edit this file to tune the model without touching any other code.
"""

from dataclasses import dataclass
from typing import Dict


# ---------------------------------------------------------------------------
# Screening thresholds
# ---------------------------------------------------------------------------

# Minimum number of Reddit mentions (across all tracked subreddits) to qualify.
# Set to 0 to keep all candidates regardless of Reddit signal.
MIN_REDDIT_MENTIONS: int = 0

# Minimum number of independent signal sources a ticker must appear in.
# 1 = keep everything; 2 = require at least 2 sources (Reddit + StockTwits, etc.)
MIN_SOURCES: int = 1

# How many top picks to output
NUM_PICKS: int = 2

# Maximum candidates to enrich with StockTwits + news calls
# (enrichment is the slowest step — keep ≤ 20 to avoid being slow)
MAX_CANDIDATES: int = 15

# Pause between individual StockTwits / Yahoo Finance calls (seconds)
REQUEST_DELAY: float = 0.5

# Hours back to scan SEC EDGAR 8-K filings
SEC_LOOKBACK_HOURS: int = 24

# Hours back to scan Reddit mentions.
# Shorter = fresher signals, less stale post-catalyst noise.
# 8h captures pre-market buzz and overnight catalysts without ingesting
# yesterday's already-priced-in moves.
REDDIT_LOOKBACK_HOURS: int = 8

# ---------------------------------------------------------------------------
# Catalyst type display labels
# ---------------------------------------------------------------------------

CATALYST_LABELS: Dict[str, str] = {
    "earnings":        "Earnings Beat",
    "fda":             "FDA Catalyst",
    "analyst_upgrade": "Analyst Upgrade",
    "acquisition":     "M&A / Acquisition",
    "deal":            "Strategic Deal",
    "unknown":         "News Catalyst",
}

# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------

# Recipient for daily picks email
EMAIL_TO: str = "cfiess@gmail.com"

# ---------------------------------------------------------------------------
# Scoring weights  ← tune these to change what matters most
# ---------------------------------------------------------------------------
# Each weight is a multiplier on a 0-1 normalised sub-score.
# Larger value = that factor drives the ranking more.

@dataclass
class SignalWeights:
    # SEC 8-K hard catalyst (earnings, FDA, acquisition filed today)
    sec_catalyst: float = 3.5

    # Cross-source confirmation (same ticker in Reddit + StockTwits + SEC)
    cross_source: float = 2.5

    # WSB mention count (retail FOMO proxy)
    wsb_mentions: float = 2.0

    # StockTwits trending rank (lower rank number = more trending)
    stocktwits_rank: float = 2.0

    # Fraction of StockTwits messages tagged "Bullish"
    st_bullish: float = 1.5

    # Reddit post upvotes + comment engagement
    reddit_quality: float = 1.0

    # News headline sentiment score
    news_sentiment: float = 1.0

    # Reddit keyword sentiment (0=bearish … 1=bullish).
    # Also used as a dampening multiplier on wsb_mentions to suppress
    # bearish pile-ons from inflating mention-count scores.
    reddit_sentiment: float = 1.2


# Default weights instance used by scorer.py
SIGNAL_WEIGHTS: SignalWeights = SignalWeights()

# Catalyst quality ranking for sec_catalyst sub-score
SEC_CATALYST_QUALITY: Dict[str, float] = {
    "earnings":   1.0,
    "fda":        0.95,
    "acquisition": 0.80,
    "deal":       0.60,
    "unknown":    0.20,
}

# ---------------------------------------------------------------------------
# yfinance retry settings (kept for optional price enrichment)
# ---------------------------------------------------------------------------

YFINANCE_RETRIES: int = 4
YFINANCE_BACKOFF_BASE: float = 10.0
