"""
EXT-01: PyPI adapter unit tests.

Tests for ingest_pypi_source covering:
- Fetched package metadata stored as IntelItem with status='raw'
- Existing version in last_versions skips the package (watermark)
- New version (different from stored) creates a new IntelItem
- source.config["last_versions"] updated after successful ingestion
- poll_pypi_sources dispatches an ARQ job per active source

Mocking strategy:
- Patch src.workers.ingest_pypi.fetch_pypi_package with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_pypi import ingest_pypi_source, poll_pypi_sources


# ---------------------------------------------------------------------------
# Sample PyPI API responses
# ---------------------------------------------------------------------------

SAMPLE_PYPI_RESPONSE = {
    "info": {
        "name": "anthropic",
        "version": "0.50.0",
        "summary": "The official Python library for the Anthropic API",
        "keywords": "ai,anthropic,claude",
        "classifiers": [
            "Programming Language :: Python :: 3",
            "License :: OSI Approved :: MIT License",
        ],
    },
    "urls": [
        {
            "upload_time_iso_8601": "2026-03-01T10:00:00Z",
            "filename": "anthropic-0.50.0-py3-none-any.whl",
        }
    ],
    "releases": {},
}

SAMPLE_PYPI_RESPONSE_V2 = {
    "info": {
        "name": "mcp",
        "version": "1.5.0",
        "summary": "Model Context Protocol SDK",
        "keywords": "mcp,llm",
        "classifiers": [],
    },
    "urls": [
        {
            "upload_time_iso_8601": "2026-03-10T08:00:00Z",
            "filename": "mcp-1.5.0-py3-none-any.whl",
        }
    ],
    "releases": {},
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
async def test_fetch_pypi_package_returns_parsed_json(
    session, source_factory, redis_client
):
    """Mock httpx response: fetch_pypi_package returns parsed JSON with info and urls."""
    import httpx
    from src.workers.ingest_pypi import fetch_pypi_package

    mock_response = MagicMock()
    mock_response.json.return_value = SAMPLE_PYPI_RESPONSE
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await fetch_pypi_package("anthropic")

    assert result["info"]["name"] == "anthropic"
    assert result["info"]["version"] == "0.50.0"
    assert "urls" in result


@pytest.mark.asyncio
async def test_ingest_pypi_source_creates_items(session, source_factory, redis_client):
    """Fetch mock returns package data; IntelItem should be created with correct fields."""
    source = await source_factory(
        id="pypi:test-creates",
        type="pypi",
        url="https://pypi.org",
        config={"packages": ["anthropic"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_pypi.fetch_pypi_package",
        new=AsyncMock(return_value=SAMPLE_PYPI_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_pypi_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 1
    item = items[0]
    assert item.status == "raw"
    assert "anthropic" in item.url
    assert "0.50.0" in item.url
    assert (
        item.title
        == "anthropic 0.50.0 — The official Python library for the Anthropic API"
    )
    assert item.source_id == source.id


@pytest.mark.asyncio
async def test_ingest_pypi_source_skips_existing_version(
    session, source_factory, redis_client
):
    """When last_versions already has the current version, no IntelItem is created."""
    source = await source_factory(
        id="pypi:test-skip-version",
        type="pypi",
        url="https://pypi.org",
        config={"packages": ["anthropic"], "last_versions": {"anthropic": "0.50.0"}},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_pypi.fetch_pypi_package",
        new=AsyncMock(return_value=SAMPLE_PYPI_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_pypi_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Version matches last_versions entry; nothing inserted
    assert len(items) == 0


@pytest.mark.asyncio
async def test_ingest_pypi_source_updates_watermark(
    session, source_factory, redis_client
):
    """After ingestion, source.config['last_versions'] must reflect the newly seen version."""
    source = await source_factory(
        id="pypi:test-watermark",
        type="pypi",
        url="https://pypi.org",
        config={"packages": ["anthropic"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_pypi.fetch_pypi_package",
        new=AsyncMock(return_value=SAMPLE_PYPI_RESPONSE),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_pypi_source(ctx, source.id)

    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert "last_versions" in refreshed.config
    assert refreshed.config["last_versions"]["anthropic"] == "0.50.0"


@pytest.mark.asyncio
async def test_poll_pypi_sources_dispatches_jobs(session, source_factory, redis_client):
    """poll_pypi_sources must enqueue one ARQ job per active pypi source."""
    source = await source_factory(
        id="pypi:test-poll",
        type="pypi",
        url="https://pypi.org",
        config={},
    )
    ctx = {"redis": redis_client}

    mock_enqueue = AsyncMock()
    redis_client.enqueue_job = mock_enqueue

    with patch.object(_db, "async_session_factory", make_session_factory(session)):
        await poll_pypi_sources(ctx)

    mock_enqueue.assert_called_once_with(
        "ingest_pypi_source", source.id, _queue_name="fast"
    )
