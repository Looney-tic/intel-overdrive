"""
INGEST-07: Hacker News Algolia adapter tests.

Tests for ingest_hn_source covering:
- New items stored correctly with status='raw'
- No-URL fallback to news.ycombinator.com/item?id={objectID}
- last_poll_ts advanced to max(created_at_i) across hits
- URL deduplication rejects known items
- Circuit breaker: consecutive_errors incremented on failure

Mocking strategy:
- Patch src.workers.ingest_hn.fetch_hn_stories with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_hn import ingest_hn_source


# ---------------------------------------------------------------------------
# Sample HN Algolia API responses
# ---------------------------------------------------------------------------

SAMPLE_HN_RESPONSE = {
    "hits": [
        {
            "objectID": "40000001",
            "title": "Claude Code Update",
            "url": "https://example.com/claude-code",
            "created_at_i": 1710000100,
            "points": 50,
            "num_comments": 10,
            "story_text": None,
        },
        {
            "objectID": "40000002",
            "title": "Ask HN: What is Claude Code?",
            "url": "https://example.com/ask-hn",
            "created_at_i": 1710000200,
            "points": 30,
            "num_comments": 5,
            "story_text": None,
        },
    ]
}

SAMPLE_HN_NO_URL = {
    "hits": [
        {
            "objectID": "40000003",
            "title": "Show HN: My Claude Code tool",
            "url": None,  # No URL — discussion post only
            "created_at_i": 1710000300,
            "points": 20,
            "num_comments": 3,
            "story_text": "Here is my cool tool built with Claude Code.",
        },
        {
            "objectID": "40000004",
            "title": "Another Story With URL",
            "url": "https://example.com/story-with-url",
            "created_at_i": 1710000400,
            "points": 15,
            "num_comments": 2,
            "story_text": None,
        },
    ]
}


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
async def test_hn_items_stored(session, source_factory, redis_client):
    """Two new HN items must be stored with status='raw'."""
    source = await source_factory(
        id="hn:test-items-stored",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        config={"query": "claude code"},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_hn.fetch_hn_stories",
        new=AsyncMock(return_value=SAMPLE_HN_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_hn_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://example.com/claude-code" in urls
    assert "https://example.com/ask-hn" in urls
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id


@pytest.mark.asyncio
async def test_hn_no_url_fallback(session, source_factory, redis_client):
    """Item with no URL must fall back to news.ycombinator.com/item?id={objectID}."""
    source = await source_factory(
        id="hn:test-no-url-fallback",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        config={"query": "claude code"},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_hn.fetch_hn_stories",
        new=AsyncMock(return_value=SAMPLE_HN_NO_URL),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_hn_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    # objectID 40000003 has no URL — must use fallback
    assert "https://news.ycombinator.com/item?id=40000003" in urls
    # objectID 40000004 has URL — must use it directly
    assert "https://example.com/story-with-url" in urls


@pytest.mark.asyncio
async def test_hn_poll_ts_updated(session, source_factory, redis_client):
    """After ingest, source.config['last_poll_ts'] must equal max(created_at_i) across hits."""
    source = await source_factory(
        id="hn:test-poll-ts",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        config={"query": "claude code", "last_poll_ts": 0},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_hn.fetch_hn_stories",
        new=AsyncMock(return_value=SAMPLE_HN_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_hn_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    # max(created_at_i) from SAMPLE_HN_RESPONSE is 1710000200
    assert refreshed.config["last_poll_ts"] == 1710000200


@pytest.mark.asyncio
async def test_hn_dedup_skips_existing(session, source_factory, redis_client):
    """Pre-existing URL must be skipped; only the new item is created."""
    source = await source_factory(
        id="hn:test-dedup",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        config={"query": "claude code"},
    )
    # Pre-insert item with URL matching first hit
    existing_url = "https://example.com/claude-code"
    existing = IntelItem(
        source_id=source.id,
        external_id="pre-existing",
        url=existing_url,
        url_hash=hashlib.sha256(existing_url.encode()).hexdigest(),
        title="Pre-existing",
        content="old content",
        primary_type="unknown",
        tags=[],
        status="raw",
        content_hash=hashlib.sha256(b"old content").hexdigest(),
    )
    session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_hn.fetch_hn_stories",
        new=AsyncMock(return_value=SAMPLE_HN_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_hn_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    # Pre-existing + 1 new (second hit is new)
    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://example.com/claude-code" in urls
    assert "https://example.com/ask-hn" in urls


@pytest.mark.asyncio
async def test_hn_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Fetch failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="hn:test-error",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        config={"query": "claude code"},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_hn.fetch_hn_stories",
        new=AsyncMock(side_effect=Exception("Network error")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Network error"):
                await ingest_hn_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_hn_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive HN source must not call fetch_hn_stories."""
    source = await source_factory(
        id="hn:test-inactive",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        is_active=False,
        config={},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=SAMPLE_HN_RESPONSE)

    with patch("src.workers.ingest_hn.fetch_hn_stories", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_hn_source(ctx, source.id)

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_hn_all_dupes_still_advances_watermark(
    session, source_factory, redis_client
):
    """When all returned hits are duplicates, last_poll_ts must still advance."""
    source = await source_factory(
        id="hn:test-all-dupes-watermark",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        config={"query": "claude code", "last_poll_ts": 1710000000},
    )
    # Pre-insert BOTH URLs from SAMPLE_HN_RESPONSE so all hits are dupes
    for url_str in ["https://example.com/claude-code", "https://example.com/ask-hn"]:
        existing = IntelItem(
            source_id=source.id,
            external_id=f"pre-{url_str}",
            url=url_str,
            url_hash=hashlib.sha256(url_str.encode()).hexdigest(),
            title="Pre-existing",
            content="old",
            primary_type="unknown",
            tags=[],
            status="raw",
            content_hash=hashlib.sha256(b"old").hexdigest(),
        )
        session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_hn.fetch_hn_stories",
        new=AsyncMock(return_value=SAMPLE_HN_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_hn_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    # Watermark must advance to max(created_at_i)=1710000200 even though
    # all hits were duplicates
    assert refreshed.config["last_poll_ts"] == 1710000200
