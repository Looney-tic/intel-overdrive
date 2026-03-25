"""
Scoring service: computes multi-component relevance score for IntelItems.

Weighted formula (PIPE-07, recalibrated):
  relevance = content_match × 0.65
            + authority_score × 0.15
            + freshness_score × 0.20

  Recalibrated from 0.50/0.30/0.20 to 0.65/0.15/0.20 to widen effective
  score range. The old formula compressed tier1 scores into [0.877, 0.931]
  because the fixed authority floor (0.30) dominated. New weights spread
  scores across ~0.35+ range for realistic gate_score inputs.

  Engagement scoring deferred — requires item-level metadata not available
  at classification time.

All functions are pure (no database, no async) — takes primitive inputs,
returns floats. This makes them easy to unit test and compose in workers.
"""
import math
from datetime import datetime, timezone

# Tier authority weights — tier1 sources are most trusted
TIER_AUTHORITY: dict[str, float] = {
    "tier1": 1.0,
    "tier2": 0.5,
    "tier3": 0.3,
}

# Freshness half-life: after this many days, freshness score ≈ 0.37 (1/e)
FRESHNESS_HALFLIFE_DAYS: int = 30


def compute_authority_score(source_tier: str) -> float:
    """
    Return the authority weight for the given source tier.

    Unknown tiers default to 0.3 (same as tier3 — treat as low authority).
    """
    return TIER_AUTHORITY.get(source_tier, 0.3)


def compute_freshness_score(published_at: datetime | None) -> float:
    """
    Exponential decay freshness score based on item age.

    Score = exp(-age_days / FRESHNESS_HALFLIFE_DAYS)
    - Today: ~1.0
    - 30 days ago: ~0.37 (1/e)
    - Very old items: approaches 0 but never reaches it

    If published_at is None (unknown age), returns 0.5 as a neutral default.
    """
    if published_at is None:
        return 0.5

    now = datetime.now(timezone.utc)
    # Ensure published_at is timezone-aware for comparison
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)

    age_days = max(0, (now - published_at).days)
    return math.exp(-age_days / FRESHNESS_HALFLIFE_DAYS)


def compute_relevance_score(
    content_match: float,
    source_tier: str,
    metadata: dict,
    published_at: datetime | None,
) -> float:
    """
    Composite relevance score using 3 weighted components (PIPE-07).

    content_match (0.65): typically the gate score or semantic similarity
    authority    (0.15): source tier weight
    freshness    (0.20): exponential decay from published_at

    Recalibrated from 0.50/0.30/0.20 to increase content_match discrimination.

    Engagement scoring deferred — requires item-level metadata not available
    at classification time.

    Result is always in [0, 1].
    """
    authority = compute_authority_score(source_tier)
    freshness = compute_freshness_score(published_at)

    score = content_match * 0.65 + authority * 0.15 + freshness * 0.20
    # Clamp to [0, 1] — content_match might be passed as > 1.0 by mistake
    return max(0.0, min(1.0, score))
