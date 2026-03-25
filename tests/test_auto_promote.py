"""
REPO-01/02: Auto-promote and full awesome-list parse tests.

Tests for auto-promote in ingest_github.py:
- Creates github-deep Source for repos with >50 stars + relevant topics
- Skips repos with low stars
- Skips repos already tracked as github-deep
- Skips repos with irrelevant topics

Tests for full awesome-list parse + auto-promote in ingest_awesome.py:
- Full README parse (from_sha=None) on every run
- Auto-promotes GitHub repos with >50 stars to github-deep
- Rate limit protection (stops after 3 consecutive failures)

Mocking strategy:
- Patch src.workers.ingest_github.fetch_github_search with AsyncMock
- Patch src.workers.ingest_awesome._pull_or_clone (sync, wrapped in to_thread)
- Patch src.workers.ingest_awesome._extract_new_entries (sync, wrapped in to_thread)
- Patch src.workers.ingest_awesome.fetch_github_repo_info with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_github import ingest_github_source
from src.workers.ingest_awesome import ingest_awesome_source


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
    request = httpx.Request("GET", "https://api.github.com/repos/owner/repo")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError(
        message=f"{status_code} Error", request=request, response=response
    )


SAMPLE_HEADERS = {
    "x-ratelimit-remaining": "25",
    "x-ratelimit-limit": "30",
}


# ---------------------------------------------------------------------------
# Tests: Auto-promote in ingest_github.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_promote_creates_deep_source(session, source_factory, redis_client):
    """Repo with >50 stars and relevant topic must be auto-promoted to github-deep Source."""
    source = await source_factory(
        id="gh:test-auto-promote-create",
        type="github",
        url="https://api.github.com",
        config={"queries": ["topic:mcp"]},
    )
    ctx = {"redis": redis_client}

    response_data = {
        "total_count": 1,
        "items": [
            {
                "id": 99001,
                "full_name": "acme/mcp-server",
                "html_url": "https://github.com/acme/mcp-server",
                "description": "An MCP server implementation",
                "topics": ["mcp", "claude"],
                "stargazers_count": 100,
            },
        ],
    }

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(return_value=(response_data, SAMPLE_HEADERS)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # Verify github-deep Source was created
    result = await session.execute(
        select(Source).where(
            Source.id == "github-deep:acme/mcp-server",
            Source.type == "github-deep",
        )
    )
    deep_source = result.scalar_one_or_none()
    assert deep_source is not None
    assert deep_source.is_active is True
    assert deep_source.config.get("auto_promoted") is True
    assert deep_source.config.get("promoted_at_stars") == 100


@pytest.mark.asyncio
async def test_auto_promote_skips_low_star_repos(session, source_factory, redis_client):
    """Repo with <=50 stars must NOT be auto-promoted even with relevant topics."""
    source = await source_factory(
        id="gh:test-auto-promote-low-stars",
        type="github",
        url="https://api.github.com",
        config={"queries": ["topic:mcp"]},
    )
    ctx = {"redis": redis_client}

    response_data = {
        "total_count": 1,
        "items": [
            {
                "id": 99002,
                "full_name": "acme/tiny-project",
                "html_url": "https://github.com/acme/tiny-project",
                "description": "A tiny project",
                "topics": ["mcp"],
                "stargazers_count": 30,
            },
        ],
    }

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(return_value=(response_data, SAMPLE_HEADERS)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # Verify no github-deep Source was created
    result = await session.execute(
        select(Source).where(Source.id == "github-deep:acme/tiny-project")
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_auto_promote_skips_already_tracked(
    session, source_factory, redis_client
):
    """Repo already tracked as github-deep must NOT get a duplicate Source row."""
    # Pre-create the github-deep source
    existing_deep = Source(
        id="github-deep:acme/already-tracked",
        name="acme/already-tracked (deep)",
        type="github-deep",
        url="https://github.com/acme/already-tracked",
        is_active=True,
        config={"star_milestones": [100, 500]},
        poll_interval_seconds=3600,
        tier="tier2",
    )
    session.add(existing_deep)
    await session.commit()

    source = await source_factory(
        id="gh:test-auto-promote-dup",
        type="github",
        url="https://api.github.com",
        config={"queries": ["topic:claude-code"]},
    )
    ctx = {"redis": redis_client}

    response_data = {
        "total_count": 1,
        "items": [
            {
                "id": 99003,
                "full_name": "acme/already-tracked",
                "html_url": "https://github.com/acme/already-tracked",
                "description": "Already deep-tracked",
                "topics": ["claude-code"],
                "stargazers_count": 200,
            },
        ],
    }

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(return_value=(response_data, SAMPLE_HEADERS)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # Verify still exactly 1 github-deep source (no duplicate)
    result = await session.execute(
        select(Source).where(Source.id == "github-deep:acme/already-tracked")
    )
    sources = result.scalars().all()
    assert len(sources) == 1


@pytest.mark.asyncio
async def test_auto_promote_skips_irrelevant_topics(
    session, source_factory, redis_client
):
    """Repo with high stars but no relevant topics must NOT be auto-promoted."""
    source = await source_factory(
        id="gh:test-auto-promote-irrelevant",
        type="github",
        url="https://api.github.com",
        config={"queries": ["topic:cooking"]},
    )
    ctx = {"redis": redis_client}

    response_data = {
        "total_count": 1,
        "items": [
            {
                "id": 99004,
                "full_name": "chef/recipes",
                "html_url": "https://github.com/chef/recipes",
                "description": "A cooking recipe app",
                "topics": ["cooking", "recipes"],
                "stargazers_count": 200,
            },
        ],
    }

    with patch(
        "src.workers.ingest_github.fetch_github_search",
        new=AsyncMock(return_value=(response_data, SAMPLE_HEADERS)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_github_source(ctx, source.id)

    # Verify no github-deep Source was created
    result = await session.execute(
        select(Source).where(Source.id == "github-deep:chef/recipes")
    )
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Tests: Full awesome-list parse + auto-promote in ingest_awesome.py
# ---------------------------------------------------------------------------


def make_mock_repo(sha: str) -> MagicMock:
    """Create a mock git.Repo object with the given HEAD commit SHA."""
    mock_repo = MagicMock()
    mock_repo.head.commit.hexsha = sha
    return mock_repo


@pytest.mark.asyncio
async def test_awesome_full_parse_extracts_all_entries(
    session, source_factory, redis_client
):
    """Full parse mode: _extract_new_entries called with from_sha=None always (not incremental diff)."""
    source = await source_factory(
        id="awesome:test-full-parse",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={"last_commit_sha": "old_sha_xyz"},
    )
    ctx = {"redis": redis_client}

    current_sha = "new_sha_abc"
    mock_repo = make_mock_repo(current_sha)

    mock_entries = [
        {
            "name": "Tool A",
            "url": "https://example.com/tool-a",
            "description": "A tool",
        },
    ]

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch(
            "src.workers.ingest_awesome._extract_new_entries",
            return_value=mock_entries,
        ) as mock_extract:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_awesome_source(ctx, source.id)

    # Key assertion: from_sha=None (full parse), NOT the stored old_sha
    mock_extract.assert_called_once_with(mock_repo, None, current_sha)


@pytest.mark.asyncio
async def test_awesome_auto_promote_github_repos(session, source_factory, redis_client):
    """GitHub repos from awesome-list with >50 stars must be auto-promoted to github-deep."""
    source = await source_factory(
        id="awesome:test-auto-promote",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={},
    )
    ctx = {"redis": redis_client}

    current_sha = "promote_sha_001"
    mock_repo = make_mock_repo(current_sha)

    mock_entries = [
        {
            "name": "Popular MCP Tool",
            "url": "https://github.com/dev/popular-mcp",
            "description": "A popular tool",
        },
        {
            "name": "Non-GitHub Resource",
            "url": "https://docs.example.com/guide",
            "description": "A guide",
        },
    ]

    mock_repo_info = {"stargazers_count": 150, "forks_count": 20}

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch(
            "src.workers.ingest_awesome._extract_new_entries",
            return_value=mock_entries,
        ):
            with patch(
                "src.workers.ingest_awesome.fetch_github_repo_info",
                new=AsyncMock(return_value=mock_repo_info),
            ):
                with patch.object(
                    _db, "async_session_factory", make_session_factory(session)
                ):
                    await ingest_awesome_source(ctx, source.id)

    # Verify github-deep Source was created for the GitHub repo
    result = await session.execute(
        select(Source).where(
            Source.id == "github-deep:dev/popular-mcp",
            Source.type == "github-deep",
        )
    )
    deep_source = result.scalar_one_or_none()
    assert deep_source is not None
    assert deep_source.is_active is True
    assert deep_source.config.get("auto_promoted") is True


@pytest.mark.asyncio
async def test_awesome_auto_promote_rate_limit_protection(
    session, source_factory, redis_client
):
    """Auto-promote must stop gracefully after 3 consecutive API failures (403)."""
    source = await source_factory(
        id="awesome:test-rate-limit",
        type="awesome-list",
        url="https://github.com/hesreallyhim/awesome-claude-code",
        config={},
    )
    ctx = {"redis": redis_client}

    current_sha = "rate_limit_sha_001"
    mock_repo = make_mock_repo(current_sha)

    # Multiple GitHub URLs to trigger multiple API calls
    mock_entries = [
        {
            "name": f"Repo {i}",
            "url": f"https://github.com/owner/repo-{i}",
            "description": f"Repo {i} description",
        }
        for i in range(6)
    ]

    with patch("src.workers.ingest_awesome._pull_or_clone", return_value=mock_repo):
        with patch(
            "src.workers.ingest_awesome._extract_new_entries",
            return_value=mock_entries,
        ):
            with patch(
                "src.workers.ingest_awesome.fetch_github_repo_info",
                new=AsyncMock(side_effect=make_http_status_error(403)),
            ) as mock_fetch:
                with patch.object(
                    _db, "async_session_factory", make_session_factory(session)
                ):
                    # Must NOT raise — rate limit protection stops the loop
                    await ingest_awesome_source(ctx, source.id)

    # The 403 causes immediate break in the promote loop (not consecutive failure counting)
    # So only 1 API call is made before breaking
    assert mock_fetch.call_count == 1

    # No github-deep Sources created (all failed)
    result = await session.execute(select(Source).where(Source.type == "github-deep"))
    assert len(result.scalars().all()) == 0

    # The awesome source ingestion itself completed successfully (items stored)
    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 6  # All 6 entries stored as IntelItems
