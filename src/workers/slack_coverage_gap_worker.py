"""Slack coverage gap alert worker.

ARQ cron worker registered in SlowWorkerSettings. Fires daily at 09:30 UTC.
Detects repeated auto_miss feedback (3+ for same keyword in 7 days) and
alerts via Slack. POSTs to SLACK_WEBHOOK_URL. Silent skip when not configured.
"""

from __future__ import annotations

import json
from collections import Counter

import httpx
from sqlalchemy import text

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)


async def check_coverage_gaps(ctx: dict) -> None:
    """ARQ cron job: detect coverage gaps from accumulated auto_miss feedback.

    Queries feedback table for auto_miss reports in the last 7 days.
    Groups by normalized query keyword. If any keyword has 3+ occurrences,
    sends a Slack alert to SLACK_WEBHOOK_URL.

    Skips silently when SLACK_WEBHOOK_URL is not configured or DB not init.
    """
    settings = get_settings()
    webhook_url = settings.SLACK_WEBHOOK_URL

    if not webhook_url:
        logger.debug("coverage_gap_skipped", reason="no webhook URL configured")
        return

    if _db.async_session_factory is None:
        logger.error("coverage_gap_called_before_db_init")
        return

    try:
        async with _db.async_session_factory() as session:
            # Query auto_miss feedback in last 7 days
            result = await session.execute(
                text(
                    """
                    SELECT notes
                    FROM feedback
                    WHERE report_type = 'auto_miss'
                      AND created_at > NOW() - INTERVAL '7 days'
                """
                )
            )
            rows = result.fetchall()

        if not rows:
            logger.debug(
                "coverage_gap_no_feedback", message="No auto_miss feedback in 7 days"
            )
            return

        # Parse notes JSON to extract query text, group by normalized keyword
        keyword_counter: Counter[str] = Counter()
        for row in rows:
            notes_raw = row[0]
            if not notes_raw:
                continue
            try:
                notes_data = (
                    json.loads(notes_raw) if isinstance(notes_raw, str) else notes_raw
                )
                query = (
                    notes_data.get("query", "") if isinstance(notes_data, dict) else ""
                )
            except (json.JSONDecodeError, TypeError):
                # notes might be plain text rather than JSON
                query = notes_raw if isinstance(notes_raw, str) else ""

            # Normalize: lowercase, strip whitespace
            normalized = query.lower().strip()
            if normalized:
                keyword_counter[normalized] += 1

        # Filter to keywords with 3+ occurrences
        gaps = {kw: count for kw, count in keyword_counter.items() if count >= 3}

        if not gaps:
            logger.debug(
                "coverage_gap_no_gaps",
                total_feedback=len(rows),
                unique_keywords=len(keyword_counter),
            )
            return

        # Build Slack alert
        gap_lines = [
            f"- *{kw}* -- {count} queries with no/low results in 7 days"
            for kw, count in sorted(gaps.items(), key=lambda x: x[1], reverse=True)
        ]

        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":mag: Coverage Gap Alert",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{len(gaps)} coverage gap(s) detected* in the last 7 days.\n"
                        "Consider adding sources for these topics:\n\n"
                        + "\n".join(gap_lines[:20])
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Powered by Overdrive Intel | Auto-detected from search miss feedback",
                    }
                ],
            },
        ]

        payload = {
            "text": f"Coverage gap detected: {len(gaps)} keyword(s) with repeated misses",
            "blocks": blocks,
        }

        webhook_fingerprint = webhook_url[-6:] if len(webhook_url) >= 6 else "***"

        async with httpx.AsyncClient(timeout=10) as client:
            post_resp = await client.post(webhook_url, json=payload)

        if post_resp.status_code == 200:
            logger.info(
                "coverage_gap_alert_posted",
                webhook_fingerprint=webhook_fingerprint,
                gap_count=len(gaps),
                gaps=list(gaps.keys())[:5],
            )
        else:
            logger.warning(
                "coverage_gap_alert_failed",
                webhook_fingerprint=webhook_fingerprint,
                status_code=post_resp.status_code,
                response_text=post_resp.text[:200],
            )

    except Exception as exc:
        logger.error("coverage_gap_error", error=str(exc))
