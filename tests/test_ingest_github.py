"""
INGEST-03: GitHub Search API ingestion worker tests.

Tests for ingest_github_source covering:
- Repository items stored with correct fields
- URL deduplication rejects known repos
- Rate limit stop: stops querying when x-ratelimit-remaining <= 2
- 429 HTTPStatusError handled gracefully (no re-raise)
- 403 HTTPStatusError handled gracefully (no re-raise)
- Custom queries from source.config["queries"] used instead of defaults
- 403/429 result in partial success (pre-rate-limit items stored)

Mocking strategy:
- Patch src.workers.ingest_github.fetch_github_search with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch, call

import httpx
import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_github import ingest_github_source, DEFAULT_GITHUB_QUERIES


# ---------------------------------------------------------------------------
# Sample GitHub API responses
# ---------------------------------------------------------------------------

SAMPLE_GITHUB_RESPONSE = {
    "total_count": 2,
    "items": [
        {
            "id": 12345,
            "full_name": "user/repo-1",
            "html_url": "https://github.com/user/repo-1",
            "description": "A Claude Code skill",
            "topics": ["claude-code", "mcp"],
        },
        {
            "id": 67890,
            "full_name": "user/repo-2",
            "html_url": "https://github.com/user/repo-2",
            "description": "Another tool",
            "topics": ["claude-code"],
        },
    ],
}

SAMPLE_HEADERS = {
    "x-ratelimit-remaining": "25",
    "x-ratelimit-limit": "30",
}

LOW_RATE_HEADERS = {
    "x-ratelimit-remaining": "1",
    "x-ratelimit-limit": "30",
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


def make_http_status_error(status_code: int) -> httpx.HTTPStatusError:
    """Build an httpx.HTTPStatusError with the given status code."""
    request = httpx.Request("GET", "https://api.github.com/search/repositories")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError(
        message=f"{status_code} Error", request=request, response=response
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_items_stored(session, source_factory, redis_client):
    """Two repos from the GitHub response must be stored as IntelItems with correct fields."""
    source = await source_factory(
        id="gh:test-items-stored",
        type="github",
        url="https://api.github.com",
        config={"queries": ["topic:claude-code"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(return_value=(SAMPLE_GITHUB_RESPONSE, SAMPLE_HEADERS)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://github.com/user/repo-1" in urls
    assert "https://github.com/user/repo-2" in urls

    # Check tags (topics) are stored
    repo1 = next(i for i in items if i.url == "https://github.com/user/repo-1")
    assert "claude-code" in repo1.tags
    assert "mcp" in repo1.tags

    # Check status
    for item in items:
        assert item.status == "raw"
        assert item.source_id == source.id


@pytest.mark.asyncio
async def test_github_duplicate_rejected(session, source_factory, redis_client):
    """Repo URL already in DB must be skipped; only new repos are inserted."""
    source = await source_factory(
        id="gh:test-dedup",
        type="github",
        url="https://api.github.com",
        config={"queries": ["topic:claude-code"]},
    )
    # Pre-insert repo-1
    existing_url = "https://github.com/user/repo-1"
    existing = IntelItem(
        source_id=source.id,
        external_id="12345",
        url=existing_url,
        url_hash=hashlib.sha256(existing_url.encode()).hexdigest(),
        title="user/repo-1",
        content="Existing repo",
        primary_type="unknown",
        tags=["claude-code"],
        status="raw",
        content_hash=hashlib.sha256(b"existing").hexdigest(),
    )
    session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(return_value=(SAMPLE_GITHUB_RESPONSE, SAMPLE_HEADERS)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    # Should have 2 total: existing repo-1 + newly-inserted repo-2
    assert len(items) == 2
    urls = {item.url for item in items}
    assert "https://github.com/user/repo-1" in urls
    assert "https://github.com/user/repo-2" in urls


@pytest.mark.asyncio
async def test_rate_limit_stop(session, source_factory, redis_client):
    """When x-ratelimit-remaining <= 2 after a query, stop before the next query.

    Items from the first query are stored; mock is called only once.
    """
    source = await source_factory(
        id="gh:test-rate-limit-stop",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1", "q2", "q3"]},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(SAMPLE_GITHUB_RESPONSE, LOW_RATE_HEADERS))

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # Only one call: stopped after q1 because remaining=1 <= 2
    assert mock_fetch.call_count == 1

    # Items from the first query ARE stored (process before rate limit check)
    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_rate_limit_missing_header_defaults_to_1(
    session, source_factory, redis_client
):
    """Missing x-ratelimit-remaining header defaults to '1' and stops after first query."""
    source = await source_factory(
        id="gh:test-missing-header",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1", "q2"]},
    )
    ctx = {"redis": redis_client}
    # Return headers without x-ratelimit-remaining
    mock_fetch = AsyncMock(
        return_value=(SAMPLE_GITHUB_RESPONSE, {"x-ratelimit-limit": "30"})
    )

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # Default is "1" which is <= 2, so stops after first query
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_rate_limit_429_handled(session, source_factory, redis_client):
    """HTTP 429 must be caught and the function must not re-raise — breaks the query loop."""
    source = await source_factory(
        id="gh:test-429",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(side_effect=make_http_status_error(429)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            # Must NOT raise — 429 is caught and treated as rate limit signal
            await ingest_github_source(ctx, source.id)


@pytest.mark.asyncio
async def test_rate_limit_403_handled(session, source_factory, redis_client):
    """HTTP 403 must be caught and the function must not re-raise — breaks the query loop."""
    source = await source_factory(
        id="gh:test-403",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(side_effect=make_http_status_error(403)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            # Must NOT raise — 403 is caught and treated as rate limit signal
            await ingest_github_source(ctx, source.id)


@pytest.mark.asyncio
async def test_non_rate_limit_http_error_propagates(
    session, source_factory, redis_client
):
    """HTTP 500 (non-rate-limit) must propagate and trigger circuit breaker."""
    source = await source_factory(
        id="gh:test-500-error",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1"]},
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(side_effect=make_http_status_error(500)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(httpx.HTTPStatusError):
                await ingest_github_source(ctx, source.id)

    # Circuit breaker: source should have consecutive_errors incremented
    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors >= 1


@pytest.mark.asyncio
async def test_custom_queries_from_config(session, source_factory, redis_client):
    """Source config['queries'] must override DEFAULT_GITHUB_QUERIES."""
    source = await source_factory(
        id="gh:test-custom-queries",
        type="github",
        url="https://api.github.com",
        config={"queries": ["custom-query-xyz"]},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(
        return_value=({"total_count": 0, "items": []}, SAMPLE_HEADERS)
    )

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    mock_fetch.assert_called_once()
    args, kwargs = mock_fetch.call_args
    # first positional arg is the query
    query_used = args[0] if args else kwargs.get("query")
    assert query_used == "custom-query-xyz"


@pytest.mark.asyncio
async def test_default_queries_used_when_no_config(
    session, source_factory, redis_client
):
    """Source with empty config must use DEFAULT_GITHUB_QUERIES."""
    source = await source_factory(
        id="gh:test-default-queries",
        type="github",
        url="https://api.github.com",
        config={},  # no 'queries' key
    )
    ctx = {"redis": redis_client}
    # Return high remaining so all queries run, return empty items
    mock_fetch = AsyncMock(
        return_value=({"total_count": 0, "items": []}, {"x-ratelimit-remaining": "29"})
    )

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    assert mock_fetch.call_count == len(DEFAULT_GITHUB_QUERIES)
    called_queries = [c.args[0] for c in mock_fetch.call_args_list]
    for q in DEFAULT_GITHUB_QUERIES:
        assert q in called_queries


@pytest.mark.asyncio
async def test_rate_limit_partial_success_stores_items(
    session, source_factory, redis_client
):
    """When rate limited mid-way, items from successful queries are still committed."""
    source = await source_factory(
        id="gh:test-partial-success",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1", "q2"]},
    )
    ctx = {"redis": redis_client}

    # First query succeeds; second raises 429
    mock_fetch = AsyncMock(
        side_effect=[
            (SAMPLE_GITHUB_RESPONSE, SAMPLE_HEADERS),
            make_http_status_error(429),
        ]
    )

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # Items from q1 are stored
    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 2

    # handle_source_success called (not handle_source_error)
    result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = result.scalar_one()
    assert refreshed.consecutive_errors == 0


@pytest.mark.asyncio
async def test_inactive_source_skipped(session, source_factory, redis_client):
    """Inactive GitHub source must be skipped without calling fetch_github_search."""
    source = await source_factory(
        id="gh:test-inactive",
        type="github",
        url="https://api.github.com",
        is_active=False,
        config={},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(return_value=(SAMPLE_GITHUB_RESPONSE, SAMPLE_HEADERS))

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limit_boundary_at_2_stops(session, source_factory, redis_client):
    """x-ratelimit-remaining == 2 (boundary) must stop after current query."""
    source = await source_factory(
        id="gh:test-boundary-2",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1", "q2"]},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(
        return_value=(SAMPLE_GITHUB_RESPONSE, {"x-ratelimit-remaining": "2"})
    )

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # remaining=2 <= 2, so stops after first query
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_rate_limit_at_3_continues(session, source_factory, redis_client):
    """x-ratelimit-remaining == 3 (above boundary) must continue to next query."""
    source = await source_factory(
        id="gh:test-boundary-3",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1", "q2"]},
    )
    ctx = {"redis": redis_client}
    mock_fetch = AsyncMock(
        return_value=({"total_count": 0, "items": []}, {"x-ratelimit-remaining": "3"})
    )

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # remaining=3 > 2, so both queries run
    assert mock_fetch.call_count == 2


@pytest.mark.asyncio
async def test_cooldown_skips_fetch(session, source_factory, redis_client):
    """Source on cooldown must skip fetch entirely — no API call, no items."""
    source = await source_factory(
        id="gh:test-cooldown-skip",
        type="github",
        url="https://api.github.com",
        config={"queries": ["q1"]},
        poll_interval_seconds=3600,
    )
    ctx = {"redis": redis_client}

    # Set cooldown manually (simulates recent poll)
    await redis_client.set(f"source:cooldown:{source.id}", "1", ex=3600, nx=True)

    mock_fetch = AsyncMock(return_value=(SAMPLE_GITHUB_RESPONSE, SAMPLE_HEADERS))

    with patch("src.workers.ingest_github.fetch_github_search", new=mock_fetch):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    mock_fetch.assert_not_called()

    # No items created
    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    assert len(result.scalars().all()) == 0
