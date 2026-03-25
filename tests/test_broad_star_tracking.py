"""
REPO-05: Broad star/maintenance tracking tests.

Tests for track_github_stars_broad in quality_workers.py:
- Updates quality_score_details with fresh GitHub signals
- Prioritizes stale items (no last_tracked_at first)
- Handles API failures gracefully
- Breaks after 3 consecutive failures
- Skips non-GitHub URLs

Mocking strategy:
- Patch src.services.quality_service.fetch_github_signals with AsyncMock
- Patch src.services.quality_service.compute_quality_subscores
- Patch src.services.quality_service.compute_aggregate_quality
- Patch src.core.init_db.async_session_factory with test session factory
"""

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _init_db
from sqlalchemy import select, text

from src.models.models import IntelItem, Source
from src.workers.quality_workers import track_github_stars_broad


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def make_github_item(
    session,
    source_id: str,
    url: str,
    quality_details: dict | None = None,
) -> IntelItem:
    """Create a processed IntelItem with a GitHub URL."""
    import hashlib

    url_hash = hashlib.sha256(url.encode()).hexdigest()
    content_hash = hashlib.sha256(url.encode()).hexdigest()
    item = IntelItem(
        source_id=source_id,
        external_id=url,
        url=url,
        url_hash=url_hash,
        title=url.split("/")[-1],
        content="A GitHub repo",
        primary_type="tool",
        tags=["github"],
        status="processed",
        content_hash=content_hash,
        quality_score_details=quality_details,
    )
    session.add(item)
    return item


SAMPLE_SIGNALS = {
    "stars": 500,
    "forks": 50,
    "open_issues": 10,
    "pushed_at": "2026-03-15T10:00:00Z",
    "archived": False,
    "has_license": True,
    "subscribers_count": 20,
}


SAMPLE_SUBSCORES = {
    "maintenance": 0.8,
    "security": 0.7,
    "compatibility": 0.6,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broad_tracker_updates_quality_details(
    session, source_factory, redis_client
):
    """Processed GitHub item must get quality_score_details updated with fresh signals."""
    source = await source_factory(
        id="gh:broad-track-update",
        type="github",
        url="https://api.github.com",
    )

    item = make_github_item(
        session,
        source.id,
        "https://github.com/owner/broad-track-repo",
        quality_details=None,
    )
    await session.commit()
    item_id = item.id

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.quality_workers.fetch_github_signals",
        new=AsyncMock(return_value=SAMPLE_SIGNALS),
    ):
        with patch(
            "src.workers.quality_workers.compute_quality_subscores",
            return_value=SAMPLE_SUBSCORES,
        ):
            with patch(
                "src.workers.quality_workers.compute_aggregate_quality",
                return_value=0.72,
            ):
                with patch.object(
                    _init_db, "async_session_factory", make_session_factory(session)
                ):
                    await track_github_stars_broad(ctx)

    # Verify quality_score was updated
    result = await session.execute(
        text(
            "SELECT quality_score, quality_score_details FROM intel_items WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(item_id)},
    )
    row = result.fetchone()
    assert row is not None
    assert row[0] == pytest.approx(0.72)
    details = row[1]
    assert details["stars"] == 500
    assert details["last_tracked_at"] is not None


@pytest.mark.asyncio
async def test_broad_tracker_prioritizes_stale_items(
    session, source_factory, redis_client
):
    """Items WITHOUT last_tracked_at must be processed before items WITH recent tracking."""
    source = await source_factory(
        id="gh:broad-track-priority",
        type="github",
        url="https://api.github.com",
    )

    # Item WITH recent last_tracked_at
    item_recent = make_github_item(
        session,
        source.id,
        "https://github.com/owner/recent-repo",
        quality_details={"last_tracked_at": "2026-03-20T07:00:00Z", "stars": 100},
    )
    # Item WITHOUT last_tracked_at (never tracked — should be prioritized)
    item_stale = make_github_item(
        session,
        source.id,
        "https://github.com/owner/stale-repo",
        quality_details=None,
    )
    await session.commit()
    stale_id = item_stale.id

    # Track which items get processed by capturing fetch_github_signals calls
    processed_urls = []

    async def mock_fetch(owner, repo, token):
        processed_urls.append(f"https://github.com/{owner}/{repo}")
        return SAMPLE_SIGNALS

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.quality_workers.fetch_github_signals",
        new=AsyncMock(side_effect=mock_fetch),
    ):
        with patch(
            "src.workers.quality_workers.compute_quality_subscores",
            return_value=SAMPLE_SUBSCORES,
        ):
            with patch(
                "src.workers.quality_workers.compute_aggregate_quality",
                return_value=0.7,
            ):
                with patch.object(
                    _init_db, "async_session_factory", make_session_factory(session)
                ):
                    await track_github_stars_broad(ctx)

    # Both should be processed but stale item should be first
    assert len(processed_urls) == 2
    assert processed_urls[0] == "https://github.com/owner/stale-repo"


