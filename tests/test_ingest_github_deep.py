"""
ADAPT-02 + EXT-01: Deep GitHub repository analysis worker tests.

Tests for ingest_github_deep_source covering:
- Star milestone crossed: last=900, current=1100, milestone=[1000] -> 1 IntelItem
- Star milestone not crossed: last=1100, current=1150 -> 0 IntelItems
- Multiple milestones crossed in one run
- Commit burst detected when last_week_commits > threshold and > 2x prior week
- Commit burst below threshold: no IntelItem
- Fragment URL uniqueness (#star-milestone-N in URLs)
- Config state updated after processing (last_star_count, last_commit_week_total)
- First run (last_readme_hash=="") does not create description-change event
- poll_github_deep_sources dispatches for github-deep type sources
- fetch_github_repo_stats returning None (202 not yet resolved): no burst, no error

CHANGELOG diffing tests (EXT-01 / Phase 12-01):
- SHA change on watched file: creates 1 IntelItem with changelog tag
- First run (file_hashes empty): skips IntelItem creation (no noise on setup)
- Watched file returning None (404): skipped gracefully, no items, no error

Mocking strategy:
- Patch src.workers.ingest_github_deep.fetch_github_repo_info with AsyncMock
- Patch src.workers.ingest_github_deep.fetch_github_repo_stats with AsyncMock
- Patch src.workers.ingest_github_deep.fetch_github_file_contents with AsyncMock
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import base64
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_github_deep import (
    ingest_github_deep_source,
    poll_github_deep_sources,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def _make_repo_info(stars: int = 1000, description: str = "A test repo") -> dict:
    return {
        "stargazers_count": stars,
        "description": description,
        "full_name": "testowner/testrepo",
        "html_url": "https://github.com/testowner/testrepo",
    }


def _make_participation(last_week_commits: int = 0) -> dict:
    """Return a mock participation response with all[] having last_week_commits at [-1]."""
    all_weeks = [0] * 51 + [last_week_commits]
    return {"all": all_weeks, "owner": [0] * 52}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_star_milestone_crossed(session, source_factory, redis_client):
    """Crossing a star milestone creates 1 IntelItem with 'reached N stars' in title."""
    source = await source_factory(
        id="github-deep:test/star-crossed",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [1000],
            "commit_burst_threshold": 20,
            "last_star_count": 900,
            "last_commit_week_total": 0,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1100)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=5)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    # 1 milestone event: 1000 stars
    milestone_items = [i for i in items if "1,000" in i.title or "1000" in i.title]
    assert len(milestone_items) == 1
    assert "1,000" in milestone_items[0].title


@pytest.mark.asyncio
async def test_star_milestone_not_crossed(session, source_factory, redis_client):
    """When current stars don't cross any milestone, no IntelItem is created for stars."""
    source = await source_factory(
        id="github-deep:test/star-not-crossed",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [1000, 5000],
            "commit_burst_threshold": 20,
            "last_star_count": 1100,
            "last_commit_week_total": 0,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1150)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=5)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # No items: not crossing any milestone; commit burst below threshold
    assert len(items) == 0


@pytest.mark.asyncio
async def test_multiple_milestones_crossed(session, source_factory, redis_client):
    """Jumping from 400 to 5500 stars crosses 3 milestones (500, 1000, 5000)."""
    source = await source_factory(
        id="github-deep:test/multi-milestone",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [500, 1000, 5000],
            "commit_burst_threshold": 20,
            "last_star_count": 400,
            "last_commit_week_total": 0,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=5500)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=5)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # 3 milestone events
    assert len(items) == 3


@pytest.mark.asyncio
async def test_commit_burst_detected(session, source_factory, redis_client):
    """Commit burst: last_week=25 > threshold=20 and > 2x prior (10) creates 1 IntelItem."""
    source = await source_factory(
        id="github-deep:test/commit-burst",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [1000],
            "commit_burst_threshold": 20,
            "last_star_count": 1200,  # above milestone, won't cross again
            "last_commit_week_total": 10,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1250)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=25)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    burst_items = [
        i for i in items if "commit burst" in i.title.lower() or "commit_burst" in i.url
    ]
    assert len(burst_items) == 1


