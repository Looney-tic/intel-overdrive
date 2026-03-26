"""Slack weekly source health report worker.

ARQ cron worker registered in SlowWorkerSettings. Fires Sunday at 09:00 UTC.
Reports erroring sources, stale sources, dead sources, and top producers.
POSTs to SLACK_DIGEST_WEBHOOK_URL. Silent skip when URL is not configured.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import text

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)


async def post_weekly_source_health(ctx: dict) -> None:
    """ARQ cron job: post weekly source health report to Slack.

    Queries DB for:
    - Sources with consecutive_errors > 3 (erroring)
    - Active sources with no successful poll in 7 days (stale)
    - Inactive sources count (dead)
    - Top 5 most productive sources in last 7 days

    Skips silently when SLACK_DIGEST_WEBHOOK_URL is not configured or DB not init.
    """
    settings = get_settings()
    webhook_url = settings.SLACK_DIGEST_WEBHOOK_URL

    if not webhook_url:
        logger.debug("source_health_skipped", reason="no webhook URL configured")
        return

    if _db.async_session_factory is None:
        logger.error("source_health_called_before_db_init")
        return

    try:
        async with _db.async_session_factory() as session:
            # a. Erroring sources (consecutive_errors > 3)
            error_result = await session.execute(
                text(
                    """
                    SELECT name, type, consecutive_errors, last_successful_poll
                    FROM sources
                    WHERE consecutive_errors > 3 AND is_active = TRUE
                    ORDER BY consecutive_errors DESC
                    LIMIT 20
                """
                )
            )
            erroring = [
                {
                    "name": row[0],
                    "type": row[1],
                    "errors": row[2],
                    "last_success": row[3].isoformat() if row[3] else "never",
                }
                for row in error_result.fetchall()
            ]

            # b. Stale sources (no successful poll in 7 days, still active)
            stale_result = await session.execute(
                text(
                    """
                    SELECT name, type, last_successful_poll
                    FROM sources
                    WHERE is_active = TRUE
                      AND (last_successful_poll < NOW() - INTERVAL '7 days'
                           OR last_successful_poll IS NULL)
                    ORDER BY last_successful_poll ASC NULLS FIRST
                    LIMIT 20
                """
                )
            )
            stale = [
                {
                    "name": row[0],
                    "type": row[1],
                    "last_success": row[2].isoformat() if row[2] else "never",
                }
                for row in stale_result.fetchall()
            ]

            # c. Dead sources count (is_active = FALSE)
            dead_result = await session.execute(
                text("SELECT COUNT(*) FROM sources WHERE is_active = FALSE")
            )
            dead_count = dead_result.scalar() or 0

            # d. Top 5 most productive sources in last 7 days
            top_result = await session.execute(
                text(
                    """
                    SELECT s.name, s.type, COUNT(i.id) as item_count
                    FROM sources s
                    JOIN intel_items i ON i.source_id = s.id
                    WHERE i.created_at > NOW() - INTERVAL '7 days'
                    GROUP BY s.id, s.name, s.type
                    ORDER BY item_count DESC
                    LIMIT 5
                """
                )
            )
            top_producers = [
                {"name": row[0], "type": row[1], "count": row[2]}
                for row in top_result.fetchall()
            ]

            # Total source counts
            total_result = await session.execute(
                text("SELECT COUNT(*) FROM sources WHERE is_active = TRUE")
            )
            total_active = total_result.scalar() or 0

        # Format Slack blocks
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"Weekly Source Health Report - {today}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Active sources:* {total_active} | *Dead:* {dead_count}",
                },
            },
        ]

        # Erroring sources
        if erroring:
            error_lines = [
                f"- {s['name']} ({s['type']}) -- {s['errors']} consecutive errors"
                for s in erroring[:10]
            ]
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*:x: Erroring Sources ({len(erroring)})*\n"
                        + "\n".join(error_lines),
                    },
                }
            )
        else:
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*:white_check_mark: No erroring sources*",
                    },
                }
            )

        # Stale sources
        if stale:
            stale_lines = [
                f"- {s['name']} ({s['type']}) -- last success: {s['last_success']}"
                for s in stale[:10]
            ]
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*:hourglass: Stale Sources ({len(stale)})*\n"
                        + "\n".join(stale_lines),
                    },
                }
            )

        blocks.append({"type": "divider"})

        # Top producers
        if top_producers:
            producer_lines = [
                f"{i+1}. {p['name']} ({p['type']}) -- {p['count']} items"
                for i, p in enumerate(top_producers)
            ]
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*:trophy: Top Producers (7 days)*\n"
                        + "\n".join(producer_lines),
                    },
                }
            )

        # Footer
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Powered by Intel Overdrive | Weekly source health report",
                    }
                ],
            }
        )

        # Cap at 50 blocks (Slack limit)
        blocks = blocks[:50]

        # POST to Slack webhook
        payload = {
            "text": "Weekly Source Health Report",
            "blocks": blocks,
        }

        webhook_fingerprint = webhook_url[-6:] if len(webhook_url) >= 6 else "***"

        async with httpx.AsyncClient(timeout=10) as client:
            post_resp = await client.post(webhook_url, json=payload)

        if post_resp.status_code == 200:
            logger.info(
                "source_health_posted",
                webhook_fingerprint=webhook_fingerprint,
                erroring=len(erroring),
                stale=len(stale),
                dead=dead_count,
            )
        else:
            logger.warning(
                "source_health_post_failed",
                webhook_fingerprint=webhook_fingerprint,
                status_code=post_resp.status_code,
                response_text=post_resp.text[:200],
            )

    except Exception as exc:
        logger.error("source_health_error", error=str(exc))
