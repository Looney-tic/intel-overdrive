"""Dead man's switch: Redis heartbeat + hourly Slack alert when pipeline goes silent.

OPS-05: Detects silent pipeline failures by checking the dms:last_ingestion key.
If the key is missing or older than DMS_THRESHOLD_HOURS, fires a Slack alert.
"""

from datetime import datetime, timezone

from src.core.config import get_settings
from src.core.logger import get_logger
from src.services.slack_delivery import deliver_slack_alert

logger = get_logger(__name__)

DMS_KEY = "dms:last_ingestion"
DMS_THRESHOLD_HOURS = 4


async def update_ingestion_heartbeat(redis_client) -> None:
    """Write current UTC timestamp to dms:last_ingestion with 48h TTL.

    Called from successful ingestion pollers (RSS, GitHub) as an accepted proxy
    for pipeline activity. If dispatch succeeds, the pipeline is alive.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    await redis_client.set(DMS_KEY, now_iso, ex=172800)
    logger.info("DMS_HEARTBEAT_UPDATED", key=DMS_KEY)


async def check_dead_mans_switch(ctx: dict) -> None:
    """ARQ cron function: fire Slack alert if no ingestion has run for >24h.

    Reads dms:last_ingestion from Redis. If missing, treats age as infinite.
    If present, parses ISO timestamp and computes age in hours.

    Always logs DMS_CHECK at INFO level with age_hours and threshold.
    Only fires Slack alert if SLACK_WEBHOOK_URL is configured.
    """
    redis_client = ctx["redis"]
    settings = get_settings()

    raw = await redis_client.get(DMS_KEY)

    if raw is None:
        age_hours = float("inf")
    else:
        # Decode bytes if needed
        ts_str = raw.decode() if isinstance(raw, bytes) else raw
        last_ts = datetime.fromisoformat(ts_str)
        # Handle naive timestamps (no tzinfo) — replace with UTC
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age_hours = (now - last_ts).total_seconds() / 3600

    logger.info(
        "DMS_CHECK",
        age_hours=round(age_hours, 2) if age_hours != float("inf") else "inf",
        threshold_hours=DMS_THRESHOLD_HOURS,
    )

    if age_hours > DMS_THRESHOLD_HOURS:
        webhook_url = settings.SLACK_WEBHOOK_URL
        if not webhook_url:
            logger.error("DMS_NO_WEBHOOK", age_hours=age_hours)
            return

        age_display = (
            f"{age_hours:.1f}h"
            if age_hours != float("inf")
            else "unknown (key missing)"
        )
        await deliver_slack_alert(
            webhook_url=webhook_url,
            item_title=f"Pipeline silent for {age_display} — no ingestion heartbeat",
            item_url="https://inteloverdrive.com/v1/health",
            item_type="ops",
            urgency="critical",
            tags=["ops", "dead-mans-switch"],
        )
