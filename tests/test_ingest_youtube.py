"""
INGEST-12: YouTube RSS adapter tests.

Tests for ingest_youtube_source covering:
- New items stored correctly with status='raw'
- 304 Not Modified: no items created, source success recorded
- Circuit breaker: consecutive_errors incremented on failure

Mocking strategy:
- Patch src.workers.ingest_youtube.fetch_feed_conditional with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_youtube import ingest_youtube_source


# ---------------------------------------------------------------------------
# Sample YouTube Atom feed bytes
# ---------------------------------------------------------------------------

SAMPLE_YOUTUBE_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Claude AI Channel</title>
  <entry>
    <id>yt:video:VIDEO001</id>
    <title>Building with Claude Code</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=VIDEO001"/>
    <author><name>Claude AI</name></author>
    <media:group>
      <media:description>Learn how to build with Claude Code in this tutorial.</media:description>
    </media:group>
    <summary>Learn how to build with Claude Code in this tutorial.</summary>
  </entry>
  <entry>
    <id>yt:video:VIDEO002</id>
    <title>MCP Server Tutorial</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=VIDEO002"/>
    <author><name>Claude AI</name></author>
    <media:group>
      <media:description>How to build MCP servers step by step.</media:description>
    </media:group>
    <summary>How to build MCP servers step by step.</summary>
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
async def test_youtube_items_stored(session, source_factory, redis_client):
    """Two YouTube video entries from the Atom feed must be stored with status='raw'."""
    source = await source_factory(
        id="youtube:test-items-stored",
        type="youtube",
        url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_TEST",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_youtube.fetch_feed_conditional",
        new=AsyncMock(return_value=(SAMPLE_YOUTUBE_ATOM, "etag-v1", None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_youtube_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://www.youtube.com/watch?v=VIDEO001" in urls
    assert "https://www.youtube.com/watch?v=VIDEO002" in urls
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id


@pytest.mark.asyncio
async def test_youtube_304_no_items(session, source_factory, redis_client):
    """304 Not Modified: no items created, etag stored."""
    source = await source_factory(
        id="youtube:test-304",
        type="youtube",
        url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_TEST",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_youtube.fetch_feed_conditional",
        new=AsyncMock(return_value=(None, "etag-v2", "Mon, 01 Jan 2024 00:00:00 GMT")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_youtube_source(ctx, source.id)

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
    assert refreshed.last_etag == "etag-v2"


@pytest.mark.asyncio
async def test_youtube_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Fetch failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="youtube:test-error",
        type="youtube",
        url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_TEST",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_youtube.fetch_feed_conditional",
        new=AsyncMock(side_effect=Exception("Network error")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Network error"):
                await ingest_youtube_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_youtube_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive YouTube source must not call fetch_feed_conditional."""
    source = await source_factory(
        id="youtube:test-inactive",
        type="youtube",
        url="https://www.youtube.com/feeds/videos.xml?channel_id=UC_TEST",
        is_active=False,
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(SAMPLE_YOUTUBE_ATOM, None, None))

    with patch("src.workers.ingest_youtube.fetch_feed_conditional", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_youtube_source(ctx, source.id)

    mock_fetch.assert_not_called()
