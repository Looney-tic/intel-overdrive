"""Slack webhook delivery with Block Kit formatting.

Posts formatted alert messages to Slack incoming webhooks. Never logs
the full webhook URL (it is a secret) — only the last 6 characters
as a fingerprint for debugging.
"""

from __future__ import annotations

import httpx

from src.core.logger import get_logger

logger = get_logger(__name__)


async def notify_signup(webhook_url: str, user_id: str, tier: str) -> bool:
    """Post a signup notification to Slack. Fire-and-forget, never raises."""
    emoji = ":tada:" if tier == "free" else ":new:"
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{emoji} *New user signed up*\n*Tier:* {tier} | *User:* `{user_id[:8]}…`",
            },
        },
    ]
    payload = {
        "text": f"New {tier} signup: {user_id[:8]}…",
        "blocks": blocks,
    }

    webhook_fingerprint = webhook_url[-6:] if len(webhook_url) >= 6 else "***"

    if not webhook_url.startswith("https://hooks.slack.com/"):
        logger.error(
            "slack_signup_rejected_invalid_url", webhook_fingerprint=webhook_fingerprint
        )
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook_url, json=payload)
        if response.status_code == 200:
            logger.info("slack_signup_sent", user_id=user_id[:8], tier=tier)
            return True
        else:
            logger.warning("slack_signup_failed", status_code=response.status_code)
            return False
    except Exception as exc:
        logger.error("slack_signup_error", error=str(exc))
        return False


async def deliver_slack_alert(
    webhook_url: str,
    item_title: str,
    item_url: str,
    item_type: str,
    urgency: str,
    tags: list[str],
) -> bool:
    """POST a formatted Block Kit message to a Slack incoming webhook.

    Returns True on HTTP 200, False otherwise. Never raises — fire-and-forget.
    CRITICAL: Never logs the full webhook URL (it's a secret).
    """
    urgency_emoji = {
        "critical": ":rotating_light:",
        "important": ":large_blue_circle:",
        "interesting": ":information_source:",
    }.get(urgency, ":bell:")

    tag_str = ", ".join(tags[:5]) if tags else "none"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{urgency_emoji} {urgency.upper()}: New Intel Alert",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*<{item_url}|{item_title}>*",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"*Type:* {item_type} | *Urgency:* {urgency} | *Tags:* {tag_str}",
                }
            ],
        },
    ]

    payload = {
        "text": f"[{urgency.upper()}] {item_title}",  # Fallback for notifications
        "blocks": blocks,
    }

    # Fingerprint: last 6 chars of webhook URL for safe logging
    webhook_fingerprint = webhook_url[-6:] if len(webhook_url) >= 6 else "***"

    # Defense-in-depth: reject non-Slack URLs regardless of how the value reached here
    if not webhook_url.startswith("https://hooks.slack.com/"):
        logger.error(
            "slack_alert_rejected_invalid_url",
            webhook_fingerprint=webhook_fingerprint,
        )
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(webhook_url, json=payload)

        if response.status_code == 200:
            logger.info(
                "slack_alert_sent",
                webhook_fingerprint=webhook_fingerprint,
                urgency=urgency,
            )
            return True
        else:
            logger.warning(
                "slack_alert_failed",
                webhook_fingerprint=webhook_fingerprint,
                status_code=response.status_code,
                response_text=response.text[:200],
            )
            return False

    except Exception as exc:
        logger.error(
            "slack_alert_error",
            webhook_fingerprint=webhook_fingerprint,
            error=str(exc),
        )
        return False