@pytest.mark.asyncio
async def test_commit_burst_below_threshold(session, source_factory, redis_client):
    """Commits at 15, threshold=20: no commit burst IntelItem created."""
    source = await source_factory(
        id="github-deep:test/burst-below",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [1000],
            "commit_burst_threshold": 20,
            "last_star_count": 1200,
            "last_commit_week_total": 5,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1250)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=15)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0


@pytest.mark.asyncio
async def test_fragment_url_uniqueness(session, source_factory, redis_client):
    """Star milestone events must have fragment identifiers (#star-milestone-N) in URL."""
    source = await source_factory(
        id="github-deep:test/fragment-url",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [1000],
            "commit_burst_threshold": 20,
            "last_star_count": 900,
            "last_commit_week_total": 0,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1100)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=5)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 1
    assert "#star-milestone-1000" in items[0].url


@pytest.mark.asyncio
async def test_config_state_updated(session, source_factory, redis_client):
    """After a successful run, source.config must have updated last_star_count."""
    source = await source_factory(
        id="github-deep:test/config-update",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [1000],
            "commit_burst_threshold": 20,
            "last_star_count": 500,
            "last_commit_week_total": 0,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=750)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=3)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    src_result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = src_result.scalar_one()
    assert refreshed.config["last_star_count"] == 750
    assert "last_commit_week_total" in refreshed.config
    assert refreshed.config["last_commit_week_total"] == 3


@pytest.mark.asyncio
async def test_first_run_no_readme_event(session, source_factory, redis_client):
    """First run (last_readme_hash=="") must not create a description-change IntelItem."""
    source = await source_factory(
        id="github-deep:test/first-run",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [10000],  # far above current stars — won't trigger
            "commit_burst_threshold": 20,
            "last_star_count": 1000,
            "last_commit_week_total": 0,
            "last_readme_hash": "",  # first run — no prior hash
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(
            return_value=_make_repo_info(stars=1050, description="A new description")
        ),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=5)),
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # No milestones crossed, no burst, no description change (first run protection)
    assert len(items) == 0


@pytest.mark.asyncio
async def test_poll_github_deep_dispatches(session, source_factory, redis_client):
    """poll_github_deep_sources must enqueue a job for each github-deep source."""
    source = await source_factory(
        id="github-deep:test/poll",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={},
    )
    ctx = {"redis": redis_client}

    mock_enqueue = AsyncMock()
    redis_client.enqueue_job = mock_enqueue

    with patch.object(_db, "async_session_factory", make_session_factory(session)):
        await poll_github_deep_sources(ctx)

    mock_enqueue.assert_called_once_with(
        "ingest_github_deep_source", source.id, _queue_name="fast"
    )


@pytest.mark.asyncio
async def test_stats_202_returns_none(session, source_factory, redis_client):
    """When fetch_github_repo_stats returns None (202 not resolved), no burst, no error."""
    source = await source_factory(
        id="github-deep:test/stats-none",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [10000],  # far above current stars
            "commit_burst_threshold": 20,
            "last_star_count": 1000,
            "last_commit_week_total": 0,
            "last_readme_hash": "",
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1050)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=None),  # 202 not yet computed
        ):
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                # Must not raise
                await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # No burst items, no error
    assert len(items) == 0


# ---------------------------------------------------------------------------
# CHANGELOG diffing tests (EXT-01 / Phase 12-01)
# ---------------------------------------------------------------------------


def _make_file_data(
    sha: str = "abc123def456", content_text: str = "## v1.0.0\n- Initial release\n"
) -> dict:
    """Return a mock fetch_github_file_contents response dict."""
    encoded = base64.b64encode(content_text.encode("utf-8")).decode("utf-8")
    return {
        "sha": sha,
        "content": encoded,
        "encoding": "base64",
        "name": "CHANGELOG.md",
        "path": "CHANGELOG.md",
    }


