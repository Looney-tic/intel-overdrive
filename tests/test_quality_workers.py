"""
Tests for quality_workers.score_quality.

Tests the score_quality worker end-to-end:
- GitHub-backed item gets quality score computed via GitHub API
- Non-GitHub item (URL matched LIKE but failed parse) gets default quality score
- Error during GitHub API call is handled gracefully (item skipped, retried next cycle)

Mocking strategy:
- Patch src.core.init_db.async_session_factory with the test session factory
- Patch fetch_github_signals to avoid real HTTP calls
- Use real DB for query logic and UPDATE verification
"""
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _init_db
from sqlalchemy import text

from src.models.models import IntelItem, Source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


async def _create_source(session) -> Source:
    source = Source(
        id=f"test:{uuid.uuid4().hex[:12]}",
        name="Test Source",
        type="rss",
        url="https://example.com/feed.xml",
        tier="tier1",
        config={},
    )
    session.add(source)
    await session.commit()
    return source


async def _create_intel_item(
    session,
    source_id: str,
    url: str = "https://github.com/anthropic/claude-code",
    status: str = "processed",
    quality_score_details: dict | None = None,
    content: str = "A GitHub tool for Claude.",
) -> IntelItem:
    item = IntelItem(
        source_id=source_id,
        external_id=str(uuid.uuid4()),
        url=url,
        title="Test Item",
        content=content,
        primary_type="tool",
        status=status,
    )
    session.add(item)
    await session.flush()

    if quality_score_details is not None:
        await session.execute(
            text(
                """
                UPDATE intel_items
                SET quality_score_details = CAST(:details AS json)
                WHERE id = CAST(:id AS uuid)
                """
            ),
            {"details": json.dumps(quality_score_details), "id": str(item.id)},
        )

    await session.commit()
    return item


