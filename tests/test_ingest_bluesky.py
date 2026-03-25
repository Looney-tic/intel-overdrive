"""
EXT-05: Bluesky adapter unit tests.

Tests for ingest_bluesky_source covering:
- is_keyword_search_source routes correctly based on URL pattern
- extract_bsky_handle extracts handle from profile URL
- extract_bsky_query extracts query from search URL
- Account feed mode stores IntelItems from author feed
- Keyword search mode stores IntelItems from search_posts
- poll_bluesky_sources returns early when BLUESKY_HANDLE is None

Mocking strategy:
- Patch src.workers.ingest_bluesky.get_or_create_bluesky_client with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_bluesky import (
    ingest_bluesky_source,
    poll_bluesky_sources,
    is_keyword_search_source,
    extract_bsky_handle,
    extract_bsky_query,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def _make_post_view(
    uri: str = "at://did:plc:abc123/app.bsky.feed.post/rkey001",
    handle: str = "testuser.bsky.social",
    text: str = "Exciting new MCP tools for Claude!",
    created_at: str = "2026-03-10T10:00:00Z",
) -> MagicMock:
    """Build a mock Bluesky PostView object."""
    post = MagicMock()
    post.uri = uri
    post.author.handle = handle
    post.record.text = text
    post.record.created_at = created_at
    return post


def _make_feed_view(post: MagicMock) -> MagicMock:
    """Wrap a post in a FeedViewPost mock."""
    feed_view = MagicMock()
    feed_view.post = post
    return feed_view


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_is_keyword_search_source():
    """is_keyword_search_source returns True for search URLs, False for profile URLs."""
    search_source = MagicMock()
    search_source.url = "https://bsky.app/search?q=generative+engine+optimization"

    profile_source = MagicMock()
    profile_source.url = "https://bsky.app/profile/lilyray.nyc"

    assert is_keyword_search_source(search_source) is True
    assert is_keyword_search_source(profile_source) is False


def test_extract_bsky_handle():
    """extract_bsky_handle returns the handle from a Bluesky profile URL."""
    source = MagicMock()
    source.url = "https://bsky.app/profile/lilyray.nyc"
    assert extract_bsky_handle(source) == "lilyray.nyc"

    source2 = MagicMock()
    source2.url = "https://bsky.app/profile/someone.bsky.social/"
    assert extract_bsky_handle(source2) == "someone.bsky.social"


def test_extract_bsky_query():
    """extract_bsky_query returns the decoded query from a Bluesky search URL."""
    source = MagicMock()
    source.url = "https://bsky.app/search?q=generative+engine+optimization"
    assert extract_bsky_query(source) == "generative engine optimization"

    source2 = MagicMock()
    source2.url = "https://bsky.app/search?q=claude+code"
    assert extract_bsky_query(source2) == "claude code"


@pytest.mark.asyncio
async def test_ingest_bluesky_source_creates_items_from_feed(
    session, source_factory, redis_client
):
    """Account feed mode: posts stored as IntelItems with bsky.app web URLs."""
    source = await source_factory(
        id="bluesky:test-feed",
        type="bluesky",
        url="https://bsky.app/profile/testuser.bsky.social",
        config={},
    )
    ctx = {"redis": redis_client}

    post1 = _make_post_view(
        uri="at://did:plc:abc123/app.bsky.feed.post/rkey001",
        handle="testuser.bsky.social",
        text="Exciting MCP tools for Claude!",
    )
    post2 = _make_post_view(
        uri="at://did:plc:abc123/app.bsky.feed.post/rkey002",
        handle="testuser.bsky.social",
        text="Another great AI update today.",
    )

    mock_response = MagicMock()
    mock_response.feed = [_make_feed_view(post1), _make_feed_view(post2)]

    mock_client = AsyncMock()
    mock_client.get_author_feed = AsyncMock(return_value=mock_response)

    with patch(
        "src.workers.ingest_bluesky.get_or_create_bluesky_client",
        new=AsyncMock(return_value=mock_client),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_bluesky_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://bsky.app/profile/testuser.bsky.social/post/rkey001" in urls
    assert "https://bsky.app/profile/testuser.bsky.social/post/rkey002" in urls
    for item in items:
        assert item.status == "raw"
        assert "bluesky" in item.tags


@pytest.mark.asyncio
async def test_ingest_bluesky_source_keyword_search(
    session, source_factory, redis_client
):
    """Keyword search mode: posts from search_posts stored as IntelItems."""
    source = await source_factory(
        id="bluesky:test-search",
        type="bluesky",
        url="https://bsky.app/search?q=mcp+tools",
        config={},
    )
    ctx = {"redis": redis_client}

    post = _make_post_view(
        uri="at://did:plc:xyz456/app.bsky.feed.post/rkey003",
        handle="anotheruser.bsky.social",
        text="MCP tools are amazing for Claude integrations!",
    )

    mock_search_response = MagicMock()
    mock_search_response.posts = [post]

    mock_client = AsyncMock()
    mock_client.app = MagicMock()
    mock_client.app.bsky = MagicMock()
    mock_client.app.bsky.feed = MagicMock()
    mock_client.app.bsky.feed.search_posts = AsyncMock(
        return_value=mock_search_response
    )

    with patch(
        "src.workers.ingest_bluesky.get_or_create_bluesky_client",
        new=AsyncMock(return_value=mock_client),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_bluesky_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 1
    assert "anotheruser.bsky.social" in items[0].url
    assert "rkey003" in items[0].url


@pytest.mark.asyncio
async def test_poll_bluesky_sources_skips_without_credentials(
    session, source_factory, redis_client
):
    """poll_bluesky_sources returns early when BLUESKY_HANDLE is None — no jobs dispatched."""
    await source_factory(
        id="bluesky:test-no-creds",
        type="bluesky",
        url="https://bsky.app/profile/testuser.bsky.social",
        config={},
    )
    ctx = {"redis": redis_client}

    mock_enqueue = AsyncMock()
    redis_client.enqueue_job = mock_enqueue

    with patch(
        "src.workers.ingest_bluesky.get_settings",
    ) as mock_settings:
        mock_settings.return_value.BLUESKY_HANDLE = None
        mock_settings.return_value.BLUESKY_APP_PASSWORD = None
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await poll_bluesky_sources(ctx)

    mock_enqueue.assert_not_called()
