"""
INGEST-13: Releasebot RSS adapter tests.

Tests for ingest_releasebot_source covering:
- New items stored correctly with status='raw'
- 304 Not Modified: no items created, source success recorded
- Circuit breaker: consecutive_errors incremented on failure

Mocking strategy:
- Patch src.workers.ingest_releasebot.fetch_feed_conditional with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_releasebot import ingest_releasebot_source


# ---------------------------------------------------------------------------
# Sample Releasebot RSS feed bytes
# ---------------------------------------------------------------------------

SAMPLE_RELEASEBOT_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Releasebot AI Tool Releases</title>
  <link>https://releasebot.example.com</link>
  <description>Release notes from Anthropic, OpenAI, and GitHub</description>
  <item>
    <title>Anthropic SDK Python v0.49.0</title>
    <link>https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.49.0</link>
    <description>Added streaming support for Claude 3.5</description>
    <guid>https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.49.0</guid>
    <pubDate>Sat, 15 Mar 2026 10:00:00 GMT</pubDate>
  </item>
  <item>
    <title>OpenAI Node.js SDK v4.28.0</title>
    <link>https://github.com/openai/openai-node/releases/tag/v4.28.0</link>
    <description>Fixed streaming in edge environments</description>
    <guid>https://github.com/openai/openai-node/releases/tag/v4.28.0</guid>
  </item>
</channel>
</rss>"""


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
async def test_releasebot_items_stored(session, source_factory, redis_client):
    """Two Releasebot entries must be stored as IntelItems with status='raw'."""
    source = await source_factory(
        id="releasebot:test-items-stored",
        type="releasebot",
        url="https://releasebot.example.com/rss",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_releasebot.fetch_feed_conditional",
        new=AsyncMock(return_value=(SAMPLE_RELEASEBOT_RSS, "etag-v1", None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_releasebot_source(ctx, source.id)

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
    assert "https://github.com/openai/openai-node/releases/tag/v4.28.0" in urls
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id
        assert item.source_name == source.name
    # First item has pubDate, second does not
    items_by_url = {item.url: item for item in items}
    item_anthropic = items_by_url[
        "https://github.com/anthropics/anthropic-sdk-python/releases/tag/v0.49.0"
    ]
    item_openai = items_by_url[
        "https://github.com/openai/openai-node/releases/tag/v4.28.0"
    ]
    assert item_anthropic.published_at is not None
    assert item_openai.published_at is None


@pytest.mark.asyncio
async def test_releasebot_304_no_items(session, source_factory, redis_client):
    """304 Not Modified: no items created, etag stored."""
    source = await source_factory(
        id="releasebot:test-304",
        type="releasebot",
        url="https://releasebot.example.com/rss",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_releasebot.fetch_feed_conditional",
        new=AsyncMock(return_value=(None, "etag-v1", "Mon, 01 Jan 2024 00:00:00 GMT")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_releasebot_source(ctx, source.id)

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
async def test_releasebot_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Fetch failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="releasebot:test-error",
        type="releasebot",
        url="https://releasebot.example.com/rss",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_releasebot.fetch_feed_conditional",
        new=AsyncMock(side_effect=Exception("Network error")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Network error"):
                await ingest_releasebot_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_releasebot_inactive_source_skipped(
    session, source_factory, redis_client
):
    """Inactive Releasebot source must not call fetch_feed_conditional."""
    source = await source_factory(
        id="releasebot:test-inactive",
        type="releasebot",
        url="https://releasebot.example.com/rss",
        is_active=False,
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(SAMPLE_RELEASEBOT_RSS, None, None))

    with patch("src.workers.ingest_releasebot.fetch_feed_conditional", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_releasebot_source(ctx, source.id)

    mock_fetch.assert_not_called()
