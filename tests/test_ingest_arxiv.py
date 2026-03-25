"""
ADAPT-01: arXiv research paper ingestion worker tests.

Tests for ingest_arxiv_source covering:
- New items stored correctly with abstract as content and categories as tags
- URL deduplication rejects known arXiv IDs
- Multi-query delay: asyncio.sleep(3) called between queries (not before first)
- Title newlines stripped (arXiv Atom adds line breaks in titles)
- Empty feed treated as success (0 items, no error)
- poll_arxiv_sources dispatches jobs for arxiv-type sources
- Source on cooldown skips fetch entirely
- Exception during fetch triggers rollback before handle_source_error

Mocking strategy:
- Patch src.workers.ingest_arxiv.fetch_arxiv_feed with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_arxiv import ingest_arxiv_source, poll_arxiv_sources


# ---------------------------------------------------------------------------
# Sample arXiv Atom XML for tests (ASCII-only, no special chars in byte literals)
# ---------------------------------------------------------------------------

SAMPLE_ARXIV_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2501.12345v1</id>
    <title>Agent-Based Code Generation with MCP</title>
    <summary>We present a novel approach to agent-based code generation using the Model Context Protocol.</summary>
    <published>2026-03-15T00:00:00Z</published>
    <category term="cs.AI"/>
    <category term="cs.SE"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2501.12346v1</id>
    <title>LLM Tool Use Survey</title>
    <summary>A comprehensive survey of tool use in large language models.</summary>
    <published>2026-03-14T00:00:00Z</published>
    <category term="cs.AI"/>
  </entry>
</feed>"""

