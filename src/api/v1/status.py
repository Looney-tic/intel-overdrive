from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from src.models.models import Source, IntelItem, APIKey
from src.api.deps import get_session, require_api_key
from src.api.schemas import StatusSummaryResponse
from src.services.spend_tracker import SpendTracker
from src.api.limiter import limiter

status_router = APIRouter(tags=["status"])


@status_router.get("/status", response_model=StatusSummaryResponse)
@limiter.limit("60/minute")
async def get_system_status(
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns pipeline status summary with source counts and remaining daily spend.
    Returns <5KB summary instead of full source list (was 114KB).
    Requires authentication.
    """
    # 1. Get source summary counts in a single query
    summary_query = select(
        func.count().label("total_sources"),
        func.count().filter(Source.is_active.is_(True)).label("active_sources"),
        func.count().filter(Source.consecutive_errors > 0).label("erroring_sources"),
    )
    summary_result = await session.execute(summary_query)
    summary_row = summary_result.one()

    total_sources = summary_row.total_sources
    active_sources = summary_row.active_sources
    erroring_sources = summary_row.erroring_sources

    # 2. Get source type counts
    type_query = select(Source.type, func.count().label("count")).group_by(Source.type)
    type_result = await session.execute(type_query)
    source_type_counts = {row.type: row.count for row in type_result}

    # 3. Get spend remaining (gracefully handle missing Redis)
    redis_client = getattr(request.app.state, "redis", None)
    remaining_spend = -1.0
    if redis_client is not None:
        tracker = SpendTracker(redis_client)
        remaining_spend = await tracker.get_remaining_spend()

    # 4. Determine pipeline health (latest processed item)
    health_query = select(func.max(IntelItem.created_at)).where(
        IntelItem.status == "processed"
    )
    health_result = await session.execute(health_query)
    last_ingestion = health_result.scalar()

    # H-1: Guard against timezone-naive datetime from DB
    if last_ingestion is not None and last_ingestion.tzinfo is None:
        last_ingestion = last_ingestion.replace(tzinfo=timezone.utc)

    pipeline_health = "healthy"
    if last_ingestion is None or last_ingestion < datetime.now(
        timezone.utc
    ) - timedelta(hours=24):
        pipeline_health = "degraded"

    status_response = StatusSummaryResponse(
        total_sources=total_sources,
        active_sources=active_sources,
        erroring_sources=erroring_sources,
        source_type_counts=source_type_counts,
        daily_spend_remaining=remaining_spend,
        pipeline_health=pipeline_health,
    )

    return JSONResponse(content=status_response.model_dump(mode="json"))
