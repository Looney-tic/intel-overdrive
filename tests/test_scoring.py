"""
Unit tests for scoring_service (PIPE-07).

Pure functions — no database or Redis needed. All tests are synchronous.
"""
import math
from datetime import datetime, timedelta, timezone

import pytest

from src.services.scoring_service import (
    TIER_AUTHORITY,
    compute_authority_score,
    compute_freshness_score,
    compute_relevance_score,
)


# ---------------------------------------------------------------------------
# compute_authority_score
# ---------------------------------------------------------------------------


def test_authority_score_tier_ordering():
    """tier1 > tier2 > tier3; unknown tier defaults to 0.3 (same as tier3)."""
    assert compute_authority_score("tier1") == 1.0
    assert compute_authority_score("tier2") == 0.5
    assert compute_authority_score("tier3") == 0.3
    # Unknown tier falls back to 0.3
    assert compute_authority_score("tier99") == 0.3
    assert compute_authority_score("") == 0.3


def test_authority_score_matches_tier_authority_dict():
    """Scores must match the TIER_AUTHORITY constant exactly."""
    for tier, expected in TIER_AUTHORITY.items():
        assert compute_authority_score(tier) == expected


# ---------------------------------------------------------------------------
# compute_freshness_score
# ---------------------------------------------------------------------------


def test_freshness_score_today():
    """Item published today should score ≈ 1.0."""
    now = datetime.now(timezone.utc)
    score = compute_freshness_score(now)
    assert score == pytest.approx(1.0, abs=0.01)


def test_freshness_score_30_days():
    """Item published 30 days ago should score ≈ 1/e ≈ 0.368."""
    published = datetime.now(timezone.utc) - timedelta(days=30)
    score = compute_freshness_score(published)
    assert score == pytest.approx(math.exp(-1), abs=0.01)


def test_freshness_score_none_returns_neutral():
    """Unknown age (None) returns 0.5 as neutral default."""
    assert compute_freshness_score(None) == 0.5


def test_freshness_score_decay():
    """Older items score lower than newer items."""
    recent = compute_freshness_score(datetime.now(timezone.utc) - timedelta(days=5))
    older = compute_freshness_score(datetime.now(timezone.utc) - timedelta(days=60))
    assert recent > older


def test_freshness_never_negative():
    """Even very old items (365 days) score > 0 — exponential decay, never zero."""
    ancient = datetime.now(timezone.utc) - timedelta(days=365)
    score = compute_freshness_score(ancient)
    assert score > 0.0


# ---------------------------------------------------------------------------
# compute_relevance_score
# ---------------------------------------------------------------------------


def test_relevance_score_range():
    """Result always in [0, 1] for various inputs."""
    combos = [
        (1.0, "tier1", {}, datetime.now(timezone.utc)),
        (0.0, "tier3", {}, None),
        (
            0.5,
            "tier2",
            {},
            datetime.now(timezone.utc) - timedelta(days=15),
        ),
        (0.8, "tier1", {}, None),
    ]
    for content_match, tier, meta, pub in combos:
        score = compute_relevance_score(content_match, tier, meta, pub)
        assert 0.0 <= score <= 1.0, f"Out of range for {content_match}, {tier}"


def test_relevance_score_weights_content_dominates():
    """
    content_match has the highest weight (0.65).
    A high content_match should produce a higher score than low content_match
    when other factors are equal.
    """
    base_date = datetime.now(timezone.utc) - timedelta(days=5)

    # High content_match
    high_content = compute_relevance_score(1.0, "tier2", {}, base_date)
    # Low content_match
    low_content = compute_relevance_score(0.0, "tier2", {}, base_date)

    assert (
        high_content > low_content
    ), f"content_match weight should dominate: {high_content} vs {low_content}"


def test_relevance_score_all_zeros():
    """All-zero/None inputs → result >= 0 (no negative scores)."""
    score = compute_relevance_score(0.0, "tier3", {}, None)
    # tier3 authority = 0.30 * 0.15 = 0.045; freshness unknown = 0.5 * 0.20 = 0.10
    # total ≈ 0.145 — should be positive
    assert score >= 0.0


def test_relevance_score_formula_manual():
    """
    Manually verify formula: content_match×0.65 + authority×0.15 + freshness×0.20
    Using a fresh tier1 item with known content_match.
    Engagement scoring deferred — requires item-level metadata not available
    at classification time.
    """
    content_match = 0.8
    published_at = datetime.now(timezone.utc)  # today → freshness ≈ 1.0

    score = compute_relevance_score(content_match, "tier1", {}, published_at)
    # content: 0.8*0.65 = 0.52, authority: 1.0*0.15 = 0.15, freshness: ~1.0*0.20 = 0.20
    expected = 0.8 * 0.65 + 1.0 * 0.15 + math.exp(0) * 0.20
    assert score == pytest.approx(expected, abs=0.01)


