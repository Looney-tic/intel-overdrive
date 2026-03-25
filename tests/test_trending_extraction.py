"""
REPO-03/04: Trending extraction and post_process dispatch tests.

Tests for _extract_trending_repos and post_process dispatch in ingest_scraper.py:
- Extracts individual repos from trending page as separate IntelItems
- Skips repos with URLs in seen_urls config
- Generic scraper unaffected (no post_process config)
- Tags include programming language

Mocking strategy:
- Patch src.workers.ingest_scraper.async_playwright for Playwright mocks
- Use MagicMock for page, browser, element objects
- Use AsyncMock for async Playwright methods
- Patch src.core.init_db.async_session_factory with test session factory
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_scraper import ingest_scraper_source, _extract_trending_repos


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def make_trending_article(href: str, description: str, stars: str, language: str):
    """Create a mock article.Box-row element for GitHub Trending."""
    article = AsyncMock()

    # h2 > a element with href
    link_el = AsyncMock()
    link_el.get_attribute = AsyncMock(return_value=href)
    article.query_selector = AsyncMock(
        side_effect=lambda sel: {
            "h2 a": link_el,
            "p": _make_text_el(description) if description else None,
            "a.Link--muted.d-inline-block.mr-3": _make_text_el(stars)
            if stars
            else None,
            "a[href$='/stargazers']": None,
            "span.d-inline-block.float-sm-right": None,
            "span[itemprop='programmingLanguage']": _make_text_el(language)
            if language
            else None,
        }.get(sel)
    )

    return article


def _make_text_el(text: str):
    """Create a mock element that returns text via inner_text()."""
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=text)
    return el


def make_mock_playwright_context(page_mock):
    """Create a mock async_playwright() context manager."""

    class MockPlaywright:
        def __init__(self):
            self.chromium = AsyncMock()
            browser = AsyncMock()
            browser.new_page = AsyncMock(return_value=page_mock)
            browser.close = AsyncMock()
            self.chromium.launch = AsyncMock(return_value=browser)

    mock_pw = MockPlaywright()

    @asynccontextmanager
    async def mock_async_playwright():
        yield mock_pw

    return mock_async_playwright


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trending_extracts_individual_repos(
    session, source_factory, redis_client
):
    """3 trending repos must produce 3 separate IntelItems."""
    source = await source_factory(
        id="scraper:trending-extract",
        type="scraper",
        url="https://github.com/trending",
        config={
            "selectors": {"item": "article.Box-row", "title": "h2 a", "url": "h2 a"},
            "post_process": "github_trending",
        },
    )

    articles = [
        make_trending_article(
            "/owner1/repo1", "First repo description", "1,234", "Python"
        ),
        make_trending_article(
            "/owner2/repo2", "Second repo description", "567", "Rust"
        ),
        make_trending_article("/owner3/repo3", "Third repo description", "89", "Go"),
    ]

    page_mock = AsyncMock()
    page_mock.goto = AsyncMock()
    page_mock.route = AsyncMock()
    page_mock.wait_for_load_state = AsyncMock()
    page_mock.query_selector_all = AsyncMock(return_value=articles)

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_scraper.async_playwright",
        new=make_mock_playwright_context(page_mock),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 3

    urls = {item.url for item in items}
    assert "https://github.com/owner1/repo1" in urls
    assert "https://github.com/owner2/repo2" in urls
    assert "https://github.com/owner3/repo3" in urls

    # Verify titles are owner/repo format
    titles = {item.title for item in items}
    assert "owner1/repo1" in titles


@pytest.mark.asyncio
async def test_trending_skips_seen_urls(session, source_factory, redis_client):
    """Repos with URLs in seen_urls config must be skipped."""
    source = await source_factory(
        id="scraper:trending-seen",
        type="scraper",
        url="https://github.com/trending",
        config={
            "selectors": {"item": "article.Box-row", "title": "h2 a", "url": "h2 a"},
            "post_process": "github_trending",
            "seen_urls": ["https://github.com/owner1/repo1"],
        },
    )

    articles = [
        make_trending_article("/owner1/repo1", "Already seen repo", "1,000", "Python"),
        make_trending_article("/owner2/repo2", "New repo", "500", "TypeScript"),
    ]

    page_mock = AsyncMock()
    page_mock.goto = AsyncMock()
    page_mock.route = AsyncMock()
    page_mock.wait_for_load_state = AsyncMock()
    page_mock.query_selector_all = AsyncMock(return_value=articles)

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_scraper.async_playwright",
        new=make_mock_playwright_context(page_mock),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # Only the new repo (owner2/repo2) should be created
    assert len(items) == 1
    assert items[0].url == "https://github.com/owner2/repo2"


@pytest.mark.asyncio
async def test_trending_generic_scraper_unaffected(
    session, source_factory, redis_client
):
    """Non-trending scraper source (no post_process) must use generic extraction, not _extract_trending_repos."""
    source = await source_factory(
        id="scraper:generic-test",
        type="scraper",
        url="https://blog.example.com",
        config={
            "selectors": {"item": "article", "title": "h2", "url": "a"},
        },
    )

    # Mock generic article elements
    article = AsyncMock()
    title_el = AsyncMock()
    title_el.inner_text = AsyncMock(return_value="Blog Post Title")
    url_el = AsyncMock()
    url_el.get_attribute = AsyncMock(return_value="/posts/blog-post")

    article.query_selector = AsyncMock(
        side_effect=lambda sel: {
            "h2": title_el,
            "a": url_el,
            "": None,
        }.get(sel, None)
    )

    page_mock = AsyncMock()
    page_mock.goto = AsyncMock()
    page_mock.route = AsyncMock()
    page_mock.wait_for_load_state = AsyncMock()
    page_mock.query_selector_all = AsyncMock(return_value=[article])

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_scraper.async_playwright",
        new=make_mock_playwright_context(page_mock),
    ):
        with patch(
            "src.workers.ingest_scraper._extract_trending_repos",
            new=AsyncMock(return_value=0),
        ) as mock_trending:
            with patch.object(
                _db, "async_session_factory", make_session_factory(session)
            ):
                await ingest_scraper_source(ctx, source.id)

    # _extract_trending_repos should NOT have been called
    mock_trending.assert_not_called()

    # Generic extraction should have processed the article
    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 1
    assert items[0].title == "Blog Post Title"


@pytest.mark.asyncio
async def test_trending_tags_include_language(session, source_factory, redis_client):
    """Trending repo with programming language must have language tag (lowercase) in tags."""
    source = await source_factory(
        id="scraper:trending-lang",
        type="scraper",
        url="https://github.com/trending",
        config={
            "selectors": {"item": "article.Box-row", "title": "h2 a", "url": "h2 a"},
            "post_process": "github_trending",
        },
    )

    articles = [
        make_trending_article("/dev/pythonlib", "A Python library", "999", "Python"),
    ]

    page_mock = AsyncMock()
    page_mock.goto = AsyncMock()
    page_mock.route = AsyncMock()
    page_mock.wait_for_load_state = AsyncMock()
    page_mock.query_selector_all = AsyncMock(return_value=articles)

    ctx = {"redis": redis_client}

    with patch(
        "src.workers.ingest_scraper.async_playwright",
        new=make_mock_playwright_context(page_mock),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_scraper_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    assert len(items) == 1
    assert "python" in items[0].tags
    assert "github" in items[0].tags
    assert "trending" in items[0].tags