@pytest.mark.asyncio
async def test_broad_tracker_handles_api_failures(
    session, source_factory, redis_client
):
    """Worker must complete without error when fetch_github_signals returns None."""
    source = await source_factory(
        id="gh:broad-track-fail",
        type="github",
        url="https://api.github.com",
    )

    item = make_github_item(
        session,
        source.id,
        "https://github.com/owner/fail-repo",
        quality_details={"stars": 50, "maintenance": 0.5},
    )
    await session.commit()
    item_id = item.id

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.quality_workers.fetch_github_signals",
        new=AsyncMock(return_value=None),
    ):
        with patch.object(
            _init_db, "async_session_factory", make_session_factory(session)
        ):
            # Must NOT raise
            await track_github_stars_broad(ctx)

    # quality_score_details should be unchanged (API failure = skip)
    result = await session.execute(
        text(
            "SELECT quality_score_details FROM intel_items WHERE id = CAST(:id AS uuid)"
        ),
        {"id": str(item_id)},
    )
    row = result.fetchone()
    # Details should still be the original value
    assert row[0]["stars"] == 50


@pytest.mark.asyncio
async def test_broad_tracker_stops_after_consecutive_failures(
    session, source_factory, redis_client
):
    """Worker must break the loop after 3 consecutive API failures."""
    source = await source_factory(
        id="gh:broad-track-consec-fail",
        type="github",
        url="https://api.github.com",
    )

    # Create 5 items — only 3 should be attempted before breaking
    for i in range(5):
        make_github_item(
            session,
            source.id,
            f"https://github.com/owner/consec-fail-{i}",
            quality_details=None,
        )
    await session.commit()

    call_count = 0

    async def mock_fetch(owner, repo, token):
        nonlocal call_count
        call_count += 1
        return None  # All fail

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.quality_workers.fetch_github_signals",
        new=AsyncMock(side_effect=mock_fetch),
    ):
        with patch.object(
            _init_db, "async_session_factory", make_session_factory(session)
        ):
            await track_github_stars_broad(ctx)

    # Should stop after 3 consecutive failures (not process all 5)
    assert call_count == 3


@pytest.mark.asyncio
async def test_broad_tracker_skips_non_github_urls(
    session, source_factory, redis_client
):
    """Non-GitHub URLs must not be attempted by the broad tracker."""
    source = await source_factory(
        id="gh:broad-track-non-gh",
        type="github",
        url="https://api.github.com",
    )

    # Create a non-GitHub item
    import hashlib

    url = "https://arxiv.org/abs/1234"
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    content_hash = hashlib.sha256(b"arxiv").hexdigest()
    item = IntelItem(
        source_id=source.id,
        external_id=url,
        url=url,
        url_hash=url_hash,
        title="ArXiv Paper",
        content="An arxiv paper",
        primary_type="paper",
        tags=["research"],
        status="processed",
        content_hash=content_hash,
    )
    session.add(item)
    await session.commit()

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.quality_workers.fetch_github_signals",
        new=AsyncMock(return_value=SAMPLE_SIGNALS),
    ) as mock_fetch:
        with patch.object(
            _init_db, "async_session_factory", make_session_factory(session)
        ):
            await track_github_stars_broad(ctx)

    # fetch_github_signals should NOT be called (SQL filters github.com URLs only)
    mock_fetch.assert_not_called()
