"""
scorer.py — Signal-based conviction scoring engine.

Each CandidateStock is scored across 7 weighted sub-factors.
All sub-scores are normalised to [0, 1] before weighting.

To retune the model: edit SignalWeights in config.py — no code changes needed.

Sub-score breakdown
-------------------
  sec_catalyst      Hard catalyst filed with SEC today (strongest signal)
  cross_source      Ticker confirmed by 2+ independent sources
  wsb_mentions      WSB mention count (retail momentum proxy)
  stocktwits_rank   Position on StockTwits trending list
  st_bullish        Fraction of StockTwits messages tagged "Bullish"
  reddit_quality    Upvote-weighted Reddit engagement
  news_sentiment    Positive news coverage
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional

from config import NUM_PICKS, SIGNAL_WEIGHTS, SignalWeights, SEC_CATALYST_QUALITY
from screener import CandidateStock

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _sigmoid(x: float, midpoint: float, steepness: float = 1.0) -> float:
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Sub-scorers (each returns [0, 1])
# ---------------------------------------------------------------------------

def _score_sec_catalyst(catalyst_type: str) -> float:
    """Use the quality rank from config — earnings=1.0, unknown=0.0."""
    return SEC_CATALYST_QUALITY.get(catalyst_type, 0.0)


def _score_cross_source(source_count: int) -> float:
    """1 source → 0.25 | 2 → 0.65 | 3 → 0.90 | 4+ → 1.0"""
    return _clamp(_sigmoid(source_count, midpoint=2.0, steepness=1.5))


def _score_wsb_mentions(mentions: int) -> float:
    """0 → 0.0 | 5 → ~0.5 | 20 → ~0.9 | 50+ → ~1.0"""
    return _clamp(_sigmoid(mentions, midpoint=10.0, steepness=0.15))


def _score_stocktwits_rank(rank: int) -> float:
    """Rank 1 → 1.0 | Rank 15 → ~0.4 | Not trending (0) → 0.0"""
    if rank == 0:
        return 0.0
    return _clamp(1.0 - (rank - 1) / 20.0)


def _score_st_bullish(bullish_pct: float) -> float:
    """50% (neutral) → 0.5 | 70% → ~0.85 | 90% → ~1.0"""
    return _clamp(_sigmoid(bullish_pct, midpoint=0.55, steepness=8.0))


def _score_reddit_quality(post_score: int, mention_count: int) -> float:
    """Combined engagement: upvotes + 2× mentions. 1000 → ~0.5; 5000+ → 1.0"""
    engagement = post_score + 2 * mention_count
    return _clamp(_sigmoid(engagement, midpoint=1000.0, steepness=0.002))


def _score_news_sentiment(sentiment: float, catalyst_type: str) -> float:
    """Boost if news has a known catalyst type; otherwise raw sentiment score."""
    if catalyst_type not in ("unknown", ""):
        return _clamp(sentiment + 0.2)
    return _clamp(sentiment)


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------

def compute_score(
    c: CandidateStock,
    weights: Optional[SignalWeights] = None,
) -> float:
    """Return a composite conviction score. Score = Σ(weight_i × sub_score_i)."""
    if weights is None:
        weights = SIGNAL_WEIGHTS

    sub_scores = {
        "sec_catalyst":   (weights.sec_catalyst,    _score_sec_catalyst(c.sec_catalyst_type)),
        "cross_source":   (weights.cross_source,    _score_cross_source(c.source_count)),
        "wsb_mentions":   (weights.wsb_mentions,    _score_wsb_mentions(c.wsb_mentions)),
        "st_rank":        (weights.stocktwits_rank, _score_stocktwits_rank(c.stocktwits_rank)),
        "st_bullish":     (weights.st_bullish,      _score_st_bullish(c.stocktwits_bullish_pct)),
        "reddit_quality": (weights.reddit_quality,  _score_reddit_quality(
                              c.reddit_post_score, c.reddit_mentions)),
        "news_sentiment": (weights.news_sentiment,  _score_news_sentiment(
                              c.news_sentiment, c.news_catalyst_type)),
    }

    total = sum(w * s for _, (w, s) in sub_scores.items())

    log.debug(
        "%s | score=%.3f | %s",
        c.ticker,
        total,
        "  ".join(f"{k}={s:.2f}(×{w:.1f})" for k, (w, s) in sub_scores.items()),
    )
    return round(total, 4)


# ---------------------------------------------------------------------------
# Ranking reason builder
# ---------------------------------------------------------------------------

def _build_reason(c: CandidateStock, rank: int) -> str:
    if rank == 1:
        if c.sec_catalyst_type in ("earnings", "fda"):
            return (
                f"Ranked #1 for a high-conviction {c.best_catalyst_label} catalyst "
                f"confirmed by {c.sources_str}."
            )
        if c.source_count >= 3:
            return (
                f"Ranked #1 for appearing across {c.source_count} independent sources "
                f"({c.sources_str}) — rare multi-signal confirmation."
            )
        if c.wsb_mentions >= 10:
            return (
                f"Ranked #1 for {c.wsb_mentions} WSB mentions driving retail momentum, "
                f"backed by {c.sources_str}."
            )
        if c.stocktwits_rank > 0:
            return (
                f"Ranked #1 as StockTwits trending #{c.stocktwits_rank} with "
                f"{int(c.stocktwits_bullish_pct * 100)}% bullish sentiment."
            )
        return f"Ranked #1 as the highest composite signal today via {c.sources_str}."
    else:
        if c.sec_catalyst_type:
            return (
                f"Ranked #2 as backup — {c.best_catalyst_label} catalyst on SEC "
                f"filing provides a hard fundamental reason for a move."
            )
        return (
            f"Ranked #2 as the strongest backup: {c.reddit_mentions} Reddit mentions "
            f"and {c.stocktwits_summary.lower()} offer diversification from Pick 1."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rank_candidates(
    candidates: List[CandidateStock],
    num_picks: int = NUM_PICKS,
    weights: Optional[SignalWeights] = None,
) -> List[CandidateStock]:
    """Score, sort, and assign ranks + reasons to the top *num_picks* candidates."""
    if not candidates:
        return []

    if weights is None:
        weights = SIGNAL_WEIGHTS

    for c in candidates:
        c.score = compute_score(c, weights)

    candidates.sort(key=lambda c: c.score, reverse=True)
    picks = candidates[:num_picks]

    for i, pick in enumerate(picks, start=1):
        pick.rank = i
        pick.rank_reason = _build_reason(pick, i)
        log.info(
            "Pick #%d: %s (score=%.3f, sources=%s, wsb=%d, st_rank=%s, catalyst=%s)",
            i, pick.ticker, pick.score, pick.sources_str,
            pick.wsb_mentions, pick.stocktwits_rank or "—",
            pick.best_catalyst_type,
        )

    return picks
