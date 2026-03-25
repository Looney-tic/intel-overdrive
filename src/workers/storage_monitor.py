"""
Storage monitor — checks DB size every 6 hours and sends Slack alerts
when approaching Neon free tier limits.

Thresholds (configurable via env):
- STORAGE_WARN_PCT (default 80%): Slack "important" alert
- STORAGE_CRITICAL_PCT (default 90%): Slack "critical" alert
- Below warn: info log only

Runs every 6 hours via SlowWorkerSettings cron, offset from DMS.
"""

from sqlalchemy import text

import src.core.init_db as _init_db
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)


def _format_size(bytes_val: int) -> str:
    """Format bytes as human-readable MB string."""
    return f"{bytes_val / (1024 * 1024):.1f} MB"


def _format_table_report(tables: list[tuple[str, int]]) -> str:
    """Format top tables as a readable report string."""
    lines = []
    for name, size_bytes in tables:
        lines.append(f"  - {name}: {_format_size(size_bytes)}")
    return "\n".join(lines)


async def check_storage(ctx: dict) -> None:
    """Check database size and alert if approaching storage limits.

    Queries pg_database_size and top tables, compares against configurable
    thresholds, and sends Slack alerts when warn/critical levels are reached.
    """
    settings = get_settings()

    if _init_db.async_session_factory is None:
        logger.error("storage_monitor_called_before_db_init")
        return

    limit_bytes = settings.STORAGE_LIMIT_MB * 1024 * 1024

    async with _init_db.async_session_factory() as session:
        # Current database size
        result = await session.execute(
            text("SELECT pg_database_size(current_database())")
        )
        current_bytes = result.scalar_one()

        # Top 5 tables by size
        result = await session.execute(
            text(
                """
                SELECT relname, pg_total_relation_size(relid)
                FROM pg_catalog.pg_statio_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
                LIMIT 5
                """
            )
        )
        top_tables = [(row[0], row[1]) for row in result.fetchall()]

        # Growth estimation: items in last 7 days vs total
        result = await session.execute(
            text(
                """
                SELECT
                    COUNT(*) AS total_items,
                    COUNT(*) FILTER (
                        WHERE created_at > NOW() - INTERVAL '7 days'
                    ) AS recent_items
                FROM intel_items
                """
            )
        )
        row = result.fetchone()
        total_items = row[0] if row else 0
        recent_items = row[1] if row else 0

    # Calculate percentage
    pct = (current_bytes / limit_bytes) * 100 if limit_bytes > 0 else 0
    current_mb = current_bytes / (1024 * 1024)
    remaining_bytes = limit_bytes - current_bytes

    # Estimate days until full
    days_until_full = None
    if total_items > 0 and recent_items > 0:
        bytes_per_item = current_bytes / total_items
        weekly_growth_bytes = recent_items * bytes_per_item
        daily_growth_bytes = weekly_growth_bytes / 7
        if daily_growth_bytes > 0 and remaining_bytes > 0:
            days_until_full = int(remaining_bytes / daily_growth_bytes)

    table_report = _format_table_report(top_tables)
    growth_str = (
        f"~{days_until_full} days until full"
        if days_until_full is not None
        else "insufficient data for growth estimate"
    )

    # Determine alert level
    if pct >= settings.STORAGE_CRITICAL_PCT:
        level = "critical"
    elif pct >= settings.STORAGE_WARN_PCT:
        level = "warn"
    else:
        level = "ok"

    # Always log
    logger.info(
        "storage_check",
        size_mb=round(current_mb, 1),
        limit_mb=settings.STORAGE_LIMIT_MB,
        pct=round(pct, 1),
        level=level,
        total_items=total_items,
        recent_7d_items=recent_items,
        days_until_full=days_until_full,
    )

    # Send Slack alert if at warn or critical
    if level == "ok":
        return

    if not settings.SLACK_WEBHOOK_URL:
        logger.warning(
            "storage_alert_skipped_no_webhook", level=level, pct=round(pct, 1)
        )
        return

    urgency = "critical" if level == "critical" else "important"

    alert_title = (
        f"Storage {level.upper()}: {current_mb:.0f} MB / {settings.STORAGE_LIMIT_MB} MB "
        f"({pct:.0f}%)"
    )
    # Build detailed message for Slack — item_url carries the detail text
    detail_lines = [
        f"Usage: {current_mb:.0f} MB / {settings.STORAGE_LIMIT_MB} MB ({pct:.0f}%)",
        f"Growth: {recent_items} items in last 7 days, {growth_str}",
        "",
        "Top tables:",
        table_report,
    ]

    try:
        from src.services.slack_delivery import deliver_slack_alert

        await deliver_slack_alert(
            webhook_url=settings.SLACK_WEBHOOK_URL,
            item_title=alert_title,
            item_url="",
            item_type="ops",
            urgency=urgency,
            tags=["ops", "storage", "monitoring"],
        )
        logger.info("storage_alert_sent", level=level, urgency=urgency)
    except Exception as exc:
        logger.error("storage_alert_failed", error=str(exc))
