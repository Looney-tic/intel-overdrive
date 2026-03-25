"""
EXT-09: Feed autodiscovery service unit tests.

Tests for src/services/feed_autodiscovery.py:
- detect_feed_type returns FeedType.RSS for RSS XML content
- detect_feed_type returns FeedType.ATOM for Atom XML content
- detect_feed_type returns FeedType.JSON_FEED for JSON Feed content
- detect_feed_type returns FeedType.UNKNOWN for plain HTML

Mocking strategy:
- Patch httpx.AsyncClient to return mock responses with known content
- feedparser.parse called on bytes content (no network required)
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.feed_autodiscovery import FeedType, detect_feed_type


# ---------------------------------------------------------------------------
# Sample feed content fixtures
# ---------------------------------------------------------------------------

RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Anthropic Blog</title>
    <link>https://anthropic.com/blog</link>
    <description>News from Anthropic</description>
    <item>
      <title>Claude 3.5 Sonnet release</title>
      <link>https://anthropic.com/blog/claude-3-5-sonnet</link>
    </item>
  </channel>
</rss>"""

ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>GitHub Blog</title>
  <link href="https://github.blog"/>
  <entry>
    <title>New GitHub features</title>
    <link href="https://github.blog/new-features"/>
    <id>https://github.blog/new-features</id>
  </entry>
</feed>"""

JSON_FEED_CONTENT = b"""{
  "version": "https://jsonfeed.org/version/1.1",
  "title": "AI Weekly",
  "home_page_url": "https://aiweekly.co",
  "items": [
    {
      "id": "1",
      "url": "https://aiweekly.co/issue-1",
      "title": "AI Weekly Issue 1"
    }
  ]
}"""

PLAIN_HTML = b"""<html>
<head><title>Example Blog</title></head>
<body>
<p>This is just a regular HTML page, not a feed.</p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Helper to build mock httpx response
# ---------------------------------------------------------------------------


def _make_mock_response(content: bytes, content_type: str = "text/xml") -> MagicMock:
    """Build a mock httpx Response with given bytes content and content-type."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.headers = {"Content-Type": content_type}
    mock.content = content
    return mock


def _make_async_client(response: MagicMock) -> AsyncMock:
    """Wrap mock response in an async context manager mock client."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=response)
    return mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_rss_feed():
    """RSS 2.0 XML content → FeedType.RSS."""
    response = _make_mock_response(RSS_XML, "text/xml")
    client = _make_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        feed_type = await detect_feed_type("https://anthropic.com/blog/feed.xml")

    assert feed_type == FeedType.RSS


@pytest.mark.asyncio
async def test_detect_atom_feed():
    """Atom XML content → FeedType.ATOM."""
    response = _make_mock_response(ATOM_XML, "application/atom+xml")
    client = _make_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        feed_type = await detect_feed_type("https://github.blog/feed.xml")

    assert feed_type == FeedType.ATOM


@pytest.mark.asyncio
async def test_detect_json_feed():
    """JSON Feed content with jsonfeed version key → FeedType.JSON_FEED."""
    response = _make_mock_response(JSON_FEED_CONTENT, "application/json")
    client = _make_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        feed_type = await detect_feed_type("https://aiweekly.co/feed.json")

    assert feed_type == FeedType.JSON_FEED


@pytest.mark.asyncio
async def test_detect_json_feed_via_content_type():
    """Content-Type 'application/feed+json' → FeedType.JSON_FEED (header wins)."""
    response = _make_mock_response(JSON_FEED_CONTENT, "application/feed+json")
    client = _make_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        feed_type = await detect_feed_type("https://example.com/feed.json")

    assert feed_type == FeedType.JSON_FEED


@pytest.mark.asyncio
async def test_detect_unknown():
    """Plain HTML page with no feed structure → FeedType.UNKNOWN."""
    response = _make_mock_response(PLAIN_HTML, "text/html")
    client = _make_async_client(response)

    with patch("httpx.AsyncClient", return_value=client):
        feed_type = await detect_feed_type("https://example.com/blog")

    assert feed_type == FeedType.UNKNOWN
