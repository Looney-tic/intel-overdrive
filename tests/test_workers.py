"""
INGEST-01: Worker settings configuration tests.

Validates fast/slow queue configuration, function registration,
cron job setup, and queue independence.

Extended in Phase 7 to verify all 8 new adapters are registered,
plus circuit breaker isolation across adapter types.

Extended in Phase 12 to verify all 5 new Phase 12 adapters are registered
(PyPI, VS Code Marketplace, Bluesky, Sitemap, GitHub Discussions).
"""
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.settings import WorkerSettings, SlowWorkerSettings
from src.workers.ingest_rss import ingest_rss_source
from src.workers.ingest_github import ingest_github_source
from src.workers.ingest_hn import ingest_hn_source, poll_hn_sources
from src.workers.ingest_reddit import ingest_reddit_source, poll_reddit_sources
from src.workers.ingest_youtube import ingest_youtube_source, poll_youtube_sources
from src.workers.ingest_gh_releases import (
    ingest_gh_releases_source,
    poll_gh_releases_sources,
)
from src.workers.ingest_npm import ingest_npm_source, poll_npm_sources
from src.workers.ingest_mcp_registry import (
    ingest_mcp_registry_source,
    poll_mcp_registry_sources,
)
from src.workers.ingest_awesome import ingest_awesome_source, poll_awesome_sources
from src.workers.ingest_releasebot import (
    ingest_releasebot_source,
    poll_releasebot_sources,
)


def test_fast_queue_config():
    """Fast queue must be named 'fast' with max_jobs=50."""
    assert WorkerSettings.queue_name == "fast"
    assert WorkerSettings.max_jobs == 50


def test_slow_queue_config():
    """Slow queue must be named 'slow' with max_jobs=5 (LLM-bound tasks)."""
    assert SlowWorkerSettings.queue_name == "slow"
    assert SlowWorkerSettings.max_jobs == 5


def test_fast_queue_has_all_functions():
    """Fast queue must register all 18 ingest workers as callable functions."""
    function_names = [f.__name__ for f in WorkerSettings.functions]
    # Original adapters
    assert "ingest_rss_source" in function_names
    assert "ingest_github_source" in function_names
    # Phase 7 adapters
    assert "ingest_hn_source" in function_names
    assert "ingest_reddit_source" in function_names
    assert "ingest_youtube_source" in function_names
    assert "ingest_gh_releases_source" in function_names
    assert "ingest_npm_source" in function_names
    assert "ingest_mcp_registry_source" in function_names
    assert "ingest_awesome_source" in function_names
    assert "ingest_releasebot_source" in function_names
    # Phase 11 adapters
    assert "ingest_arxiv_source" in function_names
    assert "ingest_github_deep_source" in function_names
    assert "ingest_scraper_source" in function_names
    # Phase 12 adapters
    assert "ingest_github_discussions_source" in function_names
    assert "ingest_pypi_source" in function_names
    assert "ingest_vscode_source" in function_names
    assert "ingest_bluesky_source" in function_names
    assert "ingest_sitemap_source" in function_names


def test_fast_queue_has_all_cron_jobs():
    """Fast queue must have at least 19 cron jobs (grows with new adapters)."""
    assert len(WorkerSettings.cron_jobs) >= 19


def test_fast_queue_cron_jobs_include_phase7_dispatchers():
    """All Phase 7 poll dispatchers must be registered as cron jobs."""
    # Extract the coroutine function objects from cron job specs
    cron_fn_names = set()
    for cron_job in WorkerSettings.cron_jobs:
        # arq cron objects expose the function via .coroutine attribute
        fn = getattr(cron_job, "coroutine", None)
        if fn is not None:
            cron_fn_names.add(fn.__name__)

    assert "poll_hn_sources" in cron_fn_names
    assert "poll_reddit_sources" in cron_fn_names
    assert "poll_youtube_sources" in cron_fn_names
    assert "poll_gh_releases_sources" in cron_fn_names
    assert "poll_npm_sources" in cron_fn_names
    assert "poll_mcp_registry_sources" in cron_fn_names
    assert "poll_awesome_sources" in cron_fn_names
    assert "poll_releasebot_sources" in cron_fn_names


def test_fast_queue_cron_jobs_include_phase12_dispatchers():
    """All Phase 12 poll dispatchers must be registered as cron jobs."""
    cron_fn_names = set()
    for cron_job in WorkerSettings.cron_jobs:
        fn = getattr(cron_job, "coroutine", None)
        if fn is not None:
            cron_fn_names.add(fn.__name__)

    assert "poll_github_discussions_sources" in cron_fn_names
    assert "poll_pypi_sources" in cron_fn_names
    assert "poll_vscode_sources" in cron_fn_names
    assert "poll_bluesky_sources" in cron_fn_names
    assert "poll_sitemap_sources" in cron_fn_names


