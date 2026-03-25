"""Source recovery worker: re-enable deactivated sources after 48h cooldown.

Cron-driven ARQ slow-queue worker that gives deactivated sources a second
chance after 48 hours. Runs every 12 hours.
"""

import src.core.init_db as _db
from src.core.logger import get_logger
from src.services.source_health import check_source_recovery

logger = get_logger(__name__)


async def check_source_recovery_cron(ctx: dict) -> None:
    """Slow queue cron: re-enable deactivated sources after 48h."""
    if _db.async_session_factory is None:
        logger.error("check_source_recovery_called_before_db_init")
        return

    async with _db.async_session_factory() as session:
        recovered = await check_source_recovery(session)

    logger.info("check_source_recovery_complete", recovered=recovered)
