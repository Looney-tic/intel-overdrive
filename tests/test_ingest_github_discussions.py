"""
EXT-06: GitHub Discussions adapter unit tests.

Tests for ingest_github_discussions_source covering:
- fetch_github_discussions parses GraphQL response and returns nodes
- fetch_github_discussions raises ValueError on GraphQL errors
- New discussions stored as IntelItem with discussion URL and external_id
- poll_github_discussions_sources returns early when GITHUB_TOKEN is None
- poll_github_discussions_sources dispatches ARQ jobs when token is set

Mocking strategy:
- Patch src.workers.ingest_github_discussions.fetch_github_discussions with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_github_discussions import (
    ingest_github_discussions_source,
    poll_github_discussions_sources,
)


# ---------------------------------------------------------------------------
# Sample GraphQL responses
# ---------------------------------------------------------------------------

SAMPLE_DISCUSSION_NODES = [
    {
        "id": "D_kwDOABCDEF4ABCDE",
        "title": "How to use Claude API with MCP?",
        "url": "https://github.com/anthropics/anthropic-sdk-python/discussions/123",
        "createdAt": "2026-03-10T14:00:00Z",
        "author": {"login": "someuser"},
        "category": {"name": "Q&A"},
        "bodyText": "I want to integrate Claude with MCP. Any examples?",
        "upvoteCount": 5,
        "comments": {"totalCount": 3},
    },
    {
        "id": "D_kwDOABCDEF4ABCDF",
        "title": "Feature request: streaming improvements",
        "url": "https://github.com/anthropics/anthropic-sdk-python/discussions/124",
        "createdAt": "2026-03-11T09:00:00Z",
        "author": {"login": "anotheruser"},
        "category": {"name": "Ideas"},
        "bodyText": "Would be great to have faster streaming support.",
        "upvoteCount": 12,
        "comments": {"totalCount": 7},
    },
]


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
async def test_fetch_github_discussions_parses_graphql(
    session, source_factory, redis_client
):
    """Mock httpx POST: fetch_github_discussions returns node list from GraphQL response."""
    import httpx
    from src.workers.ingest_github_discussions import fetch_github_discussions

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {}
    mock_response.json.return_value = {
        "data": {"repository": {"discussions": {"nodes": SAMPLE_DISCUSSION_NODES}}}
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        nodes = await fetch_github_discussions(
            "anthropics", "anthropic-sdk-python", "test-token"
        )

    assert len(nodes) == 2
    assert nodes[0]["id"] == "D_kwDOABCDEF4ABCDE"
    assert nodes[1]["title"] == "Feature request: streaming improvements"


@pytest.mark.asyncio
async def test_fetch_github_discussions_raises_on_graphql_errors(
    session, source_factory, redis_client
):
    """When response contains 'errors' key, ValueError must be raised."""
    import httpx
    from src.workers.ingest_github_discussions import fetch_github_discussions

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.headers = {}
    mock_response.json.return_value = {
        "errors": [{"message": "Could not resolve to a Repository"}]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="GitHub GraphQL errors"):
            await fetch_github_discussions("invalid", "repo", "test-token")


@pytest.mark.asyncio
async def test_ingest_discussions_creates_items(session, source_factory, redis_client):
    """Fetched discussions stored as IntelItems with correct URL and external_id."""
    source = await source_factory(
        id="github-discussions:test-creates",
        type="github-discussions",
        url="https://github.com/anthropics/anthropic-sdk-python",
        config={
            "repos": [{"owner": "anthropics", "name": "anthropic-sdk-python"}],
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_discussions.fetch_github_discussions",
        new=AsyncMock(return_value=SAMPLE_DISCUSSION_NODES),
    ):
        with patch(
            "src.workers.ingest_github_discussions.get_settings",
        ) as mock_settings:
            mock_settings.return_value.GITHUB_TOKEN = "test-token"
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_discussions_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://github.com/anthropics/anthropic-sdk-python/discussions/123" in urls
    assert "https://github.com/anthropics/anthropic-sdk-python/discussions/124" in urls
    for item in items:
        assert item.status == "raw"
        assert item.external_id is not None


@pytest.mark.asyncio
async def test_ingest_discussions_skips_without_token(
    session, source_factory, redis_client
):
    """When GITHUB_TOKEN is None, ingest_github_discussions_source returns early."""
    source = await source_factory(
        id="github-discussions:test-no-token",
        type="github-discussions",
        url="https://github.com/anthropics/anthropic-sdk-python",
        config={"repos": [{"owner": "anthropics", "name": "anthropic-sdk-python"}]},
    )
    ctx = {"redis": redis_client}

    mock_fetch = AsyncMock(return_value=SAMPLE_DISCUSSION_NODES)

    with patch(
        "src.workers.ingest_github_discussions.fetch_github_discussions",
        new=mock_fetch,
    ):
        with patch(
            "src.workers.ingest_github_discussions.get_settings",
        ) as mock_settings:
            mock_settings.return_value.GITHUB_TOKEN = None
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_discussions_source(ctx, source.id)

    # No fetch called because token check exits early
    mock_fetch.assert_not_called()

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0


@pytest.mark.asyncio
async def test_poll_discussions_dispatches_jobs(session, source_factory, redis_client):
    """poll_github_discussions_sources dispatches one job per active github-discussions source."""
    source = await source_factory(
        id="github-discussions:test-poll",
        type="github-discussions",
        url="https://github.com/anthropics/anthropic-sdk-python",
        config={"repos": [{"owner": "anthropics", "name": "anthropic-sdk-python"}]},
    )
    ctx = {"redis": redis_client}

    mock_enqueue = AsyncMock()
    redis_client.enqueue_job = mock_enqueue

    with patch(
        "src.workers.ingest_github_discussions.get_settings",
    ) as mock_settings:
        mock_settings.return_value.GITHUB_TOKEN = "test-token"
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await poll_github_discussions_sources(ctx)

    mock_enqueue.assert_called_once_with(
        "ingest_github_discussions_source", source.id, _queue_name="fast"
    )
