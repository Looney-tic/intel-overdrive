"""Slack daily digest worker -- posts grouped intel digest to Slack webhook.

ARQ cron worker registered in SlowWorkerSettings. Fires daily at 8:00am UTC.
Enhanced version: queries DB directly for top items, new sources, pipeline
health, and quality stats. Falls back to internal API call if DB query fails.
POSTs to SLACK_DIGEST_WEBHOOK_URL. Silent skip when URL is not configured.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx
from sqlalchemy import text

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)

# Type emoji mapping for Slack block formatting
_TYPE_EMOJI: dict[str, str] = {
    "update": ":arrows_counterclockwise:",
    "tool": ":wrench:",
    "skill": ":brain:",
    "practice": ":book:",
    "docs": ":page_facing_up:",
    "breaking-change": ":warning:",
    "model": ":robot_face:",
}

# Significance emoji mapping
_SIG_EMOJI: dict[str, str] = {
    "breaking": ":rotating_light:",
    "major": ":large_orange_diamond:",
    "moderate": ":small_blue_diamond:",
    "minor": ":white_small_square:",
    "informational": ":white_small_square:",
}


async def _build_enhanced_digest(session) -> dict | None:
    """Query DB directly for rich digest data.

    Returns dict with top_items, new_sources, pipeline_health, quality_stats
    or None if queries fail.
    """
    try:
        # a. Top 5 items by significance in last 24h
        top_result = await session.execute(
            text(
                """
                SELECT title, url, significance, quality_score, source_name, primary_type
                FROM intel_items
                WHERE status = 'processed'
                  AND created_at > NOW() - INTERVAL '24 hours'
                ORDER BY
                    CASE significance
                        WHEN 'breaking' THEN 1
                        WHEN 'major' THEN 2
                        WHEN 'moderate' THEN 3
                        ELSE 4
                    END,
                    quality_score DESC
                LIMIT 5
            """
            )
        )
        top_items = [
            {
                "title": row[0],
                "url": row[1],
                "significance": row[2],
                "quality_score": row[3],
                "source_name": row[4],
                "primary_type": row[5],
            }
            for row in top_result.fetchall()
        ]

        # b. New sources added in last 24h
        new_sources_result = await session.execute(
            text(
                """
                SELECT name, type
                FROM sources
                WHERE created_at > NOW() - INTERVAL '24 hours'
                ORDER BY created_at DESC
            """
            )
        )
        new_sources = [
            {"name": row[0], "type": row[1]} for row in new_sources_result.fetchall()
        ]

        # c. Pipeline health: count items by status in last 24h
        health_result = await session.execute(
            text(
                """
                SELECT status, COUNT(*) as cnt
                FROM intel_items
                WHERE created_at > NOW() - INTERVAL '24 hours'
                GROUP BY status
            """
            )
        )
        pipeline_health = {row[0]: row[1] for row in health_result.fetchall()}

        # d. Quality stats: AVG and low-quality count
        quality_result = await session.execute(
            text(
                """
                SELECT
                    ROUND(AVG(quality_score)::numeric, 2) as avg_quality,
                    COUNT(*) FILTER (WHERE quality_score < 0.3) as low_quality_count,
                    COUNT(*) as total_processed
                FROM intel_items
                WHERE status = 'processed'
                  AND created_at > NOW() - INTERVAL '24 hours'
            """
            )
        )
        quality_row = quality_result.fetchone()
        quality_stats = {
            "avg_quality": float(quality_row[0] or 0),
            "low_quality_count": quality_row[1] or 0,
            "total_processed": quality_row[2] or 0,
        }

        return {
            "top_items": top_items,
            "new_sources": new_sources,
            "pipeline_health": pipeline_health,
            "quality_stats": quality_stats,
        }

    except Exception as exc:
        logger.warning("enhanced_digest_query_failed", error=str(exc))
        return None


def _format_enhanced_blocks(data: dict) -> list[dict]:
    """Format enhanced digest data as Slack Block Kit blocks."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Daily AI Ecosystem Digest - {today}",
                "emoji": True,
            },
        },
    ]

    # Top items section
    top_items = data.get("top_items", [])
    if top_items:
        item_lines: list[str] = []
        for item in top_items:
            sig = item.get("significance", "informational")
            sig_emoji = _SIG_EMOJI.get(sig, ":white_small_square:")
            title = item.get("title", "Untitled")
            url = item.get("url", "")
            source = item.get("source_name", "")
            quality = item.get("quality_score", 0)
            link = f"<{url}|{title}>" if url else title
            item_lines.append(
                f"{sig_emoji} {link}\n     _{source}_ | Quality: {quality:.1f}"
            )

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*:trophy: Top 5 Items*\n" + "\n".join(item_lines),
                },
            }
        )
    else:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*:trophy: Top Items*\nNo processed items in the last 24h.",
                },
            }
        )

    blocks.append({"type": "divider"})

    # Pipeline health section
    pipeline = data.get("pipeline_health", {})
    if pipeline:
        total = sum(pipeline.values())
        processed = pipeline.get("processed", 0)
        failed = pipeline.get("failed", 0)
        raw = pipeline.get("raw", 0)
        embedded = pipeline.get("embedded", 0)
        queued = pipeline.get("queued", 0)
        filtered = pipeline.get("filtered", 0)
        health_text = (
            f"*:gear: Pipeline Health (24h)*\n"
            f"Total: {total} | Processed: {processed} | Failed: {failed}\n"
            f"Raw: {raw} | Embedded: {embedded} | Queued: {queued} | Filtered: {filtered}"
        )
    else:
        health_text = (
            "*:gear: Pipeline Health (24h)*\nNo items ingested in the last 24h."
        )
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": health_text},
        }
    )

    # Quality stats section
    quality = data.get("quality_stats", {})
    avg_q = quality.get("avg_quality", 0)
    low_q = quality.get("low_quality_count", 0)
    total_p = quality.get("total_processed", 0)
    quality_text = (
        f"*:bar_chart: Quality Stats (24h)*\n"
        f"Avg quality: {avg_q:.2f} | Low quality (<0.3): {low_q} | Total processed: {total_p}"
    )
    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": quality_text},
        }
    )

    # New sources section
    new_sources = data.get("new_sources", [])
    if new_sources:
        source_lines = [f"- {s['name']} ({s['type']})" for s in new_sources[:10]]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*:new: New Sources ({len(new_sources)})*\n"
                    + "\n".join(source_lines),
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
                    "text": "Powered by Intel Overdrive | View more: `overdrive-intel feed --days 1`",
                }
            ],
        }
    )

    # Cap at 50 blocks (Slack limit)
    return blocks[:50]


