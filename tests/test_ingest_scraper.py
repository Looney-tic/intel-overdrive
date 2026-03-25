"""
ADAPT-03: Playwright web scraper adapter tests.

Tests for ingest_scraper_source covering:
- Extracts items from mocked Playwright page query_selector_all
- Skips URLs already in seen_urls (config-level dedup)
- seen_urls capped at 100 entries after processing
- DB-level dedup: skips items already in database
- No items found treated as success (0 items, no error raised)
- poll_scraper_sources dispatches for scraper type sources
- Browser cleanup: browser.close() called even when page.goto raises

Mocking strategy:
- Mock async_playwright, browser, page, element_handle using AsyncMock/MagicMock
- Patch playwright.async_api.async_playwright (not ingest_scraper.async_playwright)
  because ingest_scraper imports directly: from playwright.async_api import async_playwright
- Patch src.core.init_db.async_session_factory with test session factory
- ctx dict simulates ARQ context: {"redis": redis_client}
"""
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_scraper import ingest_scraper_source, poll_scraper_sources


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def _make_element(title_text: str, href: str, excerpt_text: str = "") -> AsyncMock:
    """Create a mock Playwright ElementHandle for a single item."""
    el = AsyncMock()

    title_el = AsyncMock()
    title_el.inner_text = AsyncMock(return_value=title_text)

    url_el = AsyncMock()
    url_el.get_attribute = AsyncMock(return_value=href)

    excerpt_el = AsyncMock()
    excerpt_el.inner_text = AsyncMock(return_value=excerpt_text)

    async def _query_selector(selector: str):
        if "h2" in selector or "h3" in selector or "title" in selector.lower():
            return title_el
        if "a" in selector:
            return url_el
        if "p" in selector or "excerpt" in selector.lower():
            return excerpt_el if excerpt_text else None
        return None

    el.query_selector = _query_selector
    return el


def _make_playwright_ctx(elements=None, goto_raises=None):
    """Build a mock async_playwright context manager and browser/page hierarchy."""
    page = AsyncMock()
    browser = AsyncMock()
    chromium = AsyncMock()
    playwright_obj = MagicMock()

    # page.query_selector_all returns provided elements (default empty list)
    page.query_selector_all = AsyncMock(return_value=elements or [])
    page.goto = AsyncMock(side_effect=goto_raises) if goto_raises else AsyncMock()
    page.route = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    page.wait_for_selector = AsyncMock()

    browser.new_page = AsyncMock(return_value=page)
    browser.close = AsyncMock()
    chromium.launch = AsyncMock(return_value=browser)
    playwright_obj.chromium = chromium

    # Build async context manager for async_playwright()
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=playwright_obj)
    cm.__aexit__ = AsyncMock(return_value=False)

    return cm, browser, page


SCRAPER_SELECTORS = {
    "item": "article",
    "title": "h2, h3",
    "url": "a",
    "excerpt": "p",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_scraper_extracts_items(session, source_factory, redis_client):
    """Mock Playwright with 2 elements; verify 2 IntelItems created."""
    source = await source_factory(
        id="scraper:test-extract",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": [],
        },
    )
    ctx = {"redis": redis_client}

    elements = [
        _make_element("Article One", "/blog/one", "Excerpt for one"),
        _make_element("Article Two", "/blog/two", "Excerpt for two"),
    ]
    pw_cm, browser, page = _make_playwright_ctx(elements=elements)

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 2
    titles = {i.title for i in items}
    assert "Article One" in titles
    assert "Article Two" in titles


@pytest.mark.asyncio
async def test_ingest_scraper_seen_urls_skip(session, source_factory, redis_client):
    """URL already in seen_urls must be skipped — no IntelItem created."""
    seen_url = "https://example.com/blog/one"
    source = await source_factory(
        id="scraper:test-seen-skip",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": [seen_url],
        },
    )
    ctx = {"redis": redis_client}

    elements = [_make_element("Article One", "/blog/one", "Excerpt")]
    pw_cm, browser, page = _make_playwright_ctx(elements=elements)

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0


@pytest.mark.asyncio
async def test_ingest_scraper_seen_urls_capped(session, source_factory, redis_client):
    """Starting with 99 seen_urls, finding 5 new: seen_urls capped at 100 entries."""
    # Start with 99 pre-existing seen URLs
    existing_seen = [f"https://example.com/old-{i}" for i in range(99)]
    source = await source_factory(
        id="scraper:test-seen-cap",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": existing_seen,
        },
    )
    ctx = {"redis": redis_client}

    # 5 new elements with fresh URLs
    elements = [_make_element(f"New {i}", f"/blog/new-{i}") for i in range(5)]
    pw_cm, browser, page = _make_playwright_ctx(elements=elements)

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    src_result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = src_result.scalar_one()
    # seen_urls must be capped at 100
    assert len(refreshed.config["seen_urls"]) <= 100


