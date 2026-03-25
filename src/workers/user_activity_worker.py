"""Daily user activity digest — posts user stats to Slack admin webhook.

ARQ cron worker registered in SlowWorkerSettings. Fires daily at 9:00am UTC.
Queries user/key stats directly from DB, formats as Slack Block Kit, and
POSTs to SLACK_WEBHOOK_URL (the admin/alerts webhook, not digest webhook).
Silent skip when URL is not configured.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import text

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)


async def post_user_activity_digest(ctx: dict) -> None:
    """ARQ cron job: query user stats and post summary to Slack."""
    settings = get_settings()
    webhook_url = settings.SLACK_WEBHOOK_URL

    if not webhook_url:
        logger.debug("user_activity_skipped", reason="no SLACK_WEBHOOK_URL")
        return

    if _db.async_session_factory is None:
        logger.error("user_activity_db_not_initialized")
        return

    try:
        async with _db.async_session_factory() as session:
            result = await session.execute(
                text(
                    """
                SELECT
                    (SELECT COUNT(*) FROM users WHERE is_active = true) AS active_users,
                    (SELECT COUNT(*) FROM users) AS total_users,
                    (SELECT SUM(usage_count) FROM api_keys) AS total_requests,
                    (SELECT COUNT(DISTINCT k.user_id)
                     FROM api_keys k
                     WHERE k.last_used_at >= NOW() - INTERVAL '24 hours'
                    ) AS users_active_24h,
                    (SELECT SUM(k.usage_count)
                     FROM api_keys k
                     WHERE k.last_used_at >= NOW() - INTERVAL '24 hours'
                    ) AS requests_24h,
                    (SELECT COUNT(*) FROM intel_items WHERE status = 'processed') AS total_items,
                    (SELECT COUNT(*)
                     FROM intel_items
                     WHERE created_at >= NOW() - INTERVAL '24 hours'
                    ) AS items_24h
            """
                )
            )
            stats = result.mappings().first()

            # Top users by activity in last 24h
            top_result = await session.execute(
                text(
                    """
                SELECT u.email, SUM(k.usage_count) AS reqs,
                       MAX(k.last_used_at) AS last_active
                FROM users u
                JOIN api_keys k ON k.user_id = u.id
                WHERE k.last_used_at >= NOW() - INTERVAL '24 hours'
                GROUP BY u.email
                ORDER BY reqs DESC
                LIMIT 5
            """
                )
            )
            top_users = top_result.mappings().all()

            # New registrations in last 24h
            new_result = await session.execute(
                text(
                    """
                SELECT email, created_at
                FROM users
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
            """
                )
            )
            new_users = new_result.mappings().all()

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"User Activity Report - {today}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Users:* {stats['users_active_24h'] or 0} active today / "
                        f"{stats['active_users']} total\n"
                        f"*Requests (24h):* {stats['requests_24h'] or 0}\n"
                        f"*Items ingested (24h):* {stats['items_24h']}\n"
                        f"*Total processed items:* {stats['total_items']}"
                    ),
                },
            },
        ]

        # Top users section (P2-29: mask emails for PII protection)
        if top_users:
            lines = []
            for u in top_users:
                raw_email = u["email"] or "anon"
                masked = raw_email[:3] + "***" if len(raw_email) > 3 else "***"
                lines.append(f"  {masked} — {u['reqs']} requests")
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Top Users (24h):*\n" + "\n".join(lines),
                    },
                }
            )

        # New registrations (P2-29: mask emails for PII protection)
        if new_users:
            masked_emails = [
                (
                    u["email"][:3] + "***"
                    if u["email"] and len(u["email"]) > 3
                    else "***"
                )
                for u in new_users
            ]
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*New Registrations:* {', '.join(masked_emails)}",
                    },
                }
            )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Overdrive Intel Admin | `overdrive-intel admin stats` for details",
                    }
                ],
            }
        )

        payload = {
            "text": f"User Activity Report - {today}",
            "blocks": blocks[:50],
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)

        if resp.status_code == 200:
            logger.info(
                "user_activity_posted",
                active_24h=stats["users_active_24h"],
                requests_24h=stats["requests_24h"],
            )
        else:
            logger.warning(
                "user_activity_post_failed",
                status_code=resp.status_code,
            )

    except Exception as exc:
        logger.error("user_activity_error", error=str(exc))
