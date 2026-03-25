"""
INGEST-09: MCP Registry adapter tests.

Tests for ingest_mcp_registry_source covering:
- New items stored correctly with status='raw'
- Cursor pagination: multiple pages fetched, stops at nextCursor=null
- Server with no URL (no websiteUrl, no repository.url) is skipped
- URL deduplication rejects known servers
- Circuit breaker: consecutive_errors incremented on failure

Mocking strategy:
- Patch src.workers.ingest_mcp_registry.fetch_mcp_registry_page with AsyncMock
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
from src.workers.ingest_mcp_registry import ingest_mcp_registry_source


# ---------------------------------------------------------------------------
# Sample MCP Registry API responses
# ---------------------------------------------------------------------------

SAMPLE_MCP_PAGE_1 = {
    "servers": [
        {
            "server": {
                "name": "filesystem-server",
                "description": "MCP server for filesystem access",
                "websiteUrl": "https://example.com/filesystem-server",
                "repository": {"url": "https://github.com/example/filesystem-server"},
            }
        },
        {
            "server": {
                "name": "web-search-server",
                "description": "MCP server for web search",
                "websiteUrl": "https://example.com/web-search",
                "repository": None,
            }
        },
    ],
    "metadata": {"nextCursor": "cursor_abc123"},
}

SAMPLE_MCP_PAGE_2 = {
    "servers": [
        {
            "server": {
                "name": "code-server",
                "description": "MCP server for code execution",
                "websiteUrl": "https://example.com/code-server",
                "repository": {"url": "https://github.com/example/code-server"},
            }
        },
    ],
    "metadata": {"nextCursor": None},  # Last page
}

SAMPLE_MCP_SINGLE_PAGE = {
    "servers": [
        {
            "server": {
                "name": "test-server",
                "description": "A test MCP server",
                "websiteUrl": "https://example.com/test-server",
                "repository": None,
            }
        }
    ],
    "metadata": {"nextCursor": None},
}

SAMPLE_MCP_NO_URL = {
    "servers": [
        {
            "server": {
                "name": "no-url-server",
                "description": "Server with no URL",
                "websiteUrl": None,
                "repository": None,
            }
        },
        {
            "server": {
                "name": "valid-server",
                "description": "Server with URL",
                "websiteUrl": "https://example.com/valid",
                "repository": None,
            }
        },
    ],
    "metadata": {"nextCursor": None},
}

SAMPLE_MCP_REPO_FALLBACK = {
    "servers": [
        {
            "server": {
                "name": "repo-only-server",
                "description": "Server with only repository URL",
                "websiteUrl": None,
                "repository": {"url": "https://github.com/example/repo-only"},
            }
        }
    ],
    "metadata": {"nextCursor": None},
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
async def test_mcp_registry_items_stored(session, source_factory, redis_client):
    """Items from MCP Registry must be stored as IntelItems with status='raw'."""
    source = await source_factory(
        id="mcp:test-items-stored",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page",
        new=AsyncMock(return_value=SAMPLE_MCP_SINGLE_PAGE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 1
    assert items[0].url == "https://example.com/test-server"
    assert items[0].status == "raw"
    assert items[0].source_id == source.id


@pytest.mark.asyncio
async def test_mcp_registry_item_sets_source_name(
    session, source_factory, redis_client
):
    """INGEST-09: MCP registry adapter must set source_name on created IntelItem."""
    source = await source_factory(
        id="mcp:test-source-name",
        name="MCP Registry",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page",
        new=AsyncMock(return_value=SAMPLE_MCP_SINGLE_PAGE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 1
    assert items[0].source_name == source.name


@pytest.mark.asyncio
async def test_mcp_cursor_pagination_fetches_all_pages(
    session, source_factory, redis_client
):
    """Cursor pagination must fetch all pages until nextCursor is null.

    3 items total: 2 on page 1, 1 on page 2.
    fetch_mcp_registry_page must be called exactly twice.
    """
    source = await source_factory(
        id="mcp:test-pagination",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    ctx = {"redis": redis_client}

    mock_fetch = AsyncMock(side_effect=[SAMPLE_MCP_PAGE_1, SAMPLE_MCP_PAGE_2])

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page", new=mock_fetch
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    # Fetched exactly 2 pages
    assert mock_fetch.call_count == 2

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # 2 from page 1 + 1 from page 2 = 3 total
    assert len(items) == 3


@pytest.mark.asyncio
async def test_mcp_cursor_stops_at_null(session, source_factory, redis_client):
    """Pagination must stop when nextCursor is null, not fetching a third page."""
    source = await source_factory(
        id="mcp:test-cursor-stops",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    ctx = {"redis": redis_client}

    # Two pages: page 1 has cursor, page 2 has null cursor
    mock_fetch = AsyncMock(side_effect=[SAMPLE_MCP_PAGE_1, SAMPLE_MCP_PAGE_2])

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page", new=mock_fetch
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    # Must NOT call a third time after null cursor
    assert mock_fetch.call_count == 2


@pytest.mark.asyncio
async def test_mcp_skip_server_with_no_url(session, source_factory, redis_client):
    """Server with neither websiteUrl nor repository.url must be skipped.

    Only the server with a valid URL is inserted.
    """
    source = await source_factory(
        id="mcp:test-no-url",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page",
        new=AsyncMock(return_value=SAMPLE_MCP_NO_URL),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Only the valid server with websiteUrl is inserted; no-url server is skipped
    assert len(items) == 1
    assert items[0].url == "https://example.com/valid"


@pytest.mark.asyncio
async def test_mcp_repository_url_fallback(session, source_factory, redis_client):
    """When websiteUrl is absent, repository.url must be used as fallback."""
    source = await source_factory(
        id="mcp:test-repo-fallback",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page",
        new=AsyncMock(return_value=SAMPLE_MCP_REPO_FALLBACK),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 1
    assert items[0].url == "https://github.com/example/repo-only"


@pytest.mark.asyncio
async def test_mcp_dedup_skips_existing(session, source_factory, redis_client):
    """Pre-existing URL must be skipped."""
    source = await source_factory(
        id="mcp:test-dedup",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    # Pre-insert test-server
    existing_url = "https://example.com/test-server"
    existing = IntelItem(
        source_id=source.id,
        external_id="test-server",
        url=existing_url,
        url_hash=hashlib.sha256(existing_url.encode()).hexdigest(),
        title="test-server",
        content="Old description",
        primary_type="unknown",
        tags=[],
        status="raw",
        content_hash=hashlib.sha256(b"old").hexdigest(),
    )
    session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page",
        new=AsyncMock(return_value=SAMPLE_MCP_SINGLE_PAGE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Only 1 item (pre-existing, not duplicated)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_mcp_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Fetch failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="mcp:test-error",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page",
        new=AsyncMock(side_effect=Exception("Network error")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Network error"):
                await ingest_mcp_registry_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_mcp_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive MCP Registry source must not call fetch_mcp_registry_page."""
    source = await source_factory(
        id="mcp:test-inactive",
        type="mcp-registry",
        url="https://registry.modelcontextprotocol.io/v0/servers",
        is_active=False,
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=SAMPLE_MCP_SINGLE_PAGE)

    with patch(
        "src.workers.ingest_mcp_registry.fetch_mcp_registry_page", new=mock_fetch
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_mcp_registry_source(ctx, source.id)

    mock_fetch.assert_not_called()
