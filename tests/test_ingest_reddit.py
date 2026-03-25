"""
INGEST-08: Reddit RSS adapter tests.

Tests for ingest_reddit_source covering:
- New items stored correctly with status='raw'
- 304 Not Modified: no items created, source success recorded
- Circuit breaker: consecutive_errors incremented on failure

Mocking strategy:
- Patch src.workers.ingest_reddit.fetch_feed_conditional with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_reddit import ingest_reddit_source


# ---------------------------------------------------------------------------
# Sample Reddit RSS feed bytes
# ---------------------------------------------------------------------------

SAMPLE_REDDIT_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <category term="ClaudeAI" label="r/ClaudeAI"/>
  <entry>
    <id>t3_abcdef</id>
    <title>Claude Code is amazing</title>
    <link rel="alternate" href="https://www.reddit.com/r/ClaudeAI/comments/abcdef/claude_code_is_amazing/"/>
    <summary>Just tried Claude Code and it's incredible.</summary>
    <published>2026-03-15T12:00:00Z</published>
  </entry>
  <entry>
    <id>t3_ghijkl</id>
    <title>Best practices for MCP servers</title>
    <link rel="alternate" href="https://www.reddit.com/r/ClaudeAI/comments/ghijkl/best_practices/"/>
    <summary>Here are some tips for building MCP servers.</summary>
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
async def test_reddit_items_stored(session, source_factory, redis_client):
    """Two Reddit posts from the RSS feed must be created with status='raw'."""
    source = await source_factory(
        id="reddit:test-items-stored",
        type="reddit",
        url="https://www.reddit.com/r/ClaudeAI/new/.rss",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_reddit.fetch_feed_conditional",
        new=AsyncMock(return_value=(SAMPLE_REDDIT_RSS, "etag-v1", None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_reddit_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert any("abcdef" in u for u in urls)
    assert any("ghijkl" in u for u in urls)
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id
        assert item.source_name == source.name
    # First entry has published date, second does not
    items_by_url = {item.url: item for item in items}
    item_with_date = [v for k, v in items_by_url.items() if "abcdef" in k][0]
    item_without_date = [v for k, v in items_by_url.items() if "ghijkl" in k][0]
    assert item_with_date.published_at is not None
    assert item_without_date.published_at is None


@pytest.mark.asyncio
async def test_reddit_304_no_items(session, source_factory, redis_client):
    """304 Not Modified: no items created, source success recorded."""
    source = await source_factory(
        id="reddit:test-304",
        type="reddit",
        url="https://www.reddit.com/r/ClaudeAI/new/.rss",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_reddit.fetch_feed_conditional",
        new=AsyncMock(return_value=(None, "etag-v1", "Mon, 01 Jan 2024 00:00:00 GMT")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_reddit_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0

    # Etag must be updated even on 304
    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.last_etag == "etag-v1"


@pytest.mark.asyncio
async def test_reddit_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Fetch failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="reddit:test-error",
        type="reddit",
        url="https://www.reddit.com/r/ClaudeAI/new/.rss",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_reddit.fetch_feed_conditional",
        new=AsyncMock(side_effect=Exception("Network error")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Network error"):
                await ingest_reddit_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_reddit_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive Reddit source must not call fetch_feed_conditional."""
    source = await source_factory(
        id="reddit:test-inactive",
        type="reddit",
        url="https://www.reddit.com/r/ClaudeAI/new/.rss",
        is_active=False,
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(SAMPLE_REDDIT_RSS, None, None))

    with patch("src.workers.ingest_reddit.fetch_feed_conditional", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_reddit_source(ctx, source.id)

    mock_fetch.assert_not_called()
