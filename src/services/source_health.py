from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.logger import get_logger
from src.models.models import Source
from src.services.slack_delivery import deliver_slack_alert

logger = get_logger(__name__)

MAX_CONSECUTIVE_ERRORS = 5
MAX_RECOVERY_ATTEMPTS = 3


async def is_source_on_cooldown(
    redis_client: object,
    source_id: str,
    poll_interval_seconds: int,
) -> bool:
    """Check and set cooldown for a source using Redis SET NX.

    Returns True if the source is on cooldown (key already existed, SET returned None).
    Returns False if the source is ready to poll (key was newly set).
    """
    result = await redis_client.set(  # type: ignore[attr-defined]
        f"source:cooldown:{source_id}",
        "1",
        ex=poll_interval_seconds,
        nx=True,
    )
    # SET NX returns None if the key already existed (on cooldown)
    # SET NX returns True if the key was newly created (ready to poll)
    return result is None


async def handle_source_error(
    session: AsyncSession,
    source: Source,
    exc: Exception,
    attempt: int = 1,
) -> None:
    """Increment consecutive_errors and mark source inactive at threshold.

    Only increments consecutive_errors on the first ARQ attempt (attempt == 1).
    On retries (attempt > 1), the error is logged but the counter is not
    incremented, preventing a single failure from triple-counting with max_tries=3.
    """
    # Ensure source is not expired after a potential rollback
    session.add(source)
    await session.refresh(source)

    if attempt <= 1:
        source.consecutive_errors += 1
    else:
        logger.info(
            "source_error_retry_skip_increment",
            source_id=source.id,
            attempt=attempt,
            error=str(exc),
        )
    source.last_fetched_at = datetime.now(timezone.utc)

    if source.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        source.is_active = False
        logger.warning(
            "source_marked_dead",
            source_id=source.id,
            consecutive_errors=source.consecutive_errors,
            error=str(exc),
        )
        # Alert operator about source death via Slack
        try:
            settings = get_settings()
            if settings.SLACK_WEBHOOK_URL:
                await deliver_slack_alert(
                    webhook_url=settings.SLACK_WEBHOOK_URL,
                    item_title=f"Source deactivated: {source.name} ({source.id})",
                    item_url=source.url,
                    item_type="ops",
                    urgency="important",
                    tags=["ops", "source-health", "deactivated"],
                )
        except Exception as alert_exc:
            logger.error("source_death_alert_failed", error=str(alert_exc))
    else:
        logger.info(
            "source_error_recorded",
            source_id=source.id,
            consecutive_errors=source.consecutive_errors,
            error=str(exc),
        )

    await session.commit()


async def handle_source_success(
    session: AsyncSession,
    source: Source,
    new_etag: str | None = None,
    new_last_modified: str | None = None,
) -> None:
    """Reset consecutive_errors and update health tracking on successful fetch."""
    source.consecutive_errors = 0
    source.last_successful_poll = datetime.now(timezone.utc)
    source.last_fetched_at = datetime.now(timezone.utc)

    if new_etag is not None:
        source.last_etag = new_etag
    if new_last_modified is not None:
        source.last_modified_header = new_last_modified

    await session.commit()


async def check_source_recovery(session: AsyncSession) -> int:
    """Re-enable deactivated sources that have been down for 48+ hours.

    Gives deactivated sources a second chance by resetting is_active=True
    and consecutive_errors=0 if last_fetched_at is older than 48 hours.
    Sources that have already been recovered MAX_RECOVERY_ATTEMPTS times
    are permanently deactivated and skipped.
    Returns the number of sources recovered.
    """
    from sqlalchemy import text as sa_text

    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    # Log permanently dead sources (recovery_attempts >= MAX_RECOVERY_ATTEMPTS)
    permanently_dead = await session.execute(
        sa_text(
            """
            SELECT id, name, recovery_attempts FROM sources
            WHERE is_active = false
              AND recovery_attempts >= :max_attempts
            """
        ),
        {"max_attempts": MAX_RECOVERY_ATTEMPTS},
    )
    dead_rows = permanently_dead.fetchall()
    for row in dead_rows:
        logger.info(
            "source_permanently_dead",
            source_id=row[0],
            source_name=row[1],
            recovery_attempts=row[2],
        )

    # Recover eligible sources (under the recovery attempt limit)
    result = await session.execute(
        sa_text(
            """
            UPDATE sources
            SET is_active = true,
                consecutive_errors = 0,
                recovery_attempts = recovery_attempts + 1,
                updated_at = NOW()
            WHERE is_active = false
              AND last_fetched_at < :cutoff
              AND recovery_attempts < :max_attempts
            RETURNING id, name, recovery_attempts
            """
        ),
        {"cutoff": cutoff, "max_attempts": MAX_RECOVERY_ATTEMPTS},
    )
    recovered = result.fetchall()
    if recovered:
        await session.commit()
        for row in recovered:
            logger.info(
                "source_recovered",
                source_id=row[0],
                source_name=row[1],
                recovery_attempts=row[2],
            )
    return len(recovered)
