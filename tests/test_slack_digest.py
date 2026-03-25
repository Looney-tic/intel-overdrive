"""Unit tests for the Slack daily digest worker.

Tests the post_daily_digest worker and _format_digest_blocks helper.
Mocks httpx and settings to avoid real HTTP calls and API dependencies.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workers.slack_digest_worker import _format_digest_blocks, post_daily_digest


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_DIGEST = {
    "days": 1,
    "total": 5,
    "groups": [
        {
            "primary_type": "update",
            "count": 3,
            "items": [
                {"title": "Claude 4 Released", "url": "https://example.com/claude-4"},
                {"title": "MCP v2 Spec", "url": "https://example.com/mcp-v2"},
                {"title": "Cursor Update", "url": "https://example.com/cursor"},
                {"title": "Fourth Item", "url": "https://example.com/fourth"},
            ],
        },
        {
            "primary_type": "tool",
            "count": 2,
            "items": [
                {"title": "New DB Tool", "url": "https://example.com/db-tool"},
                {"title": "Test Runner", "url": "https://example.com/test-runner"},
            ],
        },
    ],
}

EMPTY_DIGEST = {
    "days": 1,
    "total": 0,
    "groups": [],
}


# ---------------------------------------------------------------------------
# _format_digest_blocks tests
# ---------------------------------------------------------------------------


def test_format_digest_blocks_structure():
    """Blocks list contains header, sections per group, and footer."""
    blocks = _format_digest_blocks(SAMPLE_DIGEST)
    assert isinstance(blocks, list)
    assert len(blocks) >= 3  # header + at least 1 section + footer

    # First block is header
    assert blocks[0]["type"] == "header"
    assert "Daily AI Ecosystem Digest" in blocks[0]["text"]["text"]

    # Last block is footer (context)
    assert blocks[-1]["type"] == "context"
    assert "Powered by Overdrive Intel" in blocks[-1]["elements"][0]["text"]


def test_format_digest_blocks_includes_items():
    """Item titles appear in section text blocks."""
    blocks = _format_digest_blocks(SAMPLE_DIGEST)
    section_texts = [b["text"]["text"] for b in blocks if b["type"] == "section"]
    combined = "\n".join(section_texts)

    assert "Claude 4 Released" in combined
    assert "New DB Tool" in combined


def test_format_digest_blocks_caps_at_50():
    """Blocks list is capped at 50 items (Slack limit)."""
    # Create a digest with many groups to exceed 50 blocks
    large_digest = {
        "days": 1,
        "groups": [
            {
                "primary_type": f"type-{i}",
                "count": 1,
                "items": [{"title": f"Item {i}", "url": f"https://example.com/{i}"}],
            }
            for i in range(60)  # 60 groups + header + footer = 62 blocks pre-cap
        ],
    }
    blocks = _format_digest_blocks(large_digest)
    assert len(blocks) <= 50


def test_format_digest_blocks_empty_digest():
    """Empty groups list produces minimal output (header + footer only)."""
    blocks = _format_digest_blocks(EMPTY_DIGEST)
    assert len(blocks) == 2  # header + footer
    assert blocks[0]["type"] == "header"
    assert blocks[1]["type"] == "context"


def test_format_digest_blocks_top_3_items_only():
    """Only top 3 items per group appear in section text."""
    blocks = _format_digest_blocks(SAMPLE_DIGEST)
    # The update group has 4 items, but only 3 should appear
    update_section = [
        b for b in blocks if b["type"] == "section" and "update" in b["text"]["text"]
    ]
    assert len(update_section) == 1
    section_text = update_section[0]["text"]["text"]
    assert "Fourth Item" not in section_text
    assert "Claude 4 Released" in section_text


# ---------------------------------------------------------------------------
# post_daily_digest tests — httpx mock helpers
# ---------------------------------------------------------------------------


def _make_mock_settings(
    webhook_url=None, internal_url="http://localhost:8000", internal_key=None
):
    """Create a mock settings object for digest worker tests."""
    mock_settings = MagicMock()
    mock_settings.SLACK_DIGEST_WEBHOOK_URL = webhook_url
    mock_settings.INTERNAL_API_URL = internal_url
    mock_settings.INTERNAL_API_KEY = internal_key
    return mock_settings


def _make_response(status_code=200, json_data=None, text_body="ok"):
    """Create a MagicMock httpx response (sync attributes, not coroutines)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text_body
    return resp


def _make_httpx_client(get_response=None, post_response=None):
    """Create an AsyncMock httpx client with sync-attribute responses."""
    client = AsyncMock()
    if get_response is not None:
        client.get.return_value = get_response
    if post_response is not None:
        client.post.return_value = post_response
    return client


def _patch_httpx_client(mock_client):
    """Patch httpx.AsyncClient to return mock_client as async context manager."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("src.workers.slack_digest_worker.httpx.AsyncClient", return_value=cm)


# ---------------------------------------------------------------------------
# post_daily_digest tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_daily_digest_skips_when_no_webhook():
    """No HTTP call when SLACK_DIGEST_WEBHOOK_URL is not configured."""
    settings = _make_mock_settings(webhook_url=None)

    with patch("src.workers.slack_digest_worker.get_settings", return_value=settings):
        with patch("src.workers.slack_digest_worker.httpx") as mock_httpx:
            await post_daily_digest({})
            mock_httpx.AsyncClient.assert_not_called()


@pytest.mark.asyncio
async def test_post_daily_digest_calls_digest_api():
    """Worker fetches from /v1/digest?days=1 via internal API."""
    settings = _make_mock_settings(
        webhook_url="https://hooks.slack.com/test/123",
        internal_key="test-key-123",
    )
    get_resp = _make_response(status_code=200, json_data=SAMPLE_DIGEST)
    post_resp = _make_response(status_code=200)
    client = _make_httpx_client(get_response=get_resp, post_response=post_resp)

    with patch("src.workers.slack_digest_worker.get_settings", return_value=settings):
        with _patch_httpx_client(client):
            await post_daily_digest({})

            # Verify GET was called for digest
            client.get.assert_called_once()
            call_args = client.get.call_args
            assert "/v1/digest" in call_args[0][0]


@pytest.mark.asyncio
async def test_post_daily_digest_posts_to_webhook():
    """Worker POSTs formatted blocks to the webhook URL."""
    settings = _make_mock_settings(
        webhook_url="https://hooks.slack.com/test/456",
    )
    get_resp = _make_response(status_code=200, json_data=SAMPLE_DIGEST)
    post_resp = _make_response(status_code=200)
    client = _make_httpx_client(get_response=get_resp, post_response=post_resp)

    with patch("src.workers.slack_digest_worker.get_settings", return_value=settings):
        with _patch_httpx_client(client):
            await post_daily_digest({})

            # Verify POST was called to webhook
            client.post.assert_called_once()
            post_call = client.post.call_args
            assert post_call[0][0] == "https://hooks.slack.com/test/456"
            payload = post_call[1]["json"]
            assert "blocks" in payload
            assert isinstance(payload["blocks"], list)


@pytest.mark.asyncio
async def test_post_daily_digest_handles_api_error():
    """Worker handles API error gracefully (no crash, logs error)."""
    settings = _make_mock_settings(
        webhook_url="https://hooks.slack.com/test/789",
    )
    get_resp = _make_response(status_code=500, text_body="Internal Server Error")
    client = _make_httpx_client(get_response=get_resp)

    with patch("src.workers.slack_digest_worker.get_settings", return_value=settings):
        with _patch_httpx_client(client):
            # Should not raise
            await post_daily_digest({})

            # POST should NOT have been called (API returned error)
            client.post.assert_not_called()
