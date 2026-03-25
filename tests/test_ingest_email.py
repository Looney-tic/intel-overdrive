"""
EXT-03: Email webhook unit tests.

Tests for pure functions in src/api/ingest_email.py:
- verify_mailgun_webhook_valid: correct HMAC passes verification
- verify_mailgun_webhook_expired_timestamp: stale timestamp rejected
- verify_mailgun_webhook_bad_signature: wrong HMAC rejected
- build_email_article_url: returns deterministic mailgun:// URL
- check_and_mark_token_new: Redis SET NX returning True → new token
- check_and_mark_token_replay: Redis SET NX returning None → replay

IMPORTANT: These tests exercise pure functions and async helpers directly.
Do NOT test the full FastAPI endpoint — that requires full app context.

Mocking strategy:
- verify_mailgun_webhook and build_email_article_url are pure functions (no mocking needed)
- check_and_mark_token requires a mocked Redis client
- time.time is patched to control timestamp freshness
"""
import hashlib
import hmac
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.api.ingest_email import (
    verify_mailgun_webhook,
    build_email_article_url,
    check_and_mark_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_signature(signing_key: str, timestamp: str, token: str) -> str:
    """Compute the correct HMAC-SHA256 signature for test inputs."""
    return hmac.new(
        signing_key.encode("utf-8"),
        f"{timestamp}{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Tests: verify_mailgun_webhook
# ---------------------------------------------------------------------------


def test_verify_mailgun_webhook_valid():
    """Correct HMAC with fresh timestamp → (True, 'ok')."""
    signing_key = "test-signing-key-abc123"
    token = "unique-token-xyz"
    timestamp = str(int(time.time()))  # now → fresh
    signature = _make_valid_signature(signing_key, timestamp, token)

    result, reason = verify_mailgun_webhook(signing_key, timestamp, token, signature)

    assert result is True
    assert reason == "ok"


def test_verify_mailgun_webhook_expired_timestamp():
    """Timestamp more than 300 seconds old → (False, 'timestamp_expired')."""
    signing_key = "test-signing-key-abc123"
    token = "unique-token-xyz"
    # Use a timestamp 600 seconds in the past (expired)
    stale_ts = str(int(time.time()) - 600)
    signature = _make_valid_signature(signing_key, stale_ts, token)

    result, reason = verify_mailgun_webhook(signing_key, stale_ts, token, signature)

    assert result is False
    assert reason == "timestamp_expired"


def test_verify_mailgun_webhook_bad_signature():
    """Wrong HMAC signature → (False, 'signature_invalid')."""
    signing_key = "test-signing-key-abc123"
    token = "unique-token-xyz"
    timestamp = str(int(time.time()))
    wrong_signature = "deadbeef" * 8  # 64-char hex that won't match

    result, reason = verify_mailgun_webhook(
        signing_key, timestamp, token, wrong_signature
    )

    assert result is False
    assert reason == "signature_invalid"


def test_verify_mailgun_webhook_invalid_timestamp_format():
    """Non-numeric timestamp → (False, 'timestamp_invalid')."""
    result, reason = verify_mailgun_webhook("key", "not-a-number", "token", "sig")
    assert result is False
    assert reason == "timestamp_invalid"


# ---------------------------------------------------------------------------
# Tests: build_email_article_url
# ---------------------------------------------------------------------------


def test_build_email_article_url():
    """build_email_article_url returns a deterministic mailgun:// URL."""
    token = "abc123"
    sender = "newsletter@example.com"
    subject = "This Week in AI"

    url1 = build_email_article_url(token, sender, subject)
    url2 = build_email_article_url(token, sender, subject)

    assert url1 == url2
    assert url1.startswith("mailgun://email/")
    # Digest is 16 hex chars
    digest_part = url1.split("mailgun://email/")[1]
    assert len(digest_part) == 16
    assert all(c in "0123456789abcdef" for c in digest_part)


def test_build_email_article_url_different_inputs_different_urls():
    """Different subject produces different URL (no hash collision for common inputs)."""
    url1 = build_email_article_url("token1", "sender@example.com", "Subject A")
    url2 = build_email_article_url("token1", "sender@example.com", "Subject B")
    assert url1 != url2


# ---------------------------------------------------------------------------
# Tests: check_and_mark_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_and_mark_token_new():
    """Redis SET NX returning a truthy value → token is new, returns True."""
    mock_redis = AsyncMock()
    # SET NX returns the string "OK" or True when key was set successfully
    mock_redis.set = AsyncMock(return_value=True)

    result = await check_and_mark_token(mock_redis, "fresh-token-001")

    assert result is True
    mock_redis.set.assert_called_once_with(
        "mailgun:token:fresh-token-001", "1", nx=True, ex=300
    )


@pytest.mark.asyncio
async def test_check_and_mark_token_replay():
    """Redis SET NX returning None → key already exists, replay detected, returns False."""
    mock_redis = AsyncMock()
    # SET NX returns None when key already exists
    mock_redis.set = AsyncMock(return_value=None)

    result = await check_and_mark_token(mock_redis, "replayed-token-002")

    assert result is False
    mock_redis.set.assert_called_once_with(
        "mailgun:token:replayed-token-002", "1", nx=True, ex=300
    )


# ---------------------------------------------------------------------------
# Tests: C-4 — Mailgun auth bypass fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_webhook_missing_signing_key_returns_500():
    """C-4: email_webhook returns HTTP 500 (not 200) when signing key is missing.

    Previously returned OK_RESPONSE (200) — a misconfigured deployment would
    silently accept unauthenticated requests. Now returns 500 to signal misconfiguration.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import MagicMock
    from src.api.ingest_email import router
    from src.core.config import Settings

    app = FastAPI()
    app.include_router(router)

    # Patch get_settings to return settings with no signing key
    mock_settings = MagicMock(spec=Settings)
    mock_settings.MAILGUN_WEBHOOK_SIGNING_KEY = None

    with patch("src.api.ingest_email.get_settings", return_value=mock_settings):
        client = TestClient(app)
        response = client.post(
            "/v1/ingest/email-webhook",
            data={
                "timestamp": "1700000000",
                "token": "testtoken",
                "signature": "testsig",
            },
        )

    assert response.status_code == 500
    assert "error" in response.json()
    assert "signing key" in response.json()["error"].lower()
