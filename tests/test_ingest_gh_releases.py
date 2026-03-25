"""
INGEST-11: GitHub Releases Atom adapter tests.

Tests for ingest_gh_releases_source covering:
- New items stored correctly with status='raw'
- 304 Not Modified: no items created, source success recorded
- Circuit breaker: consecutive_errors incremented on failure

Mocking strategy:
- Patch src.workers.ingest_gh_releases.fetch_feed_conditional with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_gh_releases import ingest_gh_releases_source


# ---------------------------------------------------------------------------
# Sample GitHub Releases Atom feed bytes
# ---------------------------------------------------------------------------

SAMPLE_GH_RELEASES_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Release notes from anthropic-sdk-python</title>
  <link rel="alternate" href="https://github.com/anthropics/anthropic-sdk-python/releases"/>
  <entry>
    <id>tag:github.com,2008:Repository/12345/v0.49.0</id>
    <title>v0.49.0</title>
    <updated>2026-03-10T08:30:00Z</updated>
    <link rel="alternate" href="https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.49.0"/>
    <author><name>anthropics-bot</name></author>
    <content type="html">&lt;h2&gt;What's Changed&lt;/h2&gt;&lt;p&gt;Added streaming support&lt;/p&gt;</content>
    <summary>Added streaming support for Claude 3.5</summary>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/12345/v0.48.0</id>
    <title>v0.48.0</title>
    <link rel="alternate" href="https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.48.0"/>
    <author><name>anthropics-bot</name></author>
    <content type="html">&lt;h2&gt;Bug Fixes&lt;/h2&gt;&lt;p&gt;Fixed timeout handling&lt;/p&gt;</content>
    <summary>Fixed timeout handling in async clients</summary>
  </entry>
</feed>"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gh_releases_items_stored(session, source_factory, redis_client):
    """Two GitHub release entries must be stored as IntelItems with status='raw'."""
    source = await source_factory(
        id="gh-releases:test-items-stored",
        type="github-releases",
        url="https://github.com/anthropics/anthropic-sdk-python/releases.atom",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_gh_releases.fetch_feed_conditional",
        new=AsyncMock(return_value=(SAMPLE_GH_RELEASES_ATOM, "etag-v1", None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_gh_releases_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert (
        "https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.49.0"
        in urls
    )
    assert (
        "https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.48.0"
        in urls
    )
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id
        assert item.source_name == source.name
    # First entry has updated date, second does not
    items_by_url = {item.url: item for item in items}
    item_v049 = items_by_url[
        "https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.49.0"
    ]
    item_v048 = items_by_url[
        "https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.48.0"
    ]
    assert item_v049.published_at is not None
    assert item_v048.published_at is None


@pytest.mark.asyncio
async def test_gh_releases_304_no_items(session, source_factory, redis_client):
    """304 Not Modified: no items created, etag stored."""
    source = await source_factory(
        id="gh-releases:test-304",
        type="github-releases",
        url="https://github.com/anthropics/anthropic-sdk-python/releases.atom",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_gh_releases.fetch_feed_conditional",
        new=AsyncMock(return_value=(None, "etag-v1", "Mon, 01 Jan 2024 00:00:00 GMT")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_gh_releases_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    assert len(result.scalars().all()) == 0

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.last_etag == "etag-v1"


@pytest.mark.asyncio
async def test_gh_releases_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Fetch failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="gh-releases:test-error",
        type="github-releases",
        url="https://github.com/anthropics/anthropic-sdk-python/releases.atom",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_gh_releases.fetch_feed_conditional",
        new=AsyncMock(side_effect=Exception("Network error")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Network error"):
                await ingest_gh_releases_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_gh_releases_inactive_source_skipped(
    session, source_factory, redis_client
):
    """Inactive GH Releases source must not call fetch_feed_conditional."""
    source = await source_factory(
        id="gh-releases:test-inactive",
        type="github-releases",
        url="https://github.com/anthropics/anthropic-sdk-python/releases.atom",
        is_active=False,
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(SAMPLE_GH_RELEASES_ATOM, None, None))

    with patch("src.workers.ingest_gh_releases.fetch_feed_conditional", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_gh_releases_source(ctx, source.id)

    mock_fetch.assert_not_called()