def _format_digest_blocks(digest: dict) -> list[dict]:
    """Format digest API response as Slack Block Kit blocks.

    Args:
        digest: Dict with 'days' and 'groups' keys from /v1/digest response.

    Returns:
        List of Slack Block Kit block dicts (capped at 50 per Slack limit).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Daily AI Ecosystem Digest - {today}",
                "emoji": True,
            },
        },
    ]

    groups = digest.get("groups", [])
    for group in groups:
        ptype = group.get("primary_type", group.get("type", "unknown"))
        count = group.get("count", 0)
        items = group.get("items", [])
        emoji = _TYPE_EMOJI.get(ptype, ":small_blue_diamond:")

        # Build item lines -- top 3 with title and URL as mrkdwn links
        item_lines: list[str] = []
        for item in items[:3]:
            title = item.get("title", "Untitled")
            url = item.get("url", "")
            if url:
                item_lines.append(f"- <{url}|{title}>")
            else:
                item_lines.append(f"- {title}")

        section_text = f"{emoji} *{ptype}* ({count} items)\n" + "\n".join(item_lines)

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": section_text,
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
                    "text": "Powered by Intel Overdrive | View more: `overdrive-intel feed --days 1`",
                }
            ],
        }
    )

    # Cap at 50 blocks (Slack limit)
    return blocks[:50]


async def post_daily_digest(ctx: dict) -> None:
    """ARQ cron job: fetch digest and post to Slack webhook.

    Enhanced: queries DB directly for top items, new sources, pipeline health,
    and quality stats. Falls back to internal API if DB query fails.
    Skips silently when SLACK_DIGEST_WEBHOOK_URL is not configured.
    """
    settings = get_settings()
    webhook_url = settings.SLACK_DIGEST_WEBHOOK_URL

    if not webhook_url:
        logger.debug("slack_digest_skipped", reason="no webhook URL configured")
        return

    # Try enhanced DB-direct approach first
    blocks = None
    if _db.async_session_factory is not None:
        try:
            async with _db.async_session_factory() as session:
                enhanced_data = await _build_enhanced_digest(session)
                if enhanced_data:
                    blocks = _format_enhanced_blocks(enhanced_data)
                    logger.info("slack_digest_using_enhanced", source="db_direct")
        except Exception as exc:
            logger.warning("slack_digest_enhanced_failed", error=str(exc))

    # Fallback to internal API approach
    if not blocks:
        internal_url = getattr(settings, "INTERNAL_API_URL", None) or os.environ.get(
            "OVERDRIVE_API_URL", "https://inteloverdrive.com"
        )
        digest_url = f"{internal_url}/v1/digest?days=1&per_group=5"

        internal_key = getattr(settings, "INTERNAL_API_KEY", None)
        headers: dict[str, str] = {}
        if internal_key:
            headers["X-API-Key"] = internal_key

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(digest_url, headers=headers)
                if resp.status_code != 200:
                    logger.error(
                        "slack_digest_api_error",
                        status_code=resp.status_code,
                        response_text=resp.text[:200],
                    )
                    return
                digest = resp.json()

            blocks = _format_digest_blocks(digest)
            logger.info("slack_digest_using_fallback", source="api")
        except Exception as exc:
            logger.error("slack_digest_error", error=str(exc))
            return

    if not blocks:
        logger.info("slack_digest_empty", message="No digest content to post")
        return

    # POST to Slack webhook
    payload = {
        "text": "Daily AI Ecosystem Digest",
        "blocks": blocks,
    }

    # Fingerprint for safe logging (never log full URL)
    webhook_fingerprint = webhook_url[-6:] if len(webhook_url) >= 6 else "***"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            post_resp = await client.post(webhook_url, json=payload)

        if post_resp.status_code == 200:
            logger.info(
                "slack_digest_posted",
                webhook_fingerprint=webhook_fingerprint,
                block_count=len(blocks),
            )
        else:
            logger.warning(
                "slack_digest_post_failed",
                webhook_fingerprint=webhook_fingerprint,
                status_code=post_resp.status_code,
                response_text=post_resp.text[:200],
            )
    except Exception as exc:
        logger.error("slack_digest_error", error=str(exc))
