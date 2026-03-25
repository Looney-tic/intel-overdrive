"""
EXT-04: Sitemap adapter unit tests.

Tests for ingest_sitemap_source covering:
- _parse_sitemap_urls extracts page URLs from a regular urlset sitemap
- _parse_sitemap_urls detects child sitemaps from a sitemapindex
- _filter_urls applies path prefix filtering correctly
- _filter_urls passes all URLs when url_filter is None
- _extract_page_content extracts title, excerpt, and body from HTML
- _extract_published_date extracts dates from OG, JSON-LD, and <time> tags
- ingest_sitemap_source handles 304 Not Modified (None content) gracefully

Mocking strategy:
- Call pure functions directly (no DB required for parsing tests)
- Patch fetch_feed_conditional for ingest_sitemap_source integration test
- Patch src.core.init_db.async_session_factory with test session factory
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _db
from sqlalchemy import select

from src.models.models import IntelItem, Source
from src.workers.ingest_sitemap import (
    _parse_sitemap_urls,
    _filter_entries,
    _extract_page_content,
    _extract_published_date,
    ingest_sitemap_source,
)


# ---------------------------------------------------------------------------
# Sample XML fixtures
# ---------------------------------------------------------------------------

URLSET_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/blog/post-1</loc>
    <lastmod>2026-03-01</lastmod>
  </url>
  <url>
    <loc>https://example.com/blog/post-2</loc>
    <lastmod>2026-03-10</lastmod>
  </url>
  <url>
    <loc>https://example.com/about</loc>
  </url>
</urlset>"""

SITEMAPINDEX_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://example.com/sitemap-blog.xml</loc>
  </sitemap>
  <sitemap>
    <loc>https://example.com/sitemap-news.xml</loc>
  </sitemap>
</sitemapindex>"""

SAMPLE_HTML = """<html>
<head>
  <title>How Claude Code Works</title>
  <meta name="description" content="An introduction to Claude Code and its architecture">
  <meta property="article:published_time" content="2026-03-10T14:00:00+00:00">
</head>
<body>
<article>
  <p>Claude Code is an AI coding assistant that integrates with your editor.</p>
</article>
</body>
</html>"""

SAMPLE_HTML_JSONLD = """<html>
<head>
  <title>MCP Architecture Guide</title>
  <script type="application/ld+json">
  {
    "@type": "Article",
    "datePublished": "2026-02-15T10:00:00Z",
    "headline": "MCP Architecture Guide"
  }
  </script>
</head>
<body><main><p>MCP uses a client-server architecture.</p></main></body>
</html>"""

SAMPLE_HTML_TIME_TAG = """<html>
<head><title>Agent Patterns</title></head>
<body>
<article>
  <time datetime="2026-01-20T08:00:00">January 20, 2026</time>
  <p>Building effective AI agents requires good patterns.</p>
</article>
</body>
</html>"""


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


def test_parse_sitemap_urls_urlset():
    """Regular urlset sitemap: page entries extracted, no child sitemaps."""
    page_entries, child_sitemaps = _parse_sitemap_urls(URLSET_XML)

    urls = [url for url, _ in page_entries]
    assert len(page_entries) == 3
    assert "https://example.com/blog/post-1" in urls
    assert "https://example.com/blog/post-2" in urls
    assert "https://example.com/about" in urls
    assert len(child_sitemaps) == 0


def test_parse_sitemap_urls_sitemapindex():
    """Sitemap index: child sitemap URLs extracted, no page entries."""
    page_entries, child_sitemaps = _parse_sitemap_urls(SITEMAPINDEX_XML)

    assert len(page_entries) == 0
    assert len(child_sitemaps) == 2
    assert "https://example.com/sitemap-blog.xml" in child_sitemaps
    assert "https://example.com/sitemap-news.xml" in child_sitemaps


def test_filter_entries_with_pattern():
    """url_filter='/blog/' passes blog URLs and blocks /about/ URLs."""
    entries = [
        ("https://example.com/blog/post-1", None),
        ("https://example.com/blog/post-2", None),
        ("https://example.com/about", None),
        ("https://example.com/contact", None),
    ]
    filtered = _filter_entries(entries, "/blog/")

    urls = [url for url, _ in filtered]
    assert len(filtered) == 2
    assert "https://example.com/blog/post-1" in urls
    assert "https://example.com/blog/post-2" in urls
    assert "https://example.com/about" not in urls


def test_filter_entries_no_pattern():
    """When url_filter is None, all entries pass through unchanged."""
    entries = [
        ("https://example.com/blog/post-1", None),
        ("https://example.com/about", None),
    ]
    filtered = _filter_entries(entries, None)
    assert filtered == entries


def test_filter_entries_multiple_patterns():
    """Comma-separated patterns: entries matching any pattern pass."""
    entries = [
        ("https://example.com/blog/post", None),
        ("https://example.com/news/update", None),
        ("https://example.com/about", None),
    ]
    filtered = _filter_entries(entries, "/blog/,/news/")
    urls = [url for url, _ in filtered]
    assert len(filtered) == 2
    assert "https://example.com/about" not in urls


def test_extract_page_content():
    """HTML parser extracts title, excerpt, and body from sample page."""
    result = _extract_page_content(SAMPLE_HTML)

    assert result["title"] == "How Claude Code Works"
    assert "introduction to Claude Code" in result["excerpt"]
    assert "Claude Code is an AI coding assistant" in result["body"]


def test_extract_published_date_og():
    """OG article:published_time tag yields correct published date."""
    dt = _extract_published_date(SAMPLE_HTML)

    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 10


def test_extract_published_date_jsonld():
    """JSON-LD datePublished yields correct published date."""
    dt = _extract_published_date(SAMPLE_HTML_JSONLD)

    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 2
    assert dt.day == 15


def test_extract_published_date_time_tag():
    """<time datetime="..."> tag yields correct published date."""
    dt = _extract_published_date(SAMPLE_HTML_TIME_TAG)

    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 1
    assert dt.day == 20


@pytest.mark.asyncio
async def test_ingest_sitemap_handles_304(session, source_factory, redis_client):
    """When fetch_feed_conditional returns None content (304), no IntelItems created."""
    source = await source_factory(
        id="sitemap:test-304",
        type="sitemap",
        url="https://example.com/sitemap.xml",
        config={"url_filter": "/blog/"},
    )
    ctx = {"redis": redis_client}

    # fetch_feed_conditional returns (None, None, None) for 304 Not Modified
    with patch(
        "src.workers.ingest_sitemap.fetch_feed_conditional",
        new=AsyncMock(return_value=(None, None, None)),
    ):
        with patch.object(_db, "async_session_factory", make_session_factory(session)):
            await ingest_sitemap_source(ctx, source.id)

    result = await session.execute(
        select(IntelItem).where(IntelItem.source_id == source.id)
    )
    items = result.scalars().all()
    # 304 means nothing new — no items inserted
    assert len(items) == 0