@pytest.mark.asyncio
async def test_changelog_sha_change_creates_item(session, source_factory, redis_client):
    """When CHANGELOG.md SHA changes vs stored hash, one IntelItem with 'changelog' tag is created."""
    source = await source_factory(
        id="github-deep:test/changelog-sha-change",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [100000],  # far above current stars — won't trigger
            "commit_burst_threshold": 100,  # far above any test commit count
            "last_star_count": 1000,
            "last_commit_week_total": 0,
            "last_readme_hash": "existing-hash",
            "watched_files": ["CHANGELOG.md"],
            "file_hashes": {"CHANGELOG.md": "old-sha-111"},  # non-empty → not first run
        },
    )
    ctx = {"redis": redis_client}

    # New SHA differs from stored "old-sha-111"
    new_file_data = _make_file_data(
        sha="new-sha-222", content_text="## v2.0.0\n- Breaking change!\n"
    )

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1010)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=2)),
        ):
            with patch(
                "src.workers.ingest_github_deep.fetch_github_file_contents",
                new=AsyncMock(return_value=new_file_data),
            ):
                with patch.object(
                    _db, "async_session_factory", make_session_factory(session)
                ):
                    await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()

    changelog_items = [i for i in items if "changelog" in i.tags]
    assert len(changelog_items) == 1
    assert "changelog" in changelog_items[0].tags
    assert "CHANGELOG.md" in changelog_items[0].url


@pytest.mark.asyncio
async def test_changelog_first_run_skips(session, source_factory, redis_client):
    """On first run (file_hashes is empty), no IntelItem created for CHANGELOG."""
    source = await source_factory(
        id="github-deep:test/changelog-first-run",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [100000],
            "commit_burst_threshold": 100,
            "last_star_count": 1000,
            "last_commit_week_total": 0,
            "last_readme_hash": "",
            "watched_files": ["CHANGELOG.md"],
            "file_hashes": {},  # empty → is_first_file_run = True
        },
    )
    ctx = {"redis": redis_client}

    file_data = _make_file_data(sha="some-sha-333")

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1010)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=2)),
        ):
            with patch(
                "src.workers.ingest_github_deep.fetch_github_file_contents",
                new=AsyncMock(return_value=file_data),
            ):
                with patch.object(
                    _db, "async_session_factory", make_session_factory(session)
                ):
                    await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # First run: SHA recorded but no IntelItem created
    changelog_items = [i for i in items if "changelog" in (i.tags or [])]
    assert len(changelog_items) == 0

    # Verify file_hashes was populated with current SHA
    src_result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = src_result.scalar_one()
    assert refreshed.config.get("file_hashes", {}).get("CHANGELOG.md") == "some-sha-333"


@pytest.mark.asyncio
async def test_changelog_404_skipped(session, source_factory, redis_client):
    """When fetch_github_file_contents returns None (file not found), no items, no error."""
    source = await source_factory(
        id="github-deep:test/changelog-404",
        type="github-deep",
        url="https://github.com/testowner/testrepo",
        config={
            "star_milestones": [100000],
            "commit_burst_threshold": 100,
            "last_star_count": 1000,
            "last_commit_week_total": 0,
            "last_readme_hash": "existing-hash",
            "watched_files": ["CHANGELOG.md"],
            "file_hashes": {"CHANGELOG.md": "old-sha-444"},
        },
    )
    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_github_deep.fetch_github_repo_info",
        new=AsyncMock(return_value=_make_repo_info(stars=1010)),
    ):
        with patch(
            "src.workers.ingest_github_deep.fetch_github_repo_stats",
            new=AsyncMock(return_value=_make_participation(last_week_commits=2)),
        ):
            with patch(
                "src.workers.ingest_github_deep.fetch_github_file_contents",
                new=AsyncMock(return_value=None),  # 404 → returns None
            ):
                with patch.object(
                    _db, "async_session_factory", make_session_factory(session)
                ):
                    # Must not raise
                    await ingest_github_deep_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    changelog_items = [i for i in items if "changelog" in (i.tags or [])]
    assert len(changelog_items) == 0
