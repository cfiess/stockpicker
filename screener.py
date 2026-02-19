"""
screener.py — Filter pipeline that turns a raw gapper list into
              qualified trade candidates.

Pipeline stages
---------------
1. Fetch tickers from Finviz gap-up screener
2. For each ticker: pull price data, validate gap ≥ 2%
3. Detect catalyst from news headlines
4. Compute relative volume; require ≥ 2× average
5. Confirm SPY is positive on the day
6. Return a list of CandidateStock objects ready for scoring
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from config import (
    CATALYST_LABELS,
    ENTRY_BUFFER_PCT,
    MIN_GAP_PCT,
    MIN_REL_VOL,
    MIN_SPY_PCT,
    REQUEST_DELAY,
    STOP_LOSS_BELOW_LOW_PCT,
)
from data_sources import (
    classify_catalyst,
    get_alpaca_quote,
    get_gappers,
    get_news_headlines,
    get_relative_volume,
    get_spy_status,
    get_stock_data,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CandidateStock:
    """All information needed for scoring and SMS formatting."""

    ticker: str
    gap_pct: float                  # e.g. 0.05 = 5% gap
    gap_held: bool                  # price still above prev close
    catalyst_type: str              # earnings | fda | analyst_upgrade | acquisition | unknown
    catalyst_summary: str           # 1-line headline
    rel_vol: float                  # e.g. 3.2 = 3.2× average volume
    current_price: float
    open_price: float
    prev_close: float
    high_of_day: float
    low_of_day: float
    float_shares: int               # 0 if unknown
    spy_pct: float                  # SPY % change on the day
    spy_price: float
    score: float = 0.0              # filled in by scorer.py
    rank: int = 0                   # 1 = primary, 2 = backup
    rank_reason: str = ""           # one-sentence explanation

    # Derived fields filled by screener
    entry_low: float = 0.0
    entry_high: float = 0.0
    stop_loss: float = 0.0

    def __post_init__(self) -> None:
        self._compute_levels()

    def _compute_levels(self) -> None:
        """Calculate entry zone and stop-loss from price data."""
        price = self.current_price
        self.entry_low = round(price * (1 - ENTRY_BUFFER_PCT), 2)
        self.entry_high = round(price * (1 + ENTRY_BUFFER_PCT), 2)
        self.stop_loss = round(self.low_of_day * (1 - STOP_LOSS_BELOW_LOW_PCT), 2)

    @property
    def catalyst_label(self) -> str:
        return CATALYST_LABELS.get(self.catalyst_type, "News Catalyst")

    @property
    def entry_zone_str(self) -> str:
        return f"${self.entry_low:.2f}–${self.entry_high:.2f}"

    @property
    def gap_pct_str(self) -> str:
        return f"{self.gap_pct * 100:.1f}%"

    @property
    def rel_vol_str(self) -> str:
        return f"{self.rel_vol:.1f}x"


# ---------------------------------------------------------------------------
# Main screening function
# ---------------------------------------------------------------------------

def run_screen(
    min_gap_pct: float = MIN_GAP_PCT,
    min_rel_vol: float = MIN_REL_VOL,
    min_spy_pct: float = MIN_SPY_PCT,
) -> Tuple[List[CandidateStock], float, float]:
    """
    Execute the full screening pipeline.

    Returns:
        (candidates, spy_pct, spy_price)

    *candidates* is a list of CandidateStock objects that passed all filters,
    sorted by gap size descending (scorer.py will re-rank them by conviction).
    """

    # ── Step 1: SPY gate ─────────────────────────────────────────────────────
    spy_pct, spy_price = get_spy_status()
    if spy_pct < min_spy_pct:
        log.info(
            "SPY gate FAILED: SPY is %.2f%% — skipping screen for today",
            spy_pct * 100,
        )
        return [], spy_pct, spy_price

    log.info("SPY gate PASSED: SPY +%.2f%%", spy_pct * 100)

    # ── Step 2: Fetch gapper candidates from Finviz ──────────────────────────
    raw_tickers = get_gappers(min_gap_pct=min_gap_pct)

    if not raw_tickers:
        log.warning("No gapper candidates returned from Finviz")
        return [], spy_pct, spy_price

    # ── Step 3: Per-ticker validation ────────────────────────────────────────
    candidates: List[CandidateStock] = []

    for ticker in raw_tickers:
        log.info("Evaluating %s …", ticker)
        time.sleep(REQUEST_DELAY)   # pace requests to avoid yfinance rate limits

        # 3a. Price / gap data
        stock = get_stock_data(ticker)
        if stock is None:
            log.debug("%s: skipped — no price data", ticker)
            continue

        if stock["gap_pct"] < min_gap_pct:
            log.debug(
                "%s: skipped — gap %.2f%% < threshold %.2f%%",
                ticker,
                stock["gap_pct"] * 100,
                min_gap_pct * 100,
            )
            continue

        # 3b. News catalyst
        headlines = get_news_headlines(ticker)
        catalyst_type, catalyst_summary = classify_catalyst(headlines)

        if catalyst_type == "unknown" and not headlines:
            log.debug("%s: skipped — no news catalyst found", ticker)
            continue

        # 3c. Relative volume
        rel_vol = get_relative_volume(ticker, volume_today=stock["volume_today"])
        if rel_vol < min_rel_vol:
            log.debug(
                "%s: skipped — rel_vol %.2f < threshold %.2f",
                ticker,
                rel_vol,
                min_rel_vol,
            )
            continue

        log.info(
            "%s QUALIFIED: gap=%.1f%%, relvol=%.1f×, catalyst=%s",
            ticker,
            stock["gap_pct"] * 100,
            rel_vol,
            catalyst_type,
        )

        # 3d. Refine entry price with Alpaca real-time quote if available
        current_price = stock["current_price"]
        alpaca_quote = get_alpaca_quote(ticker)
        if alpaca_quote and alpaca_quote["last"] > 0:
            current_price = alpaca_quote["last"]
            log.debug("%s: using Alpaca real-time price $%.2f", ticker, current_price)

        candidate = CandidateStock(
            ticker=ticker,
            gap_pct=stock["gap_pct"],
            gap_held=stock["gap_held"],
            catalyst_type=catalyst_type,
            catalyst_summary=catalyst_summary,
            rel_vol=rel_vol,
            current_price=current_price,
            open_price=stock["open"],
            prev_close=stock["prev_close"],
            high_of_day=stock["high"],
            low_of_day=stock["low"],
            float_shares=stock["float_shares"],
            spy_pct=spy_pct,
            spy_price=spy_price,
        )
        candidates.append(candidate)

    log.info(
        "Screening complete: %d candidates qualified out of %d evaluated",
        len(candidates),
        len(raw_tickers),
    )

    # Sort by gap size descending as pre-sort before scorer re-ranks
    candidates.sort(key=lambda c: c.gap_pct, reverse=True)
    return candidates, spy_pct, spy_price
