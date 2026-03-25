"""Generic webhook delivery with optional HMAC-SHA256 signing.

HTTP POST delivery for alert rules configured with a webhook_url
in their delivery_channels. Mirrors the retry and error-handling
patterns from slack_delivery.py.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx

from src.core.logger import get_logger

logger = get_logger(__name__)


async def deliver_webhook_alert(
    webhook_url: str,
    payload: dict,
    secret: str | None = None,
    timeout: float = 10.0,
) -> bool:
    """HTTP POST webhook delivery with optional HMAC-SHA256 signing. One retry on 5xx.

    Returns True if the server responded with a 2xx or 3xx status code.
    Returns False on 4xx, 5xx (after one retry), timeout, or any exception.
    Never raises — fire-and-forget.
    """
    # Defense-in-depth: resolve DNS and validate IPs at delivery time (P1-9)
    # Uses _resolve_and_validate_webhook_ip to prevent DNS rebinding TOCTOU attacks
    try:
        from src.api.v1.alerts import _resolve_and_validate_webhook_ip

        _resolve_and_validate_webhook_ip(webhook_url)
    except ValueError as e:
        logger.warning(
            "webhook_ssrf_blocked",
            webhook_fingerprint=webhook_url[-6:] if len(webhook_url) >= 6 else "***",
            reason=str(e),
        )
        return False

    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}

    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Overdrive-Signature"] = f"sha256={sig}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                webhook_url, content=body, headers=headers, timeout=timeout
            )
            # One retry on server error
            if resp.status_code >= 500:
                resp = await client.post(
                    webhook_url, content=body, headers=headers, timeout=timeout
                )
            success = resp.status_code < 400
            if not success:
                logger.warning(
                    "webhook_delivery_failed",
                    webhook_fingerprint=webhook_url[-6:]
                    if len(webhook_url) >= 6
                    else "***",
                    status=resp.status_code,
                )
            return success
        except httpx.TimeoutException:
            logger.warning(
                "webhook_delivery_timeout",
                webhook_fingerprint=webhook_url[-6:]
                if len(webhook_url) >= 6
                else "***",
            )
            return False
        except Exception as e:
            logger.error(
                "webhook_delivery_error",
                webhook_fingerprint=webhook_url[-6:]
                if len(webhook_url) >= 6
                else "***",
                error=str(e),
            )
            return False