async def _reload_quality(session, item_id: uuid.UUID) -> tuple:
    """Reload quality_score and quality_score_details for an item."""
    result = await session.execute(
        text(
            """
            SELECT quality_score, quality_score_details
            FROM intel_items
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": str(item_id)},
    )
    return result.fetchone()


# GitHub signals fixture — represents a healthy active repo
_MOCK_GITHUB_SIGNALS = {
    "stars": 1200,
    "forks": 150,
    "open_issues": 20,
    "pushed_at": (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    ),
    "archived": False,
    "has_license": True,
    "subscribers_count": 80,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_score_quality_github_item_gets_computed_score(session, redis_client):
    """GitHub-backed item gets quality score computed from real GitHub signals."""
    source = await _create_source(session)
    item = await _create_intel_item(
        session,
        source.id,
        url="https://github.com/anthropic/claude-code",
    )

    from src.workers.quality_workers import score_quality

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch(
            "src.workers.quality_workers.fetch_github_signals",
            new_callable=AsyncMock,
            return_value=_MOCK_GITHUB_SIGNALS,
        ):
            await score_quality({"redis": redis_client})

    row = await _reload_quality(session, item.id)
    assert row is not None
    quality_score, details = row[0], row[1]

    assert quality_score is not None, "quality_score must be set for GitHub item"
    assert isinstance(quality_score, float)
    assert 0.0 <= quality_score <= 1.0

    assert details is not None, "quality_score_details must be set for GitHub item"
    assert "maintenance" in details
    assert "security" in details
    assert "compatibility" in details


@pytest.mark.asyncio
async def test_score_quality_non_parseable_github_url_gets_default_score(
    session, redis_client
):
    """Item with non-parseable GitHub URL gets default quality_score of 0.5."""
    source = await _create_source(session)
    # URL contains 'github.com' so it matches the LIKE filter,
    # but parse_github_url will return None for this malformed URL
    item = await _create_intel_item(
        session,
        source.id,
        url="https://github.com/",  # no owner/repo — parse_github_url returns None
    )

    from src.workers.quality_workers import score_quality

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        # fetch_github_signals should never be called for non-parseable URLs
        mock_fetch = AsyncMock(return_value=_MOCK_GITHUB_SIGNALS)
        with patch("src.workers.quality_workers.fetch_github_signals", mock_fetch):
            await score_quality({"redis": redis_client})

    row = await _reload_quality(session, item.id)
    assert row is not None
    quality_score, details = row[0], row[1]

    assert quality_score == 0.5, "Non-parseable GitHub URL must get default score 0.5"
    assert details is not None
    assert details.get("note") == "non-parseable GitHub URL"

    # fetch_github_signals must NOT have been called
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_score_quality_github_api_error_uses_heuristic_fallback(
    session, redis_client
):
    """When GitHub API call fails (returns None), item gets a heuristic score
    instead of punitive 0.1, so it exits the scoring queue with a fair ranking."""
    source = await _create_source(session)
    # Create item with realistic content so heuristic produces a meaningful score
    item = await _create_intel_item(
        session,
        source.id,
        url="https://github.com/anthropic/claude-code",
        content="A comprehensive AI coding assistant that provides inline suggestions, "
        "code review, and refactoring support for multiple programming languages. "
        "Features include context-aware completions, project-wide understanding, "
        "and integration with popular editors and IDEs. " * 5,
    )
    # Set summary on the item so heuristic gets a bonus
    await session.execute(
        text(
            """
            UPDATE intel_items
            SET summary = :summary
            WHERE id = CAST(:id AS uuid)
        """
        ),
        {
            "id": str(item.id),
            "summary": "AI coding assistant with inline suggestions and code review",
        },
    )
    await session.commit()

    from src.workers.quality_workers import score_quality

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch(
            "src.workers.quality_workers.fetch_github_signals",
            new_callable=AsyncMock,
            return_value=None,  # Simulates API failure
        ):
            await score_quality({"redis": redis_client})

    row = await _reload_quality(session, item.id)
    assert row is not None
    quality_score, details = row[0], row[1]

    # Failed fetch now uses heuristic scoring instead of punitive 0.1
    assert quality_score is not None, "quality_score must be set after API failure"
    assert quality_score > 0.1, f"Heuristic fallback ({quality_score}) must be > 0.1"
    assert quality_score <= 1.0
    assert details is not None, "Failed GitHub fetch must set quality_score_details"
    assert details["note"] == "github_api_fetch_failed_heuristic_fallback"
    assert details["method"] == "heuristic"


@pytest.mark.asyncio
async def test_score_quality_already_scored_item_not_reprocessed(session, redis_client):
    """Items that already have quality_score_details are not fetched again."""
    source = await _create_source(session)
    # Create an item that already has quality_score_details set
    item = await _create_intel_item(
        session,
        source.id,
        url="https://github.com/anthropic/claude-code",
        quality_score_details={
            "maintenance": 0.9,
            "security": 1.0,
            "compatibility": 1.0,
        },
    )

    from src.workers.quality_workers import score_quality

    mock_fetch = AsyncMock(return_value=_MOCK_GITHUB_SIGNALS)
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.quality_workers.fetch_github_signals", mock_fetch):
            await score_quality({"redis": redis_client})

    # fetch_github_signals must NOT be called — item was filtered by quality_score_details IS NULL
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_score_quality_rate_limited_sentinel_breaks_batch(session, redis_client):
    """OPS-03: When fetch_github_signals returns rate_limited sentinel for the 2nd item,
    the batch loop breaks and only the 1st item gets scored."""
    source = await _create_source(session)
    item1 = await _create_intel_item(
        session,
        source.id,
        url="https://github.com/anthropic/claude-code",
    )
    item2 = await _create_intel_item(
        session,
        source.id,
        url="https://github.com/openai/codex",
    )

    from src.workers.quality_workers import score_quality

    # First call returns normal signals, second call returns rate_limited sentinel
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch(
            "src.workers.quality_workers.fetch_github_signals",
            new_callable=AsyncMock,
            side_effect=[_MOCK_GITHUB_SIGNALS, {"rate_limited": True}],
        ):
            await score_quality({"redis": redis_client})

    row1 = await _reload_quality(session, item1.id)
    row2 = await _reload_quality(session, item2.id)

    # First item was scored before rate limit hit
    assert row1 is not None
    quality_score1, details1 = row1[0], row1[1]
    assert quality_score1 is not None
    assert details1 is not None
    assert "maintenance" in details1

    # Second item was not scored — batch loop broke on rate_limited sentinel
    quality_score2, details2 = row2[0], row2[1]
    assert (
        details2 is None
    ), "Second item must not be scored when rate_limited sentinel received"


@pytest.mark.asyncio
async def test_score_quality_non_github_item_not_fetched(session, redis_client):
    """Items with non-GitHub URLs are not in the scoring batch at all."""
    source = await _create_source(session)
    # Non-GitHub URL — will not match LIKE '%%github.com%%'
    item = await _create_intel_item(
        session,
        source.id,
        url="https://npm.org/package/some-tool",
    )

    from src.workers.quality_workers import score_quality

    mock_fetch = AsyncMock(return_value=_MOCK_GITHUB_SIGNALS)
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.quality_workers.fetch_github_signals", mock_fetch):
            await score_quality({"redis": redis_client})

    # fetch_github_signals must not be called — non-GitHub URL excluded by SQL filter
    mock_fetch.assert_not_called()

    row = await _reload_quality(session, item.id)
    quality_score, details = row[0], row[1]
    # Non-GitHub URL not in the scoring batch — quality_score_details stays NULL
    assert details is None, "Non-GitHub item must not get quality_score_details"
    # quality_score stays at ORM default 0.0 (model has default=0.0, not nullable)
    assert quality_score == 0.0, "Non-GitHub item quality_score must remain at default"