def test_fast_queue_function_count():
    """Fast queue must have at least 19 functions registered (grows with new adapters)."""
    assert len(WorkerSettings.functions) >= 19


def test_fast_queue_cron_count():
    """Fast queue must have at least 19 cron jobs registered (grows with new adapters)."""
    assert len(WorkerSettings.cron_jobs) >= 19


def test_queues_are_independent():
    """Fast and slow queues must use different names — they run as separate processes."""
    assert WorkerSettings.queue_name != SlowWorkerSettings.queue_name


def test_fast_functions_are_callable():
    """Functions registered in fast queue must be callable coroutines."""
    import asyncio

    for fn in WorkerSettings.functions:
        assert callable(fn), f"{fn} is not callable"
        assert asyncio.iscoroutinefunction(fn), f"{fn} is not a coroutine function"


def test_slow_queue_has_empty_functions_placeholder():
    """Slow queue functions list exists (Phase 3 will populate it)."""
    assert hasattr(SlowWorkerSettings, "functions")
    assert isinstance(SlowWorkerSettings.functions, list)


def test_cron_minute_offsets_are_unique():
    """No two cron dispatchers in the fast queue should share the same minute values.

    Staggered offsets prevent thundering herd on the DB and Redis.
    """
    all_minutes: set[int] = set()
    for cron_job in WorkerSettings.cron_jobs:
        # arq cron objects expose minute via .kwargs or direct attributes
        minute_val = getattr(cron_job, "minute", None)
        if minute_val is None:
            continue
        if isinstance(minute_val, (set, frozenset)):
            for m in minute_val:
                assert (
                    m not in all_minutes
                ), f"Duplicate cron minute {m} across multiple dispatchers"
                all_minutes.add(m)
        else:
            assert (
                minute_val not in all_minutes
            ), f"Duplicate cron minute {minute_val} across multiple dispatchers"
            all_minutes.add(minute_val)


# ---------------------------------------------------------------------------
# Circuit breaker isolation test
# ---------------------------------------------------------------------------


def _make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


SAMPLE_HN_RESPONSE_ISOLATION = {
    "hits": [
        {
            "objectID": "99990001",
            "title": "Isolation Test Story",
            "url": "https://example.com/isolation-test",
            "created_at_i": 1710099000,
            "points": 10,
            "num_comments": 1,
            "story_text": None,
        },
    ]
}


@pytest.mark.asyncio
async def test_circuit_breaker_isolation_across_adapters(
    session, source_factory, redis_client
):
    """A failure in one adapter must not affect another adapter's source state.

    This proves the ROADMAP success criterion: 'A source adapter failure
    triggers the circuit breaker for that source only; all other sources
    continue polling.'

    In production, adapters run as separate ARQ jobs with separate DB sessions.
    Isolation is guaranteed because consecutive_errors is per-Source row.
    This test verifies that handle_source_error on one source increments only
    that source's error counter — the other source remains at zero errors.
    """
    from src.services.source_health import handle_source_error

    # Create two sources of different types
    hn_source = await source_factory(
        id="hn:isolation-test",
        type="hn",
        url="https://hn.algolia.com/api/v1/search_by_date",
        config={"query": "claude code", "last_poll_ts": 0},
    )
    reddit_source = await source_factory(
        id="reddit:isolation-test",
        type="reddit",
        url="https://www.reddit.com/r/ClaudeAI/new/.rss",
        config={},
    )

    # Both start at zero errors
    assert hn_source.consecutive_errors == 0
    assert reddit_source.consecutive_errors == 0

    # Simulate Reddit adapter failure via handle_source_error
    await handle_source_error(session, reddit_source, Exception("Reddit 503"))

    # Reload both sources from DB to verify isolation
    result = await session.execute(
        select(Source)
        .where(Source.id == reddit_source.id)
        .execution_options(populate_existing=True)
    )
    reddit_refreshed = result.scalar_one()
    assert reddit_refreshed.consecutive_errors >= 1

    result = await session.execute(
        select(Source)
        .where(Source.id == hn_source.id)
        .execution_options(populate_existing=True)
    )
    hn_refreshed = result.scalar_one()
    assert hn_refreshed.consecutive_errors == 0
    assert hn_refreshed.is_active is True
