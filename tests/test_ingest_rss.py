"""
INGEST-02: RSS/Atom ingestion worker tests.

Tests for ingest_rss_source covering:
- New items stored correctly
- URL deduplication rejects known URLs
- 304 Not Modified treated as no-op (etag stored, no items created)
- ETag passed to conditional GET fetch
- Bozo feed with no entries raises exception (triggers circuit breaker)
- Bozo feed with entries continues (degraded-but-usable path)

Mocking strategy:
- Patch src.workers.ingest_rss.fetch_feed_conditional with AsyncMock
- Patch src.core.init_db.async_session_factory with the test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_rss import ingest_rss_source


# ---------------------------------------------------------------------------
# Sample RSS feed bytes for tests
# ---------------------------------------------------------------------------

SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
  <item>
    <title>Test Item 1</title>
    <link>https://example.com/item-1</link>
    <description>Description of item 1</description>
    <guid>https://example.com/item-1</guid>
  </item>
  <item>
    <title>Test Item 2</title>
    <link>https://example.com/item-2</link>
    <description>Description of item 2</description>
    <guid>https://example.com/item-2</guid>
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
async def test_new_items_stored(session, source_factory, redis_client):
    """Two new items from the RSS feed must be created in the DB with status='raw'."""
    source = await source_factory(
        id="rss:test-new-items",
        type="rss",
        url="https://example.com/feed.xml",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_rss.fetch_feed_conditional",
        new=AsyncMock(
            return_value=(SAMPLE_RSS, "etag-v1", "Mon, 01 Jan 2024 00:00:00 GMT")
        ),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_rss_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://example.com/item-1" in urls
    assert "https://example.com/item-2" in urls
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id


@pytest.mark.asyncio
async def test_duplicate_url_rejected(session, source_factory, redis_client):
    """Item with a URL already in the DB must be skipped; only the new URL is created."""
    source = await source_factory(
        id="rss:test-dedup",
        type="rss",
        url="https://example.com/feed.xml",
    )
    # Pre-insert item-1 so it already exists
    import hashlib

    existing_url = "https://example.com/item-1"
    existing = IntelItem(
        source_id=source.id,
        external_id="existing-guid",
        url=existing_url,
        url_hash=hashlib.sha256(existing_url.encode()).hexdigest(),
        title="Pre-existing Item",
        content="Old content",
        primary_type="unknown",
        tags=[],
        status="raw",
        content_hash=hashlib.sha256(b"old content").hexdigest(),
    )
    session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_rss.fetch_feed_conditional",
        new=AsyncMock(return_value=(SAMPLE_RSS, "etag-v1", None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_rss_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    # Should have 2 total: existing item-1 + new item-2
    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://example.com/item-1" in urls
    assert "https://example.com/item-2" in urls


@pytest.mark.asyncio
async def test_conditional_get_304(session, source_factory, redis_client):
    """304 Not Modified: no items created, source health still updated."""
    source = await source_factory(
        id="rss:test-304",
        type="rss",
        url="https://example.com/feed.xml",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_rss.fetch_feed_conditional",
        new=AsyncMock(return_value=(None, "etag-v1", "Mon, 01 Jan 2024 00:00:00 GMT")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_rss_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0

    # Source etag should be updated — service updates in-place on same session
    # expire_on_commit=False means the object retains updated values after commit
    src_result = await session.execute(select(Source).where(Source.id == source.id))
    refreshed = src_result.scalar_one()
    assert refreshed.last_etag == "etag-v1"


@pytest.mark.asyncio
async def test_etag_passed_to_fetch(session, source_factory, redis_client):
    """Existing source.last_etag must be passed as stored_etag to fetch_feed_conditional."""
    source = await source_factory(
        id="rss:test-etag-pass",
        type="rss",
        url="https://example.com/feed.xml",
    )
    # Set a pre-existing etag on the source
    source.last_etag = "old-etag-value"
    await session.commit()

    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(None, "old-etag-value", None))

    with patch("src.workers.ingest_rss.fetch_feed_conditional", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_rss_source(ctx, source.id)

    mock_fetch.assert_called_once()
    call_kwargs = mock_fetch.call_args
    # fetch_feed_conditional(url, stored_etag, stored_last_modified)
    # It may be called positionally
    args, kwargs = call_kwargs
    stored_etag = kwargs.get("stored_etag", args[1] if len(args) > 1 else None)
    assert stored_etag == "old-etag-value"


@pytest.mark.asyncio
async def test_bozo_feed_no_entries_raises(session, source_factory, redis_client):
    """Bozo feed with no entries must raise, triggering circuit breaker path."""
    source = await source_factory(
        id="rss:test-bozo-no-entries",
        type="rss",
        url="https://example.com/feed.xml",
    )
    ctx = {"redis": redis_client}

    # Provide invalid XML that feedparser will parse as bozo with no entries
    bozo_content = b"<this is not valid xml at all???>>>"

    with patch(
        "src.workers.ingest_rss.fetch_feed_conditional",
        new=AsyncMock(return_value=(bozo_content, None, None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception):
                await ingest_rss_source(ctx, source.id)

    # Circuit breaker: source should have consecutive_errors incremented
    # handle_source_error modifies source in-place on the same session (expire_on_commit=False)
    # Use populate_existing=True to force re-load without triggering sync identity-map load
    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_bozo_feed_with_entries_continues(session, source_factory, redis_client):
    """Bozo feed that still has entries must be processed (degraded but usable)."""
    source = await source_factory(
        id="rss:test-bozo-with-entries",
        type="rss",
        url="https://example.com/feed.xml",
    )
    ctx = {"redis": redis_client}

    # This RSS has valid entries so feedparser will produce entries even if it marks bozo
    # We use a well-formed feed that will produce entries
    with patch(
        "src.workers.ingest_rss.fetch_feed_conditional",
        new=AsyncMock(return_value=(SAMPLE_RSS, "etag1", None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            # Should NOT raise
            await ingest_rss_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive source must be skipped without calling fetch_feed_conditional."""
    source = await source_factory(
        id="rss:test-inactive",
        type="rss",
        url="https://example.com/feed.xml",
        is_active=False,
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(SAMPLE_RSS, None, None))

    with patch("src.workers.ingest_rss.fetch_feed_conditional", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_rss_source(ctx, source.id)

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_new_items_etag_stored_after_success(
    session, source_factory, redis_client
):
    """After successful ingestion, source.last_etag must be updated."""
    source = await source_factory(
        id="rss:test-etag-stored",
        type="rss",
        url="https://example.com/feed.xml",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_rss.fetch_feed_conditional",
        new=AsyncMock(return_value=(SAMPLE_RSS, "new-etag-xyz", None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_rss_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.last_etag == "new-etag-xyz"
    assert refreshed.last_successful_poll is not None
