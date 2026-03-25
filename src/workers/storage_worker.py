"""
Storage management worker — reclaims pgvector storage by NULLing embeddings
on filtered items.

Filtered items already went through the relevance gate and failed, so they
don't need embeddings. NULLing them reclaims vector storage (each 1024-dim
float32 vector is ~4 KB).

Runs daily at 4:00am UTC via SlowWorkerSettings cron.
"""

from sqlalchemy import text

import src.core.init_db as _init_db
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)

# Process in batches to avoid long-running transactions
CLEANUP_BATCH_SIZE = 5000

# Each 1024-dim float32 vector is ~4 KB (1024 * 4 bytes)
BYTES_PER_VECTOR = 1024 * 4


async def cleanup_filtered_embeddings(ctx: dict) -> None:
    """NULL embeddings on filtered items older than MIN_AGE_HOURS.

    Dedup guard: only targets items where embedding IS NOT NULL AND
    status = 'filtered', so already-NULLed items are naturally skipped.

    Does NOT delete any items — only NULLs their embedding column.
    """
    settings = get_settings()

    if not settings.STORAGE_CLEANUP_ENABLED:
        logger.info("storage_cleanup_disabled")
        return

    if _init_db.async_session_factory is None:
        logger.error("storage_cleanup_called_before_db_init")
        return

    min_age_hours = settings.STORAGE_CLEANUP_MIN_AGE_HOURS
    total_nulled = 0

    while True:
        async with _init_db.async_session_factory() as session:
            # Select a batch of filtered items with non-NULL embeddings
            # older than the configured minimum age.
            # The WHERE clause IS the dedup guard: embedding IS NOT NULL
            # means already-NULLed items are never selected.
            result = await session.execute(
                text(
                    """
                    SELECT id
                    FROM intel_items
                    WHERE status = 'filtered'
                      AND embedding IS NOT NULL
                      AND updated_at < NOW() - MAKE_INTERVAL(hours => :hours)
                    LIMIT :batch_size
                    FOR UPDATE SKIP LOCKED
                """
                ),
                {"hours": min_age_hours, "batch_size": CLEANUP_BATCH_SIZE},
            )
            rows = result.fetchall()

            if not rows:
                break

            ids = [row[0] for row in rows]

            await session.execute(
                text(
                    """
                    UPDATE intel_items
                    SET embedding = NULL,
                        updated_at = NOW()
                    WHERE id = ANY(:ids)
                """
                ),
                {"ids": ids},
            )
            await session.commit()

            total_nulled += len(ids)
            logger.info(
                "storage_cleanup_batch",
                batch_size=len(ids),
                running_total=total_nulled,
            )

    if total_nulled == 0:
        logger.info("storage_cleanup_nothing_to_do")
        return

    estimated_mb = round(total_nulled * BYTES_PER_VECTOR / (1024 * 1024), 2)

    logger.info(
        "storage_cleanup_complete",
        items_nulled=total_nulled,
        estimated_mb_saved=estimated_mb,
    )

    # Send stats to Slack if webhook is configured
    if settings.SLACK_WEBHOOK_URL:
        try:
            from src.services.slack_delivery import deliver_slack_alert

            await deliver_slack_alert(
                webhook_url=settings.SLACK_WEBHOOK_URL,
                item_title=(
                    f"Storage cleanup: NULLed {total_nulled} filtered embeddings, "
                    f"~{estimated_mb} MB reclaimed"
                ),
                item_url="",
                item_type="ops",
                urgency="interesting",
                tags=["ops", "storage", "cleanup"],
            )
        except Exception as exc:
            logger.error("storage_cleanup_slack_failed", error=str(exc))