@pytest.mark.asyncio
async def test_ingest_scraper_dedup_db(session, source_factory, redis_client):
    """URL already in DB (DedupService) must be skipped even if not in seen_urls."""
    article_url = "https://example.com/blog/already-in-db"
    source = await source_factory(
        id="scraper:test-dedup-db",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": [],
        },
    )

    # Pre-insert the item in the DB
    existing = IntelItem(
        source_id=source.id,
        external_id=article_url,
        url=article_url,
        url_hash=hashlib.sha256(article_url.encode()).hexdigest(),
        title="Already In DB",
        content="Existing content",
        primary_type="unknown",
        tags=[],
        status="raw",
        content_hash=hashlib.sha256(b"Existing content").hexdigest(),
    )
    session.add(existing)
    await session.commit()

    ctx = {"redis": redis_client}

    elements = [_make_element("Already In DB", "/blog/already-in-db")]
    pw_cm, browser, page = _make_playwright_ctx(elements=elements)

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Still just the 1 pre-existing item (not re-inserted)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_ingest_scraper_no_items_found(session, source_factory, redis_client):
    """Selectors matching nothing is treated as success (no error raised)."""
    source = await source_factory(
        id="scraper:test-no-items",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": [],
        },
    )
    ctx = {"redis": redis_client}

    pw_cm, browser, page = _make_playwright_ctx(elements=[])

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            # Must NOT raise even though no items found
            await ingest_scraper_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 0

    # Source should still be updated as successful
    src_result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = src_result.scalar_one()
    assert refreshed.last_successful_poll is not None


@pytest.mark.asyncio
async def test_poll_scraper_sources_dispatches(session, source_factory, redis_client):
    """poll_scraper_sources must enqueue a job for each active scraper source."""
    source = await source_factory(
        id="scraper:test-poll",
        type="scraper",
        url="https://example.com/blog",
        config={"selectors": SCRAPER_SELECTORS, "seen_urls": []},
    )
    ctx = {"redis": redis_client}

    mock_enqueue = AsyncMock()
    redis_client.enqueue_job = mock_enqueue

    with patch.object(_db, "async_session_factory", make_session_factory(session)):
        await poll_scraper_sources(ctx)

    mock_enqueue.assert_called_once_with(
        "ingest_scraper_source", source.id, _queue_name="fast", _defer_by=0
    )


@pytest.mark.asyncio
async def test_ingest_scraper_browser_cleanup(session, source_factory, redis_client):
    """Even when page.goto raises, the exception propagates and session is handled."""
    source = await source_factory(
        id="scraper:test-cleanup",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": [],
        },
    )
    ctx = {"redis": redis_client}

    pw_cm, browser, page = _make_playwright_ctx(
        goto_raises=RuntimeError("connection refused")
    )

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            with pytest.raises(RuntimeError, match="connection refused"):
                await ingest_scraper_source(ctx, source.id)

    # browser.close() must have been called in the exception handler
    browser.close.assert_called_once()


@pytest.mark.asyncio
async def test_ingest_scraper_semaphore_blocks_at_max(
    session, source_factory, redis_client
):
    """Redis semaphore: when slot count exceeds max (2), job returns early without scraping."""
    from src.workers.ingest_scraper import SCRAPER_SEMAPHORE_KEY, SCRAPER_SEMAPHORE_MAX

    source = await source_factory(
        id="scraper:test-semaphore",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": [],
        },
    )
    # Mock enqueue_job so the semaphore-retry re-enqueue path doesn't fail
    # (redis_client is a real Redis client, not an ARQ client, so enqueue_job doesn't exist)
    redis_client.enqueue_job = AsyncMock()
    ctx = {"redis": redis_client}

    # Pre-fill the semaphore to max capacity — simulates SCRAPER_SEMAPHORE_MAX concurrent jobs
    await redis_client.set(SCRAPER_SEMAPHORE_KEY, str(SCRAPER_SEMAPHORE_MAX))

    elements = [_make_element("Should Not Be Scraped", "/blog/blocked")]
    pw_cm, browser, page = _make_playwright_ctx(elements=elements)

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    # No IntelItems should have been created (semaphore blocked the job)
    from sqlalchemy import select

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert (
        len(items) == 0
    ), "Semaphore should have blocked scraping when at max capacity"

    # Semaphore should be back at SCRAPER_SEMAPHORE_MAX (decr'd back after rejection)
    slot_count = int(await redis_client.get(SCRAPER_SEMAPHORE_KEY) or 0)
    assert (
        slot_count == SCRAPER_SEMAPHORE_MAX
    ), f"Semaphore should remain at {SCRAPER_SEMAPHORE_MAX} after rejection, got {slot_count}"


@pytest.mark.asyncio
async def test_ingest_scraper_seen_urls_order_preserved(
    session, source_factory, redis_client
):
    """seen_urls dedup preserves insertion order (most recent URLs retained) — M-3 fix."""
    # Start with 95 old URLs
    old_urls = [f"https://example.com/old-{i}" for i in range(95)]
    source = await source_factory(
        id="scraper:test-url-order",
        type="scraper",
        url="https://example.com/blog",
        config={
            "selectors": SCRAPER_SELECTORS,
            "seen_urls": old_urls,
        },
    )
    ctx = {"redis": redis_client}

    # 10 new elements — these are "newer" than the old 95
    new_urls = [f"/blog/new-{i}" for i in range(10)]
    elements = [_make_element(f"New {i}", new_urls[i]) for i in range(10)]
    pw_cm, browser, page = _make_playwright_ctx(elements=elements)

    with patch("src.workers.ingest_scraper.async_playwright", return_value=pw_cm):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    src_result = await session.execute(
        select(Source)
        .where(Source.id == source.id)
        .execution_options(populate_existing=True)
    )
    refreshed = src_result.scalar_one()
    saved_seen = refreshed.config["seen_urls"]

    # Cap at 100
    assert len(saved_seen) <= 100

    # The 10 newly found URLs should all be present (they are the most recent)
    expected_new_urls = [f"https://example.com/blog/new-{i}" for i in range(10)]
    for url in expected_new_urls:
        assert (
            url in saved_seen
        ), f"New URL {url} should be in seen_urls after order-preserving dedup"
