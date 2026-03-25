"""Alert workers for post-classification alert delivery.

Cron-driven ARQ slow-queue worker:
  check_alerts: processed items -> keyword match -> cooldown check -> Slack delivery

Registered in SlowWorkerSettings alongside embed_items, gate_relevance, classify_items.
"""

import src.core.init_db as _init_db
from sqlalchemy import text
from src.services.alert_engine import check_and_deliver_alerts
from src.core.logger import get_logger

logger = get_logger(__name__)


async def check_alerts(ctx: dict) -> None:
    """Slow queue cron: match newly-processed items against active alert rules.

    Uses NOT IN (SELECT intel_item_id FROM alert_deliveries) to find items
    that haven't been alerted yet. This avoids the updated_at bug where raw
    SQL UPDATE in classify_items doesn't trigger ORM onupdate.

    First-run backfill protection: on first ever run, sets a Redis marker
    and skips processing to prevent a storm of alerts for all existing items.
    """
    if _init_db.async_session_factory is None:
        logger.error("check_alerts_called_before_db_init")
        return

    redis_client = ctx["redis"]

    # First-run backfill protection
    first_run_done = await redis_client.get("alert:first_run_done")
    if first_run_done is None:
        await redis_client.set("alert:first_run_done", "1")
        logger.info(
            "check_alerts_first_run",
            message="Set initial marker, skipping backfill",
        )
        return

    async with _init_db.async_session_factory() as session:
        # Fetch processed items that have NOT yet been alerted.
        # NOT EXISTS (correlated subquery) is used instead of NOT IN:
        #   - NOT IN returns no rows if the subquery contains any NULLs (safety bug)
        #   - NOT EXISTS scales better — the subquery is correlated and stops early
        # Time window (24h) bounds the scan so old items are not reconsidered
        # indefinitely as the table grows.
        result = await session.execute(
            text(
                """
                SELECT id, title, content, tags, primary_type, url, confidence_score, significance
                FROM intel_items i
                WHERE status = 'processed'
                  AND updated_at > NOW() - INTERVAL '24 hours'
                  AND NOT EXISTS (
                      SELECT 1 FROM alert_deliveries ad
                      WHERE ad.intel_item_id = i.id
                  )
                ORDER BY created_at ASC
                LIMIT 100
                """
            ),
        )
        rows = result.fetchall()

        if not rows:
            return

        items = [
            {
                "id": str(row[0]),
                "title": row[1],
                "content": row[2],
                "tags": row[3] or [],
                "primary_type": row[4],
                "url": row[5],
                "confidence_score": row[6],
                "significance": row[7],
            }
            for row in rows
        ]

        delivered = await check_and_deliver_alerts(session, redis_client, items)

    logger.info(
        "check_alerts_complete",
        items_checked=len(items),
        alerts_delivered=delivered,
    )
