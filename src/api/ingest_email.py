"""Mailgun email webhook endpoint for newsletter ingestion.

Receives forwarded newsletter emails via Mailgun inbound routing,
validates security (HMAC + timestamp + replay), extracts text,
stores as IntelItem, and returns HTTP 200 on ALL code paths.

MAIL-01: HMAC-SHA256 verification
MAIL-02: Timestamp freshness + Redis replay prevention
MAIL-03: trafilatura text extraction with fallback (optional dependency)
MAIL-04: Excerpt truncation to 500 chars
MAIL-05: All code paths return HTTP 200 to prevent Mailgun retries
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService

# trafilatura is optional — graceful fallback to plain text if not installed
try:
    import trafilatura as _trafilatura  # type: ignore[import]

    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _trafilatura = None  # type: ignore[assignment]
    _TRAFILATURA_AVAILABLE = False

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])

OK_RESPONSE = JSONResponse({"ok": True})


# ─── Pure functions (testable without I/O) ──────────────────────────────────


def verify_mailgun_webhook(
    signing_key: str,
    timestamp: str,
    token: str,
    signature: str,
    max_age_seconds: int = 300,
) -> tuple[bool, str]:
    """Verify Mailgun webhook HMAC-SHA256 signature and timestamp freshness.

    Returns (is_valid, reason) tuple. reason is "ok" on success.
    """
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return (False, "timestamp_invalid")

    if abs(time.time() - ts) > max_age_seconds:
        return (False, "timestamp_expired")

    expected = hmac.new(
        signing_key.encode("utf-8"),
        f"{timestamp}{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return (False, "signature_invalid")

    return (True, "ok")


def build_email_article_url(token: str, sender: str, subject: str) -> str:
    """Build a deterministic mailgun:// URL for dedup.

    Uses SHA-256 of token+sender+subject to produce a stable identifier.
    """
    key = f"mailgun:{token}:{sender}:{subject}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"mailgun://email/{digest}"


# ─── Async helpers ──────────────────────────────────────────────────────────


async def check_and_mark_token(redis_client: Any, token: str) -> bool:
    """Check Redis for token replay and mark as seen.

    Returns True if token is new (not replayed), False if already seen.
    Uses SET NX EX 300 for atomic check-and-set with 5-minute expiry.
    """
    key = f"mailgun:token:{token}"
    result = await redis_client.set(key, "1", nx=True, ex=300)
    return result is not None


async def extract_excerpt(body_html: str, body_plain: str, stripped_text: str) -> str:
    """Extract text from email HTML via trafilatura (if available) with fallback to plain text.

    Uses asyncio.to_thread() to avoid blocking the event loop when trafilatura is available.
    Truncates to 500 chars per MAIL-04.
    """
    text: str | None = None

    # Try trafilatura on HTML first (if installed)
    if body_html and _TRAFILATURA_AVAILABLE and _trafilatura is not None:
        text = await asyncio.to_thread(
            _trafilatura.extract,
            body_html,
            output_format="txt",
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )

    # Fallback to stripped_text or body_plain
    if not text:
        fallback = stripped_text or body_plain
        if fallback:
            # Strip any remaining HTML tags
            text = re.sub(r"<[^>]+>", "", fallback).strip()

    if not text:
        return ""

    # Truncate to 500 chars
    return text[:500]


# ─── Endpoint ───────────────────────────────────────────────────────────────


@router.post("/email-webhook")
async def email_webhook(
    request: Request,
) -> JSONResponse:
    """Receive Mailgun inbound email webhook.

    All code paths return HTTP 200 (MAIL-05) to prevent Mailgun retries.
    DB session is managed independently via async_session_factory
    (endpoint needs independent commit, not the standard get_session dep).
    """
    # Parse form data (Mailgun sends multipart/form-data)
    form_data = await request.form()

    timestamp = str(form_data.get("timestamp", ""))
    token = str(form_data.get("token", ""))
    signature = str(form_data.get("signature", ""))
    sender = str(form_data.get("sender", ""))
    recipient = str(form_data.get("recipient", ""))
    subject = str(form_data.get("subject", ""))
    body_html = str(form_data.get("body-html", ""))
    body_plain = str(form_data.get("body-plain", ""))
    stripped_text = str(form_data.get("stripped-text", ""))

    # Step 1: Check signing key is configured.
    # C-4: Return 500 (not 200) when signing key is missing — prevents silent
    # acceptance of unauthenticated requests in misconfigured deployments.
    # In production, validate_production_secrets() blocks startup if key is absent.
    settings = get_settings()
    if not settings.MAILGUN_WEBHOOK_SIGNING_KEY:
        logger.error("mailgun_webhook_signing_key_not_configured")
        return JSONResponse(
            status_code=500,
            content={"error": "webhook signing key not configured"},
        )

    # Step 2: Verify HMAC signature and timestamp
    is_valid, reason = verify_mailgun_webhook(
        settings.MAILGUN_WEBHOOK_SIGNING_KEY,
        timestamp,
        token,
        signature,
    )
    if not is_valid:
        logger.info("mailgun_webhook_rejected", reason=reason, sender=sender)
        return OK_RESPONSE

    # Step 3: Check for token replay via Redis
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        is_new = await check_and_mark_token(redis_client, token)
        if not is_new:
            logger.info("mailgun_webhook_replay_detected", token=token)
            return OK_RESPONSE
    else:
        logger.warning("mailgun_webhook_no_redis_for_replay_check")
        # Reject when Redis is unavailable — replay protection is critical
        return JSONResponse(
            status_code=503,
            content={"error": "Replay protection unavailable"},
        )

    # Step 4: Process the email using an independent DB session
    if _db.async_session_factory is None:
        logger.error("mailgun_webhook_db_not_initialized")
        return OK_RESPONSE

    try:
        async with _db.async_session_factory() as session:
            # Look up newsletter-email source
            result = await session.execute(
                select(Source).where(
                    Source.type == "newsletter-email",
                    Source.is_active == True,  # noqa: E712
                )
            )
            source = result.scalars().first()
            if source is None:
                logger.warning("mailgun_webhook_no_newsletter_email_source")
                return OK_RESPONSE

            # Step 5: Extract excerpt
            excerpt = await extract_excerpt(body_html, body_plain, stripped_text)
            if not excerpt and not subject:
                logger.info("mailgun_webhook_empty_content", sender=sender)
                return OK_RESPONSE

            # Step 6: Build URL and dedup
            article_url = build_email_article_url(token, sender, subject)
            dedup = DedupService(session)

            if await dedup.check_url_exists(article_url):
                logger.info("mailgun_webhook_duplicate", sender=sender)
                return OK_RESPONSE

            url_hash = dedup._compute_url_hash(article_url)
            content_hash = dedup._get_content_fingerprint(excerpt) if excerpt else None

            # Step 7: Create IntelItem
            item = IntelItem(
                source_id=source.id,
                external_id=url_hash,
                url=article_url,
                url_hash=url_hash,
                title=subject or "(No subject)",
                content=excerpt,
                excerpt=excerpt,
                primary_type="unknown",
                tags=["newsletter"],
                status="raw",
                content_hash=content_hash,
                source_name=source.name,
            )
            session.add(item)

            await session.commit()

            logger.info(
                "mailgun_webhook_stored",
                sender=sender,
                subject=subject,
                item_id=str(item.id),
            )

    except Exception:
        logger.exception(
            "mailgun_webhook_processing_error", sender=sender, subject=subject
        )

    return OK_RESPONSE
