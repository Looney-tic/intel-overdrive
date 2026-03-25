"""Tests for the webhook delivery service (UX-07).

Tests cover HTTP POST delivery, HMAC signing, retry behavior on 5xx,
no-retry on 4xx, and timeout handling.
"""
import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from src.services.webhook_delivery import deliver_webhook_alert


# ---------------------------------------------------------------------------
# Helper to build a mock httpx AsyncClient context manager
# ---------------------------------------------------------------------------


def _make_mock_client(*responses):
    """Build a mock httpx.AsyncClient that returns responses in sequence."""
    instance = AsyncMock()
    instance.post = AsyncMock(side_effect=list(responses))
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)
    return instance


# ---------------------------------------------------------------------------
# Test: successful delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_webhook_success():
    """UX-07: deliver_webhook_alert returns True on 2xx response."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    instance = _make_mock_client(mock_response)

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload={"event": "alert", "item": {"title": "Test Alert"}},
        )

    assert result is True
    instance.post.assert_called_once()


# ---------------------------------------------------------------------------
# Test: 5xx triggers one retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_webhook_5xx_retries_once():
    """UX-07: 5xx response triggers one retry; returns True if retry succeeds."""
    response_500 = MagicMock()
    response_500.status_code = 500

    response_200 = MagicMock()
    response_200.status_code = 200

    instance = _make_mock_client(response_500, response_200)

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload={"event": "alert"},
        )

    assert result is True
    assert (
        instance.post.call_count == 2
    ), "Should have called POST twice (1 attempt + 1 retry)"


@pytest.mark.asyncio
async def test_deliver_webhook_5xx_fails_after_retry():
    """UX-07: Two consecutive 5xx responses returns False."""
    response_500a = MagicMock()
    response_500a.status_code = 500

    response_500b = MagicMock()
    response_500b.status_code = 503

    instance = _make_mock_client(response_500a, response_500b)

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload={"event": "alert"},
        )

    assert result is False
    assert instance.post.call_count == 2


# ---------------------------------------------------------------------------
# Test: 4xx — no retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_webhook_4xx_no_retry():
    """UX-07: 4xx response returns False immediately without retry."""
    response_403 = MagicMock()
    response_403.status_code = 403

    instance = _make_mock_client(response_403)

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload={"event": "alert"},
        )

    assert result is False
    assert instance.post.call_count == 1, "4xx should not trigger a retry"


# ---------------------------------------------------------------------------
# Test: timeout handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_webhook_timeout_returns_false():
    """UX-07: TimeoutException returns False and does not raise."""
    instance = AsyncMock()
    instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload={"event": "alert"},
        )

    assert result is False


# ---------------------------------------------------------------------------
# Test: HMAC signing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_webhook_hmac_signature_header_present():
    """UX-07: When secret is provided, X-Overdrive-Signature header is included."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    instance = _make_mock_client(mock_response)

    secret = "my-webhook-secret"
    payload = {"event": "alert", "item": {"title": "Test"}}

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload=payload,
            secret=secret,
        )

    assert result is True

    # Verify the POST was called with the HMAC signature header
    call_kwargs = instance.post.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
    assert "X-Overdrive-Signature" in headers, "HMAC signature header should be present"

    # Verify the signature is correct
    body = json.dumps(payload)
    expected_sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    assert headers["X-Overdrive-Signature"] == f"sha256={expected_sig}"


@pytest.mark.asyncio
async def test_deliver_webhook_no_secret_no_signature_header():
    """UX-07: Without a secret, X-Overdrive-Signature header is NOT included."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    instance = _make_mock_client(mock_response)

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload={"event": "alert"},
            secret=None,
        )

    assert result is True

    call_kwargs = instance.post.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
    assert "X-Overdrive-Signature" not in headers


@pytest.mark.asyncio
async def test_deliver_webhook_3xx_is_success():
    """UX-07: 3xx responses are treated as success (status_code < 400)."""
    response_302 = MagicMock()
    response_302.status_code = 302

    instance = _make_mock_client(response_302)

    with patch(
        "src.services.webhook_delivery.httpx.AsyncClient", return_value=instance
    ):
        result = await deliver_webhook_alert(
            webhook_url="https://example.com/hook",
            payload={"event": "alert"},
        )

    assert result is True
