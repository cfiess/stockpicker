"""
config.py — Centralized configuration for the stock screener.

All thresholds and scoring weights live here so they can be tuned without
touching any other module.
"""

from dataclasses import dataclass, field
from typing import Dict


# ---------------------------------------------------------------------------
# Screening thresholds
# ---------------------------------------------------------------------------

# Minimum gap-up percentage (e.g. 0.02 = 2%)
MIN_GAP_PCT: float = 0.02

# Minimum relative volume ratio by 9:45 ET (e.g. 2.0 = 200% of average)
MIN_REL_VOL: float = 2.0

# SPY must be positive by at least this % on the day (0.0 = any green)
MIN_SPY_PCT: float = 0.0

# How many top picks to include in the SMS
NUM_PICKS: int = 2

# Market open time used for relative-volume window calculations
MARKET_OPEN_HOUR: int = 9
MARKET_OPEN_MINUTE: int = 30

# Snapshot time for relative volume (9:45 ET)
RELVOL_SNAPSHOT_HOUR: int = 9
RELVOL_SNAPSHOT_MINUTE: int = 45

# Days of historical volume used to compute "average" volume at 9:45
RELVOL_LOOKBACK_DAYS: int = 20

# Seconds to sleep between per-ticker yfinance requests (avoids rate limits)
REQUEST_DELAY: float = 1.5

# Max retries on yfinance rate-limit errors, with exponential backoff
YFINANCE_RETRIES: int = 4
YFINANCE_BACKOFF_BASE: float = 3.0  # seconds; doubles each retry

# ---------------------------------------------------------------------------
# Catalyst keywords used to classify news headlines
# ---------------------------------------------------------------------------

CATALYST_KEYWORDS: Dict[str, list] = {
    "earnings": [
        "earnings", "eps", "beat", "revenue", "quarterly results",
        "q1", "q2", "q3", "q4", "fiscal", "guidance raised",
    ],
    "fda": [
        "fda", "approval", "approved", "clearance", "nda", "bla",
        "pdufa", "breakthrough therapy", "clinical trial", "phase 3",
        "phase 2", "data readout",
    ],
    "analyst_upgrade": [
        "upgrade", "upgraded", "outperform", "buy rating", "overweight",
        "price target raised", "pt raised", "initiates coverage",
        "raised to buy",
    ],
    "acquisition": [
        "acquisition", "acquired", "merger", "buyout", "takeover",
        "deal", "agreement to acquire", "strategic", "m&a",
    ],
}

# Catalyst type display labels
CATALYST_LABELS: Dict[str, str] = {
    "earnings": "Earnings Beat",
    "fda": "FDA Catalyst",
    "analyst_upgrade": "Analyst Upgrade",
    "acquisition": "M&A / Acquisition",
    "unknown": "News Catalyst",
}

# ---------------------------------------------------------------------------
# Scoring weights  ← tune these to adjust conviction ranking
# ---------------------------------------------------------------------------
# Each weight is a multiplier applied to a 0-1 normalised sub-score.
# Final score = sum(weight_i * normalised_sub_score_i).
# Increase a weight to make that factor matter more.

@dataclass
class ScoringWeights:
    # How large the gap-up is (bigger gap → higher score)
    gap_pct: float = 2.0

    # Relative volume (higher relvol → more market interest)
    rel_vol: float = 2.5

    # Catalyst quality bonus (earnings > fda > upgrade > acquisition > unknown)
    catalyst_quality: float = 2.0

    # SPY tailwind bonus (stronger SPY green = larger bonus)
    spy_tailwind: float = 0.5

    # Float-adjusted penalty — low-float stocks get a slight bonus for momentum
    # (score is reduced for very large floats)
    low_float_bonus: float = 0.5

    # Premarket sustained move bonus (gap held into open = conviction)
    gap_held: float = 1.5


# Default weights instance — imported by scorer.py
WEIGHTS: ScoringWeights = ScoringWeights()

# Catalyst quality ranking (higher = better catalyst for our strategy)
CATALYST_QUALITY_RANK: Dict[str, float] = {
    "earnings": 1.0,
    "fda": 0.9,
    "analyst_upgrade": 0.6,
    "acquisition": 0.7,
    "unknown": 0.2,
}

# ---------------------------------------------------------------------------
# Entry / stop-loss calculation parameters
# ---------------------------------------------------------------------------

# Entry zone is defined as current ask ± ENTRY_BUFFER_PCT
ENTRY_BUFFER_PCT: float = 0.005  # 0.5% band

# Stop-loss placed this many % below the intraday low at snapshot time
STOP_LOSS_BELOW_LOW_PCT: float = 0.02  # 2% below low


# ---------------------------------------------------------------------------
# Twilio / notification settings (values come from .env at runtime)
# ---------------------------------------------------------------------------

SMS_MAX_LENGTH: int = 1600  # Twilio concatenated SMS limit

# ---------------------------------------------------------------------------
# Data-source settings
# ---------------------------------------------------------------------------

# Maximum number of gapper candidates to retrieve from Finviz before scoring
MAX_CANDIDATES: int = 30

# Finviz screener URL for gap-ups (populated in data_sources.py)
FINVIZ_SCREENER_URL: str = (
    "https://finviz.com/screener.ashx"
    "?v=111&f=geo_usa,sh_price_o1,ta_gap_u2&ft=4&o=-gap"
)

# Yahoo Finance ticker for SPY
SPY_TICKER: str = "SPY"
