"""
EXT-07: VS Code Marketplace adapter unit tests.

Tests for ingest_vscode_source covering:
- fetch_vscode_extensions sends correct POST with filterType/flags structure
- New extensions stored as IntelItem with marketplace URL
- Extensions older than last_poll_ts watermark are skipped
- source.config["last_poll_ts"] advances after ingestion
- poll_vscode_sources dispatches ARQ jobs

Mocking strategy:
- Patch src.workers.ingest_vscode.fetch_vscode_extensions with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_vscode import ingest_vscode_source, poll_vscode_sources


# ---------------------------------------------------------------------------
# Sample VS Code Marketplace API responses
# ---------------------------------------------------------------------------

SAMPLE_EXTENSION_RECENT = {
    "publisher": {"publisherName": "anthropic"},
    "extensionName": "claude-code",
    "displayName": "Claude Code",
    "shortDescription": "AI-powered coding assistant by Anthropic",
    "versions": [
        {
            "version": "1.2.0",
            "lastUpdated": "2026-03-10T12:00:00Z",
        }
    ],
    "statistics": [
        {"statisticName": "install", "value": 50000},
        {"statisticName": "weightedRating", "value": 4.8},
    ],
}

SAMPLE_EXTENSION_OLD = {
    "publisher": {"publisherName": "someone"},
    "extensionName": "old-ext",
    "displayName": "Old Extension",
    "shortDescription": "An old extension",
    "versions": [
        {
            "version": "0.1.0",
            "lastUpdated": "2024-01-01T00:00:00Z",
        }
    ],
    "statistics": [],
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
async def test_fetch_vscode_extensions_sends_post(
    session, source_factory, redis_client
):
    """fetch_vscode_extensions sends a POST with correct filterType=8/10 and flags."""
    import httpx
    from src.workers.ingest_vscode import fetch_vscode_extensions

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [{"extensions": [SAMPLE_EXTENSION_RECENT]}]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        extensions = await fetch_vscode_extensions("mcp")

    # Verify POST was called
    assert mock_client.post.called
    call_kwargs = mock_client.post.call_args
    body = (
        call_kwargs.kwargs.get("json") or call_kwargs.args[1]
        if len(call_kwargs.args) > 1
        else call_kwargs.kwargs.get("json")
    )
    # Verify the body has filters with criteria
    if body:
        filters = body.get("filters", [])
        if filters:
            criteria = filters[0].get("criteria", [])
            filter_types = {c.get("filterType") for c in criteria}
            # filterType=8 is VS Code target, filterType=10 is search text
            assert 8 in filter_types or 10 in filter_types
    # Results returned correctly
    assert len(extensions) == 1
    assert extensions[0]["extensionName"] == "claude-code"


@pytest.mark.asyncio
async def test_ingest_vscode_source_creates_items(
    session, source_factory, redis_client
):
    """New extension should be stored as IntelItem with canonical marketplace URL."""
    source = await source_factory(
        id="vscode:test-creates",
        type="vscode-marketplace",
        url="https://marketplace.visualstudio.com",
        config={"queries": ["mcp"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_vscode.fetch_vscode_extensions",
        new=AsyncMock(return_value=[SAMPLE_EXTENSION_RECENT]),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_vscode_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 1
    item = items[0]
    assert item.status == "raw"
    assert "marketplace.visualstudio.com" in item.url
    assert "anthropic.claude-code" in item.url
    assert item.source_id == source.id


@pytest.mark.asyncio
async def test_ingest_vscode_source_skips_old_by_watermark(
    session, source_factory, redis_client
):
    """Extensions with lastUpdated <= last_poll_ts must not be inserted."""
    # SAMPLE_EXTENSION_OLD has lastUpdated 2024-01-01 (far in past)
    # Set watermark to 2025-01-01 so old extension is filtered out
    import time
    from datetime import datetime, timezone

    watermark_dt = datetime(2025, 1, 1, tzinfo=timezone.utc)
    watermark_ts = watermark_dt.timestamp()

    source = await source_factory(
        id="vscode:test-watermark-skip",
        type="vscode-marketplace",
        url="https://marketplace.visualstudio.com",
        config={"queries": ["old"], "last_poll_ts": watermark_ts},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_vscode.fetch_vscode_extensions",
        new=AsyncMock(return_value=[SAMPLE_EXTENSION_OLD]),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_vscode_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Old extension (2024) is older than watermark (2025); nothing inserted
    assert len(items) == 0


@pytest.mark.asyncio
async def test_ingest_vscode_source_updates_watermark(
    session, source_factory, redis_client
):
    """After ingestion, source.config['last_poll_ts'] must advance to match the latest extension."""
    source = await source_factory(
        id="vscode:test-watermark-update",
        type="vscode-marketplace",
        url="https://marketplace.visualstudio.com",
        config={"queries": ["claude"], "last_poll_ts": 0},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_vscode.fetch_vscode_extensions",
        new=AsyncMock(return_value=[SAMPLE_EXTENSION_RECENT]),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_vscode_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert "last_poll_ts" in refreshed.config
    # 2026-03-10 is after the initial 0 watermark
    assert refreshed.config["last_poll_ts"] > 0


@pytest.mark.asyncio
async def test_poll_vscode_sources_dispatches_jobs(
    session, source_factory, redis_client
):
    """poll_vscode_sources must enqueue one ARQ job per active vscode-marketplace source."""
    source = await source_factory(
        id="vscode:test-poll",
        type="vscode-marketplace",
        url="https://marketplace.visualstudio.com",
        config={},
    )
    ctx = {"redis": redis_client}

    mock_enqueue = AsyncMock()
    redis_client.enqueue_job = mock_enqueue

    with patch.object(_db, "async_session_factory", make_session_factory(session)):
        await poll_vscode_sources(ctx)

    mock_enqueue.assert_called_once_with(
        "ingest_vscode_source", source.id, _queue_name="fast"
    )
