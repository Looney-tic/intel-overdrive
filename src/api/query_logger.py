"""Lightweight query logging — fire-and-forget INSERT, never fails the request."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import get_logger

logger = get_logger(__name__)


async def log_query(
    session: AsyncSession,
    api_key_id: int,
    query_type: str,
    query_text: str | None,
    result_count: int,
) -> None:
    """Log a query to the query_logs table. Swallows all exceptions."""
    try:
        await session.execute(
            text(
                "INSERT INTO query_logs (id, api_key_id, query_type, query_text, result_count, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :api_key_id, :query_type, :query_text, :result_count, NOW(), NOW())"
            ),
            {
                "api_key_id": api_key_id,
                "query_type": query_type,
                "query_text": query_text,
                "result_count": result_count,
            },
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.debug("query_log_failed", query_type=query_type, exc_info=True)