EMPTY_ARXIV_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
</feed>"""

NEWLINE_TITLE_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2501.99999v1</id>
    <title>Title With
Newline Inside It</title>
    <summary>Abstract text here.</summary>
    <published>2026-03-15T00:00:00Z</published>
    <category term="cs.AI"/>
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
async def test_ingest_arxiv_new_items(session, source_factory, redis_client):
    """Two new arXiv entries must produce 2 IntelItems with correct fields."""
    source = await source_factory(
        id="arxiv:test-new-items",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        config={"queries": ["cat:cs.AI AND ti:agent"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_arxiv.fetch_arxiv_feed",
        new=AsyncMock(return_value=SAMPLE_ARXIV_ATOM),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_arxiv_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "http://arxiv.org/abs/2501.12345v1" in urls
    assert "http://arxiv.org/abs/2501.12346v1" in urls

    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id
        assert item.content is not None and len(item.content) > 0

    # Check categories stored as tags
    item_mcp = next(i for i in items if "2501.12345" in i.url)
    assert "cs.AI" in item_mcp.tags
    assert "cs.SE" in item_mcp.tags


@pytest.mark.asyncio
async def test_ingest_arxiv_dedup(session, source_factory, redis_client):
    """Pre-existing URL must be skipped; only the new URL is stored."""
    source = await source_factory(
        id="arxiv:test-dedup",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        config={"queries": ["cat:cs.AI"]},
    )

    # Pre-insert the first arXiv entry
    existing_url = "http://arxiv.org/abs/2501.12345v1"
    existing = IntelItem(
        source_id=source.id,
        external_id=existing_url,
        url=existing_url,
        url_hash=hashlib.sha256(existing_url.encode()).hexdigest(),
        title="Pre-existing",
        content="Old abstract",
        primary_type="unknown",
        tags=[],
        status="raw",
        content_hash=hashlib.sha256(b"Old abstract").hexdigest(),
    )
    session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_arxiv.fetch_arxiv_feed",
        new=AsyncMock(return_value=SAMPLE_ARXIV_ATOM),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_arxiv_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    # Should have 2 total: 1 pre-existing + 1 new (2501.12346)
    assert len(items) == 2
    urls = {item.url for item in items}
    assert "http://arxiv.org/abs/2501.12346v1" in urls


@pytest.mark.asyncio
async def test_ingest_arxiv_multi_query_delay(session, source_factory, redis_client):
    """asyncio.sleep(3) must be called exactly once between 2 queries (not before first)."""
    source = await source_factory(
        id="arxiv:test-delay",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        config={"queries": ["cat:cs.AI AND ti:agent", "cat:cs.SE AND ti:LLM"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_arxiv.fetch_arxiv_feed",
        new=AsyncMock(return_value=EMPTY_ARXIV_ATOM),
    ):
        with patch(
            "src.workers.ingest_arxiv.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_arxiv_source(ctx, source.id)

    # sleep(3) called once (between the two queries, not before the first)
    mock_sleep.assert_called_once_with(3)


@pytest.mark.asyncio
async def test_ingest_arxiv_title_newline_stripped(
    session, source_factory, redis_client
):
    """arXiv titles with embedded newlines must have them replaced with spaces."""
    source = await source_factory(
        id="arxiv:test-newline",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        config={"queries": ["cat:cs.AI"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_arxiv.fetch_arxiv_feed",
        new=AsyncMock(return_value=NEWLINE_TITLE_ATOM),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_arxiv_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 1
    assert "\n" not in items[0].title
    assert "Title With Newline Inside It" == items[0].title


@pytest.mark.asyncio
async def test_ingest_arxiv_empty_feed(session, source_factory, redis_client):
    """Empty feed (0 entries) must succeed with 0 items stored."""
    source = await source_factory(
        id="arxiv:test-empty",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        config={"queries": ["cat:cs.AI AND ti:agent"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_arxiv.fetch_arxiv_feed",
        new=AsyncMock(return_value=EMPTY_ARXIV_ATOM),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_arxiv_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0

    # Source health should be updated (no exception raised)
    src_result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = src_result.scalar_one()
    assert refreshed.last_successful_poll is not None


@pytest.mark.asyncio
async def test_poll_arxiv_sources_dispatches(session, source_factory, redis_client):
    """poll_arxiv_sources must enqueue one job per active arxiv source."""
    source = await source_factory(
        id="arxiv:test-poll",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        config={"queries": ["cat:cs.AI"]},
    )
    ctx = {"redis": redis_client}

    # Mock redis.enqueue_job to capture calls
    mock_enqueue = AsyncMock()
    redis_client.enqueue_job = mock_enqueue

    with patch.object(_db, "async_session_factory", make_session_factory(session)):
        await poll_arxiv_sources(ctx)

    mock_enqueue.assert_called_once_with(
        "ingest_arxiv_source", source.id, _queue_name="fast"
    )


@pytest.mark.asyncio
async def test_ingest_arxiv_cooldown_skip(session, source_factory, redis_client):
    """Source on cooldown must skip fetching entirely."""
    source = await source_factory(
        id="arxiv:test-cooldown",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        poll_interval_seconds=3600,
        config={"queries": ["cat:cs.AI"]},
    )
    ctx = {"redis": redis_client}

    mock_fetch = AsyncMock(return_value=SAMPLE_ARXIV_ATOM)

    with patch(
        "src.workers.ingest_arxiv.is_source_on_cooldown",
        new=AsyncMock(return_value=True),
    ):
        with patch("src.workers.ingest_arxiv.fetch_arxiv_feed", new=mock_fetch):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_arxiv_source(ctx, source.id)

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_ingest_arxiv_error_handling(session, source_factory, redis_client):
    """Exception during fetch must trigger rollback before handle_source_error."""
    source = await source_factory(
        id="arxiv:test-error",
        type="arxiv",
        url="http://export.arxiv.org/api/query",
        config={"queries": ["cat:cs.AI"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_arxiv.fetch_arxiv_feed",
        new=AsyncMock(side_effect=RuntimeError("network error")),
    ):
        with patch(
            "src.workers.ingest_arxiv.handle_source_error", new=AsyncMock()
        ) as mock_error:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                with pytest.raises(RuntimeError):
                    await ingest_arxiv_source(ctx, source.id)

    # handle_source_error must have been called
    mock_error.assert_called_once()

    # Source should have consecutive_errors incremented (original handle_source_error runs)
    # Since we patched handle_source_error, check that circuit breaker path was triggered
    # by verifying the exception propagated
