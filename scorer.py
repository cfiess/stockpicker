"""
scorer.py — Conviction scoring engine.

Each CandidateStock receives a composite score built from weighted sub-scores.
All sub-scores are normalised to [0, 1] before weighting so that weights are
directly comparable in magnitude.

To retune the model, edit WEIGHTS in config.py — no code changes required.

Sub-score breakdown
-------------------
  gap_pct          Larger gap → more momentum
  rel_vol          Higher relative volume → more market conviction
  catalyst_quality Ranked: earnings > acquisition > fda > upgrade > unknown
  spy_tailwind     Stronger SPY green = better macro backdrop
  low_float_bonus  Smaller float → larger price swings on volume
  gap_held         Boolean bonus: stock still holding above prev close
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional

from config import CATALYST_QUALITY_RANK, NUM_PICKS, WEIGHTS, ScoringWeights
from screener import CandidateStock

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _sigmoid(x: float, midpoint: float, steepness: float = 1.0) -> float:
    """Smooth S-curve normaliser; output always in (0, 1)."""
    return 1.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Individual sub-scorers  (each returns a value in [0, 1])
# ---------------------------------------------------------------------------

def _score_gap(gap_pct: float) -> float:
    """
    Gap of 2% → 0.0 baseline; 10% → ~0.9; 20%+ → approaches 1.0.
    Midpoint at 8% — stocks gapping more than 8% get above-median scores.
    """
    # shift so 2% (our minimum) anchors near 0
    adjusted = (gap_pct * 100) - 2.0          # 2% gap → 0, 10% gap → 8
    return _clamp(_sigmoid(adjusted, midpoint=6.0, steepness=0.25))


def _score_rel_vol(rel_vol: float) -> float:
    """
    RelVol 2× → baseline; 5× → ~0.75; 10×+ → approaches 1.0.
    """
    return _clamp(_sigmoid(rel_vol, midpoint=4.0, steepness=0.4))


def _score_catalyst(catalyst_type: str) -> float:
    """Lookup table from config.py."""
    return CATALYST_QUALITY_RANK.get(catalyst_type, 0.2)


def _score_spy(spy_pct: float) -> float:
    """
    SPY 0% → 0.0; SPY +1% → ~0.73; SPY +2%+ → approaches 1.0.
    Negative SPY already filtered out; mild green still gets a lower score.
    """
    pct = spy_pct * 100  # convert to percentage points
    return _clamp(_sigmoid(pct, midpoint=0.75, steepness=1.5))


def _score_float(float_shares: int) -> float:
    """
    Lower float = higher score.  Measured in millions of shares.
    <10M float → 1.0; 50M → ~0.5; 200M+ → approaches 0.0.
    Stocks with unknown float (0) get a neutral 0.5.
    """
    if float_shares <= 0:
        return 0.5

    float_m = float_shares / 1_000_000
    return _clamp(1.0 - _sigmoid(float_m, midpoint=50.0, steepness=0.05))


def _score_gap_held(gap_held: bool) -> float:
    """Binary: full credit if gap held, zero otherwise."""
    return 1.0 if gap_held else 0.0


# ---------------------------------------------------------------------------
# Composite scorer
# ---------------------------------------------------------------------------

def compute_score(candidate: CandidateStock, weights: ScoringWeights = WEIGHTS) -> float:
    """
    Return a composite conviction score for *candidate*.

    Score = Σ( weight_i × sub_score_i )

    The absolute value means nothing on its own — use it only for ranking.
    """
    sub_scores = {
        "gap_pct": (weights.gap_pct, _score_gap(candidate.gap_pct)),
        "rel_vol": (weights.rel_vol, _score_rel_vol(candidate.rel_vol)),
        "catalyst_quality": (weights.catalyst_quality, _score_catalyst(candidate.catalyst_type)),
        "spy_tailwind": (weights.spy_tailwind, _score_spy(candidate.spy_pct)),
        "low_float_bonus": (weights.low_float_bonus, _score_float(candidate.float_shares)),
        "gap_held": (weights.gap_held, _score_gap_held(candidate.gap_held)),
    }

    total = sum(w * s for _, (w, s) in sub_scores.items())

    log.debug(
        "%s | score=%.3f | %s",
        candidate.ticker,
        total,
        "  ".join(f"{k}={s:.2f}(×{w})" for k, (w, s) in sub_scores.items()),
    )

    return round(total, 4)


# ---------------------------------------------------------------------------
# Ranking rationale builder
# ---------------------------------------------------------------------------

_RANK1_TEMPLATES = [
    (
        "catalyst_quality",
        lambda c: c.catalyst_type in ("earnings", "fda"),
        "Ranked #1 for a high-conviction {catalyst_label} catalyst paired with "
        "{rel_vol}x relative volume — the strongest setup today.",
    ),
    (
        "rel_vol",
        lambda c: c.rel_vol >= 5.0,
        "Ranked #1 because extreme relative volume ({rel_vol}x) signals unusual "
        "institutional interest, amplifying the {gap_pct} gap.",
    ),
    (
        "gap_held",
        lambda c: c.gap_held and c.gap_pct >= 0.05,
        "Ranked #1 for holding a large {gap_pct} gap on {rel_vol}x volume — "
        "sellers are absent and momentum is clean.",
    ),
    (
        "default",
        lambda c: True,
        "Ranked #1 as the highest-scoring setup overall: {gap_pct} gap, "
        "{rel_vol}x volume, and a {catalyst_label} catalyst in a positive tape.",
    ),
]

_RANK2_TEMPLATES = [
    (
        "backup_different_catalyst",
        lambda c: True,
        "Ranked #2 as the strongest backup: {gap_pct} gap on {rel_vol}x volume "
        "with a {catalyst_label} catalyst — offers diversification if #1 stalls.",
    ),
]


def _build_reason(candidate: CandidateStock, rank: int) -> str:
    templates = _RANK1_TEMPLATES if rank == 1 else _RANK2_TEMPLATES
    for _, condition, template in templates:
        if condition(candidate):
            return template.format(
                catalyst_label=candidate.catalyst_label,
                rel_vol=candidate.rel_vol_str,
                gap_pct=candidate.gap_pct_str,
            )
    return f"Ranked #{rank} by composite conviction score."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rank_candidates(
    candidates: List[CandidateStock],
    num_picks: int = NUM_PICKS,
    weights: ScoringWeights = WEIGHTS,
) -> List[CandidateStock]:
    """
    Score every candidate, sort by score descending, and assign ranks
    and ranking reasons to the top *num_picks*.

    Returns only the top *num_picks* candidates with .score, .rank,
    and .rank_reason populated.
    """
    if not candidates:
        return []

    for candidate in candidates:
        candidate.score = compute_score(candidate, weights)

    candidates.sort(key=lambda c: c.score, reverse=True)

    picks = candidates[:num_picks]
    for i, pick in enumerate(picks, start=1):
        pick.rank = i
        pick.rank_reason = _build_reason(pick, i)
        log.info(
            "Pick #%d: %s (score=%.3f, gap=%.1f%%, relvol=%.1f×, catalyst=%s)",
            i,
            pick.ticker,
            pick.score,
            pick.gap_pct * 100,
            pick.rel_vol,
            pick.catalyst_type,
        )

    return picks
