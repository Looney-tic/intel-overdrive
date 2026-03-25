"""
Unit tests for src/services/feed_fetcher.py.

Tests conditional GET header construction, 304 handling, error propagation,
and GitHub Search API header/param construction.

Mocking strategy:
- Mock httpx.AsyncClient using a custom mock transport to control responses
  without network I/O. This tests the actual function logic (header construction,
  response parsing, raise_for_status) rather than just mocking the function itself.
"""
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from src.services.feed_fetcher import fetch_feed_conditional, fetch_github_search


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client_get(response: httpx.Response):
    """Create a patched httpx.AsyncClient that returns the given response from .get()."""
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _make_response(
    status_code: int,
    content: bytes = b"",
    headers: dict | None = None,
) -> httpx.Response:
    """Build an httpx.Response with the given status, content, and headers."""
    request = httpx.Request("GET", "https://example.com/feed.xml")
    resp = httpx.Response(
        status_code=status_code,
        content=content,
        headers=headers or {},
        request=request,
    )
    return resp


# ===========================================================================
# fetch_feed_conditional tests
# ===========================================================================


@pytest.mark.asyncio
async def test_conditional_get_sends_etag_header():
    """When stored_etag is provided, If-None-Match header must be sent."""
    response = _make_response(
        304, headers={"ETag": '"old-etag"', "Last-Modified": "Mon, 01 Jan 2024"}
    )
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        await fetch_feed_conditional(
            "https://example.com/feed.xml",
            stored_etag='"old-etag"',
        )

    call_kwargs = mock_client.get.call_args
    headers_sent = (
        call_kwargs.kwargs.get("headers") or call_kwargs.args[1]
        if len(call_kwargs.args) > 1
        else call_kwargs.kwargs.get("headers", {})
    )
    assert headers_sent.get("If-None-Match") == '"old-etag"'


@pytest.mark.asyncio
async def test_conditional_get_sends_last_modified_header():
    """When stored_last_modified is provided, If-Modified-Since header must be sent."""
    lm = "Mon, 01 Jan 2024 00:00:00 GMT"
    response = _make_response(304)
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        await fetch_feed_conditional(
            "https://example.com/feed.xml",
            stored_last_modified=lm,
        )

    call_kwargs = mock_client.get.call_args
    headers_sent = call_kwargs.kwargs.get("headers", {})
    assert headers_sent.get("If-Modified-Since") == lm


@pytest.mark.asyncio
async def test_conditional_get_304_returns_none_content():
    """304 Not Modified must return (None, stored_etag, stored_last_modified)."""
    response = _make_response(304)
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        content, etag, lm = await fetch_feed_conditional(
            "https://example.com/feed.xml",
            stored_etag='"v1"',
            stored_last_modified="Mon, 01 Jan 2024",
        )

    assert content is None
    assert etag == '"v1"'
    assert lm == "Mon, 01 Jan 2024"


@pytest.mark.asyncio
async def test_conditional_get_200_returns_content_and_headers():
    """200 OK must return (content, new_etag, new_last_modified) from response headers."""
    body = b"<rss>feed content</rss>"
    response = _make_response(
        200,
        content=body,
        headers={"ETag": '"new-etag"', "Last-Modified": "Tue, 02 Jan 2024"},
    )
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        content, etag, lm = await fetch_feed_conditional("https://example.com/feed.xml")

    assert content == body
    assert etag == '"new-etag"'
    assert lm == "Tue, 02 Jan 2024"


@pytest.mark.asyncio
async def test_conditional_get_500_raises():
    """Non-2xx/304 responses must raise HTTPStatusError via raise_for_status."""
    response = _make_response(500)
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_feed_conditional("https://example.com/feed.xml")


# ===========================================================================
# fetch_github_search tests
# ===========================================================================


@pytest.mark.asyncio
async def test_github_search_includes_auth_header_when_token_provided():
    """When github_token is provided, Authorization: Bearer header must be sent."""
    response_data = {"total_count": 0, "items": []}
    response = _make_response(
        200,
        content=b'{"total_count": 0, "items": []}',
        headers={"content-type": "application/json", "x-ratelimit-remaining": "29"},
    )
    # Mock .json() since httpx.Response.json() parses content
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        await fetch_github_search("topic:claude-code", github_token="ghp_test123")

    call_kwargs = mock_client.get.call_args
    headers_sent = call_kwargs.kwargs.get("headers", {})
    assert headers_sent.get("Authorization") == "Bearer ghp_test123"


@pytest.mark.asyncio
async def test_github_search_no_auth_header_when_token_none():
    """When github_token is None, no Authorization header must be sent."""
    response = _make_response(
        200,
        content=b'{"total_count": 0, "items": []}',
        headers={"content-type": "application/json"},
    )
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        await fetch_github_search("topic:claude-code", github_token=None)

    call_kwargs = mock_client.get.call_args
    headers_sent = call_kwargs.kwargs.get("headers", {})
    assert "Authorization" not in headers_sent


@pytest.mark.asyncio
async def test_github_search_correct_params():
    """Query params must include q, sort=updated, per_page, and page."""
    response = _make_response(
        200,
        content=b'{"total_count": 0, "items": []}',
        headers={"content-type": "application/json"},
    )
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        await fetch_github_search("topic:mcp", github_token=None, page=2, per_page=50)

    call_kwargs = mock_client.get.call_args
    params_sent = call_kwargs.kwargs.get("params", {})
    assert params_sent["q"] == "topic:mcp"
    assert params_sent["sort"] == "updated"
    assert params_sent["per_page"] == 50
    assert params_sent["page"] == 2


@pytest.mark.asyncio
async def test_github_search_raises_on_error():
    """Non-2xx response from GitHub API must raise HTTPStatusError."""
    response = _make_response(403)
    mock_client = _mock_client_get(response)

    with patch("src.services.feed_fetcher.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_github_search("topic:claude-code", github_token=None)
