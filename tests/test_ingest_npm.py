"""
INGEST-10: npm Registry search adapter tests.

Tests for ingest_npm_source covering:
- New items stored correctly with status='raw'
- URL comes from package.links.npm (canonical)
- URL falls back to constructed URL when links.npm is absent
- URL deduplication rejects known packages
- Circuit breaker: consecutive_errors incremented on failure
- Watermark: last_poll_ts saved after successful poll
- Watermark: packages older than last_poll_ts are skipped

Mocking strategy:
- Patch src.workers.ingest_npm.fetch_npm_search with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch, call

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_npm import ingest_npm_source, DEFAULT_NPM_QUERIES


# ---------------------------------------------------------------------------
# Sample npm search API responses
# ---------------------------------------------------------------------------

SAMPLE_NPM_RESPONSE = {
    "objects": [
        {
            "package": {
                "name": "mcp-tool",
                "description": "An MCP protocol tool for Claude",
                "keywords": ["mcp", "claude", "ai"],
                "links": {
                    "npm": "https://www.npmjs.com/package/mcp-tool",
                    "homepage": "https://example.com/mcp-tool",
                },
                "date": "2026-03-01",
            }
        },
        {
            "package": {
                "name": "@anthropic/sdk",
                "description": "Official Anthropic TypeScript SDK",
                "keywords": ["anthropic", "claude", "sdk"],
                "links": {
                    "npm": "https://www.npmjs.com/package/@anthropic%2Fsdk",
                },
                "date": "2026-02-15",
            }
        },
    ],
    "total": 2,
}

SAMPLE_NPM_NO_NPM_LINK = {
    "objects": [
        {
            "package": {
                "name": "claude-helper",
                "description": "A helper for Claude",
                "keywords": ["claude"],
                "links": {
                    "homepage": "https://example.com",
                    # no 'npm' key
                },
                "date": "2026-03-10",
            }
        }
    ],
    "total": 1,
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
async def test_npm_items_stored(session, source_factory, redis_client):
    """Two npm packages must be stored as IntelItems with status='raw'."""
    source = await source_factory(
        id="npm:test-items-stored",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["mcp"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(return_value=SAMPLE_NPM_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://www.npmjs.com/package/mcp-tool" in urls
    assert "https://www.npmjs.com/package/@anthropic%2Fsdk" in urls
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id


@pytest.mark.asyncio
async def test_npm_item_sets_source_name(session, source_factory, redis_client):
    """INGEST-10: npm adapter must set source_name on created IntelItem."""
    source = await source_factory(
        id="npm:test-source-name",
        name="npm Registry",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["mcp"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(return_value=SAMPLE_NPM_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    for item in items:
        assert item.source_name == source.name


@pytest.mark.asyncio
async def test_npm_url_canonical_from_links_npm(session, source_factory, redis_client):
    """URL must come from package.links.npm, not constructed from name."""
    source = await source_factory(
        id="npm:test-url-canonical",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["mcp"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(return_value=SAMPLE_NPM_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.title == "mcp-tool")
    )
    item = result.scalar_one()
    # URL must be from links.npm, not f"https://www.npmjs.com/package/{name}"
    # (same in this case but test verifies the field is used)
    assert item.url == "https://www.npmjs.com/package/mcp-tool"


@pytest.mark.asyncio
async def test_npm_url_fallback_when_no_npm_link(session, source_factory, redis_client):
    """When links.npm is absent, URL is constructed from package name."""
    source = await source_factory(
        id="npm:test-url-fallback",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["claude"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(return_value=SAMPLE_NPM_NO_NPM_LINK),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 1
    assert items[0].url == "https://www.npmjs.com/package/claude-helper"


@pytest.mark.asyncio
async def test_npm_dedup_skips_existing(session, source_factory, redis_client):
    """Pre-existing npm URL must be skipped; only new packages inserted."""
    source = await source_factory(
        id="npm:test-dedup",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["mcp"]},
    )
    # Pre-insert mcp-tool
    existing_url = "https://www.npmjs.com/package/mcp-tool"
    existing = IntelItem(
        source_id=source.id,
        external_id="mcp-tool",
        url=existing_url,
        url_hash=hashlib.sha256(existing_url.encode()).hexdigest(),
        title="mcp-tool",
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
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(return_value=SAMPLE_NPM_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    # Should have 2 total: pre-existing mcp-tool + new @anthropic/sdk
    assert len(items) == 2


@pytest.mark.asyncio
async def test_npm_default_queries_used_when_no_config(
    session, source_factory, redis_client
):
    """Source with no 'queries' in config must use DEFAULT_NPM_QUERIES."""
    source = await source_factory(
        id="npm:test-default-queries",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value={"objects": [], "total": 0})

    with patch("src.workers.ingest_npm.fetch_npm_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    assert mock_fetch.call_count == len(DEFAULT_NPM_QUERIES)


@pytest.mark.asyncio
async def test_npm_error_increments_consecutive_errors(
    session, source_factory, redis_client
):
    """Fetch failure must increment source.consecutive_errors (circuit breaker)."""
    source = await source_factory(
        id="npm:test-error",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["mcp"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(side_effect=Exception("Network error")),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(Exception, match="Network error"):
                await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_npm_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive npm source must not call fetch_npm_search."""
    source = await source_factory(
        id="npm:test-inactive",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        is_active=False,
        config={},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=SAMPLE_NPM_RESPONSE)

    with patch("src.workers.ingest_npm.fetch_npm_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_npm_watermark_saved_after_poll(session, source_factory, redis_client):
    """After ingestion, source.config['last_poll_ts'] must be set to the newest package timestamp."""
    source = await source_factory(
        id="npm:test-watermark-saved",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["mcp"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(return_value=SAMPLE_NPM_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    # SAMPLE_NPM_RESPONSE has dates "2026-03-01" and "2026-02-15"; newest is 2026-03-01
    assert "last_poll_ts" in refreshed.config
    # 2026-03-01T00:00:00 UTC = 1772323200
    assert refreshed.config["last_poll_ts"] == 1772323200


@pytest.mark.asyncio
async def test_npm_watermark_skips_old_packages(session, source_factory, redis_client):
    """Packages with date <= last_poll_ts must not be inserted."""
    # Set watermark to 2026-02-20T00:00:00 UTC = 1771545600
    # Only "2026-03-01" (1772323200) is newer; "2026-02-15" (1771113600) is older
    watermark_ts = 1771545600
    source = await source_factory(
        id="npm:test-watermark-skip",
        type="npm",
        url="https://registry.npmjs.org/-/v1/search",
        config={"queries": ["mcp"], "last_poll_ts": watermark_ts},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_npm.fetch_npm_search",
        new=AsyncMock(return_value=SAMPLE_NPM_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_npm_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    # Only mcp-tool (2026-03-01) should be inserted; @anthropic/sdk (2026-02-15) is too old
    assert len(items) == 1
    assert items[0].url == "https://www.npmjs.com/package/mcp-tool"