def test_recalibrated_score_range():
    """
    Gate_score inputs [0.65, 1.0] for tier1 fresh items should produce a score
    range >= 0.20. The old formula compressed this to ~0.054; the new formula
    should spread it to ~0.23+.
    """
    now = datetime.now(timezone.utc)

    score_low = compute_relevance_score(0.65, "tier1", {}, now)
    score_high = compute_relevance_score(1.0, "tier1", {}, now)

    spread = score_high - score_low
    assert spread >= 0.20, (
        f"Score range too narrow: {score_high:.4f} - {score_low:.4f} = {spread:.4f}, "
        f"expected >= 0.20"
    )


def test_score_discrimination():
    """
    Verify meaningful discrimination between tiers and gate scores.
    - tier1 vs tier3 at same gate_score should produce different scores
    - gate_score=0.95 should score meaningfully higher than gate_score=0.65
    """
    now = datetime.now(timezone.utc)

    # Tier discrimination at same gate_score
    tier1_score = compute_relevance_score(0.80, "tier1", {}, now)
    tier3_score = compute_relevance_score(0.80, "tier3", {}, now)
    assert (
        tier1_score > tier3_score
    ), f"tier1 ({tier1_score:.4f}) should beat tier3 ({tier3_score:.4f})"

    # Gate score discrimination at same tier
    high_gate = compute_relevance_score(0.95, "tier1", {}, now)
    low_gate = compute_relevance_score(0.65, "tier1", {}, now)
    gate_diff = high_gate - low_gate
    # 0.30 * 0.65 = 0.195 — should be meaningful (> 0.15)
    assert gate_diff > 0.15, (
        f"Gate score discrimination too low: {high_gate:.4f} - {low_gate:.4f} = "
        f"{gate_diff:.4f}, expected > 0.15"
    )


def test_backfill_roundtrip():
    """
    Validate the backfill reverse-engineering math:
    1. Given (old_score, tier, published_at), derive content_match using old weights
    2. Apply new formula, assert new_score matches expected value
    3. Assert derived content_match is in [0.0, 1.0]

    Old formula: old_score = content_match * 0.50 + authority * 0.30 + freshness * 0.20
    Reverse: content_match = (old_score - authority * 0.30 - freshness * 0.20) / 0.50
    New formula: new_score = content_match * 0.65 + authority * 0.15 + freshness * 0.20
    """
    from src.services.scoring_service import (
        compute_authority_score,
        compute_freshness_score,
    )

    # Test triples: (old_score, tier, published_at_offset_days, description)
    now = datetime.now(timezone.utc)
    triples = [
        # (a) tier1 fresh item with old_score=0.90
        (0.90, "tier1", now, "tier1 fresh"),
        # (b) tier2 stale item with old_score=0.60
        (0.60, "tier2", now - timedelta(days=90), "tier2 stale"),
        # (c) tier3 fresh item with old_score=0.75
        (0.75, "tier3", now, "tier3 fresh"),
    ]

    OLD_CONTENT_W = 0.50
    OLD_AUTHORITY_W = 0.30
    OLD_FRESHNESS_W = 0.20
    NEW_CONTENT_W = 0.65
    NEW_AUTHORITY_W = 0.15
    NEW_FRESHNESS_W = 0.20

    for old_score, tier, published_at, desc in triples:
        authority = compute_authority_score(tier)
        freshness = compute_freshness_score(published_at)

        # Reverse-engineer content_match from old formula
        content_match = (
            old_score - authority * OLD_AUTHORITY_W - freshness * OLD_FRESHNESS_W
        ) / OLD_CONTENT_W

        assert (
            0.0 <= content_match <= 1.0
        ), f"[{desc}] derived content_match {content_match:.4f} out of [0, 1]"

        # Apply new formula
        new_score = (
            content_match * NEW_CONTENT_W
            + authority * NEW_AUTHORITY_W
            + freshness * NEW_FRESHNESS_W
        )

        # Verify against compute_relevance_score (which clamps to [0, 1])
        expected_from_fn = compute_relevance_score(
            content_match, tier, {}, published_at
        )
        assert new_score == pytest.approx(expected_from_fn, abs=0.01), (
            f"[{desc}] manual new_score {new_score:.4f} != "
            f"function result {expected_from_fn:.4f}"
        )
