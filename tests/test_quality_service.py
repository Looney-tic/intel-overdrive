"""
Unit tests for quality scoring service.

Requirements traced:
- QUAL-01: Quality sub-scores (maintenance, security, compatibility) from GitHub signals
- QUAL-02: Safe pattern detection (dangerous patterns, credential detection)
- QUAL-03: Staleness detection (>180 days = stale)
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from src.services.quality_service import (
    parse_github_url,
    check_safe_patterns,
    is_stale,
    compute_community_score,
    compute_quality_subscores,
    compute_aggregate_quality,
    fetch_github_signals,
    compute_title_penalty,
    compute_summary_penalty,
    compute_content_substance,
    compute_heuristic_quality,
)


# ---------------------------------------------------------------------------
# parse_github_url
# ---------------------------------------------------------------------------


def test_parse_github_url_basic():
    """parse_github_url extracts owner/repo from standard GitHub URL."""
    result = parse_github_url("https://github.com/anthropic/claude-code")
    assert result == ("anthropic", "claude-code")


def test_parse_github_url_with_path():
    """parse_github_url strips trailing path segments."""
    result = parse_github_url("https://github.com/owner/repo/tree/main")
    assert result == ("owner", "repo")


def test_parse_non_github_url():
    """parse_github_url returns None for non-GitHub URLs."""
    result = parse_github_url("https://example.com/foo")
    assert result is None


# ---------------------------------------------------------------------------
# QUAL-02: Safe pattern detection
# ---------------------------------------------------------------------------


def test_safe_patterns_clean_code():
    """QUAL-02: Clean code returns score 1.0 with empty findings."""
    code = "def hello():\n    return 'world'"
    score, findings = check_safe_patterns(code)
    assert score == 1.0
    assert findings == []


def test_safe_patterns_builtin_detected():
    """QUAL-02: Dynamic code execution builtin usage is flagged as unsafe."""
    # Testing that the quality service detects dangerous patterns
    dangerous_code = "result = " + "ev" + "al('1+1')"
    score, findings = check_safe_patterns(dangerous_code)
    assert score < 1.0
    assert len(findings) > 0


def test_safe_patterns_credential_detected():
    """QUAL-02: Credential-like pattern (ghp_ prefix) is detected."""
    # L-1 fix: regex requires 36+ chars after ghp_ to avoid false positives
    code = 'token = "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"'
    score, findings = check_safe_patterns(code)
    assert score < 1.0
    assert any("GitHub" in f for f in findings)


def test_safe_patterns_hardcoded_path():
    """QUAL-02: Hardcoded user path is detected."""
    code = 'key_path = "/Users/john/.ssh/id_rsa"'
    score, findings = check_safe_patterns(code)
    assert score < 1.0
    assert any("path" in f.lower() for f in findings)


# ---------------------------------------------------------------------------
# QUAL-03: Staleness detection
# ---------------------------------------------------------------------------


def test_not_stale_recent():
    """QUAL-03: 30-day-old push date is not stale."""
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert is_stale(recent) is False


def test_stale_old():
    """QUAL-03: 200-day-old push date is stale (>180 days)."""
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    assert is_stale(old) is True


# ---------------------------------------------------------------------------
# QUAL-01: Quality sub-scores
# ---------------------------------------------------------------------------


def test_quality_subscores_active_repo():
    """QUAL-01: Active repo with recent push gets high maintenance, not stale."""
    recent_push = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    signals = {
        "stars": 1500,
        "forks": 200,
        "open_issues": 50,
        "pushed_at": recent_push,
        "archived": False,
        "has_license": True,
        "subscribers_count": 100,
    }
    subscores = compute_quality_subscores(signals)
    assert subscores["maintenance"] > 0.9
    assert subscores["is_stale"] is False
    assert subscores["compatibility"] == 1.0  # has_license=True


def test_quality_subscores_archived_repo():
    """QUAL-01: Archived repo gets maintenance=0.0, is_stale=True."""
    old_push = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    signals = {
        "stars": 500,
        "forks": 50,
        "open_issues": 10,
        "pushed_at": old_push,
        "archived": True,
        "has_license": True,
        "subscribers_count": 30,
    }
    subscores = compute_quality_subscores(signals)
    assert subscores["maintenance"] == 0.0
    assert subscores["is_stale"] is True


# ---------------------------------------------------------------------------
# Aggregate quality score
# ---------------------------------------------------------------------------


def test_aggregate_quality():
    """Verify updated 4-weight formula with community score."""
    subscores = {
        "maintenance": 1.0,
        "security": 1.0,
        "compatibility": 1.0,
        "community": 1.0,
    }
    assert compute_aggregate_quality(subscores) == 1.0

    subscores2 = {
        "maintenance": 0.5,
        "security": 0.8,
        "compatibility": 0.6,
        "community": 0.4,
    }
    expected = round(0.5 * 0.25 + 0.4 * 0.30 + 0.8 * 0.25 + 0.6 * 0.20, 3)
    assert compute_aggregate_quality(subscores2) == expected


# ---------------------------------------------------------------------------
# QUAL-01: fetch_github_signals rate-limit sentinel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_github_signals_returns_rate_limited_on_403():
    """QUAL-01: fetch_github_signals returns {"rate_limited": True} on HTTP 403."""
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.headers = {}

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)

    mock_async_client_cm = MagicMock()
    mock_async_client_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_async_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "src.services.quality_service.httpx.AsyncClient",
        return_value=mock_async_client_cm,
    ):
        result = await fetch_github_signals("owner", "repo", github_token=None)

    assert result == {"rate_limited": True}


# ---------------------------------------------------------------------------
# QFIX-01: Community score (stars/forks/subscribers)
# ---------------------------------------------------------------------------


def test_compute_community_score_zero_stars():
    """QFIX-01: 0 stars + 0 forks = 0.0 community score."""
    score = compute_community_score({"stars": 0, "forks": 0, "subscribers_count": 0})
    assert score == 0.0


def test_compute_community_score_low_stars():
    """QFIX-01: 10 stars produces ~0.20 community score."""
    score = compute_community_score({"stars": 10, "forks": 0, "subscribers_count": 0})
    assert 0.15 <= score <= 0.30, f"10-star score {score} not in [0.15, 0.30]"


def test_compute_community_score_medium_stars():
    """QFIX-01: 1000 stars produces ~0.60 community score."""
    score = compute_community_score(
        {"stars": 1000, "forks": 50, "subscribers_count": 20}
    )
    assert 0.55 <= score <= 0.70, f"1000-star score {score} not in [0.55, 0.70]"


def test_compute_community_score_high_stars():
    """QFIX-01: 50000 stars produces >0.85 community score."""
    score = compute_community_score(
        {"stars": 50000, "forks": 5000, "subscribers_count": 1000}
    )
    assert score > 0.85, f"50000-star score {score} should be >0.85"


def test_compute_community_score_forks_bonus():
    """QFIX-01: Stars + high forks scores higher than stars alone."""
    stars_only = compute_community_score(
        {"stars": 500, "forks": 0, "subscribers_count": 0}
    )
    stars_plus_forks = compute_community_score(
        {"stars": 500, "forks": 1000, "subscribers_count": 0}
    )
    assert (
        stars_plus_forks > stars_only
    ), f"With forks ({stars_plus_forks}) should exceed without ({stars_only})"


def test_compute_aggregate_quality_with_community():
    """QFIX-01: Full subscores dict with community key uses new 4-weight formula."""
    subscores = {
        "maintenance": 0.9,
        "security": 1.0,
        "compatibility": 1.0,
        "community": 0.7,
    }
    expected = round(0.9 * 0.25 + 0.7 * 0.30 + 1.0 * 0.25 + 1.0 * 0.20, 3)
    assert compute_aggregate_quality(subscores) == expected


def test_compute_aggregate_quality_backward_compat():
    """QFIX-01: Subscores dict WITHOUT community key defaults community to 0.5."""
    subscores = {
        "maintenance": 1.0,
        "security": 1.0,
        "compatibility": 1.0,
    }
    # community defaults to 0.5
    expected = round(1.0 * 0.25 + 0.5 * 0.30 + 1.0 * 0.25 + 1.0 * 0.20, 3)
    assert compute_aggregate_quality(subscores) == expected


def test_quality_differentiation_stars():
    """QFIX-01: 0-star repo scores lower than 2400-star repo end-to-end."""
    signals_0 = {
        "stars": 0,
        "forks": 0,
        "pushed_at": datetime.now(timezone.utc).isoformat(),
        "archived": False,
        "has_license": True,
        "open_issues": 0,
        "subscribers_count": 0,
    }
    signals_2400 = {
        "stars": 2400,
        "forks": 100,
        "pushed_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        "archived": False,
        "has_license": True,
        "open_issues": 10,
        "subscribers_count": 50,
    }
    sub0 = compute_quality_subscores(signals_0)
    sub2400 = compute_quality_subscores(signals_2400)
    q0 = compute_aggregate_quality(sub0)
    q2400 = compute_aggregate_quality(sub2400)
    assert (
        q2400 > q0 + 0.1
    ), f"2400-star ({q2400}) should be >0.1 higher than 0-star ({q0})"


# ---------------------------------------------------------------------------
# QSCORE-01: Title penalty
# ---------------------------------------------------------------------------


def test_compute_title_penalty_untitled():
    """QSCORE-01: 'Untitled' title returns -0.25 penalty."""
    assert compute_title_penalty("Untitled") == -0.25


def test_compute_title_penalty_empty():
    """QSCORE-01: Empty title returns -0.25 penalty."""
    assert compute_title_penalty("") == -0.25


def test_compute_title_penalty_bare_owner_repo():
    """QSCORE-01: Bare owner/repo title returns -0.25 penalty."""
    assert compute_title_penalty("owner/repo-name") == -0.25


def test_compute_title_penalty_test_repo():
    """QSCORE-01: Test repo title returns -0.15 penalty."""
    assert compute_title_penalty("my-first test repo") == -0.15


def test_compute_title_penalty_hello_world():
    """QSCORE-01: Hello-world title returns -0.15 penalty."""
    assert compute_title_penalty("hello-world demo") == -0.15


def test_compute_title_penalty_normal():
    """QSCORE-01: Normal descriptive title returns 0.0 (no penalty)."""
    assert compute_title_penalty("Claude Code MCP Server for VS Code") == 0.0


# ---------------------------------------------------------------------------
# QSCORE-01: Summary penalty
# ---------------------------------------------------------------------------


def test_compute_summary_penalty_none():
    """QSCORE-01: None summary returns -0.15 penalty."""
    assert compute_summary_penalty(None) == -0.15


def test_compute_summary_penalty_short():
    """QSCORE-01: Very short summary returns -0.15 penalty."""
    assert compute_summary_penalty("Too short") == -0.15


def test_compute_summary_penalty_bad_pattern():
    """QSCORE-01: Summary with bad pattern returns -0.15 penalty."""
    assert compute_summary_penalty("Cannot determine the purpose of this repo") == -0.15


def test_compute_summary_penalty_placeholder():
    """QSCORE-01: Summary with placeholder keyword returns -0.15 penalty."""
    assert (
        compute_summary_penalty("This is a placeholder description for the project")
        == -0.15
    )


def test_compute_summary_penalty_good():
    """QSCORE-01: Good descriptive summary returns 0.0 (no penalty)."""
    assert (
        compute_summary_penalty(
            "A comprehensive MCP server for Claude Code integration"
        )
        == 0.0
    )


# ---------------------------------------------------------------------------
# QSCORE-01: Content substance
# ---------------------------------------------------------------------------


def test_compute_content_substance_none():
    """QSCORE-01: None content returns -0.20."""
    assert compute_content_substance(None) == -0.20


def test_compute_content_substance_very_short():
    """QSCORE-01: Content <100 chars returns -0.15."""
    assert compute_content_substance("short") == -0.15


def test_compute_content_substance_medium_short():
    """QSCORE-01: Content 100-499 chars returns 0.05."""
    assert compute_content_substance("x" * 200) == 0.05


def test_compute_content_substance_medium():
    """QSCORE-01: Content 500-1999 chars returns 0.15."""
    assert compute_content_substance("x" * 800) == 0.15


def test_compute_content_substance_long():
    """QSCORE-01: Content 2000+ chars returns 0.20."""
    assert compute_content_substance("x" * 3000) == 0.20


# ---------------------------------------------------------------------------
# QSCORE-02: Heuristic quality composite
# ---------------------------------------------------------------------------


def test_compute_heuristic_quality_tier1_good():
    """QSCORE-02: Tier1 + long content + good summary + tags + good title scores 0.80-0.90."""
    score, details = compute_heuristic_quality(
        "tier1",
        "x" * 2000,
        "A thorough summary of the project",
        '["ai","mcp"]',
        "Real Project",
    )
    assert 0.80 <= score <= 0.90, f"tier1 good: {score}"
    assert details["method"] == "heuristic"
    assert details["source_tier"] == "tier1"


def test_compute_heuristic_quality_tier1_untitled():
    """QSCORE-02: Tier1 + good content but 'Untitled' scores below 0.65."""
    score, details = compute_heuristic_quality(
        "tier1", "x" * 2000, "A good summary of content", '["ai"]', "Untitled"
    )
    assert score <= 0.65, f"untitled: {score}"
    assert details["title_penalty"] == -0.25


def test_compute_heuristic_quality_tier3_minimal():
    """QSCORE-02: Tier3 + no content + no summary scores below 0.30."""
    score, details = compute_heuristic_quality("tier3", None, None, None)
    assert score < 0.30, f"tier3 minimal: {score}"


def test_compute_heuristic_quality_floor():
    """QSCORE-02: Score never drops below 0.05 even with all penalties."""
    score, details = compute_heuristic_quality("tier3", None, None, None, "Untitled")
    assert score >= 0.05, f"floor violated: {score}"


def test_compute_heuristic_quality_unknown_tier():
    """QSCORE-02: Unknown tier defaults to 0.15 base."""
    score, details = compute_heuristic_quality(
        None, "x" * 500, "A reasonable summary text", '["tag"]'
    )
    assert details["base_score"] == 0.15
    assert details["source_tier"] == "unknown"
