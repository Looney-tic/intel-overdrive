"""SLA endpoint — pipeline freshness metrics for INTEL-11."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import SLAResponse
from src.models.models import APIKey

sla_router = APIRouter(tags=["sla"])


@sla_router.get("/sla", response_model=SLAResponse)
@limiter.limit("60/minute")
async def get_sla(
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns pipeline freshness metrics and source health summary.

    All values are pure SQL aggregations — zero LLM cost.
    Use this endpoint to verify pipeline health before consuming the feed.
    """
    # 1. newest_item_age_hours: age of the newest processed item (None if no processed items)
    # SQL computes NOW() - MAX(created_at) which gives age of the NEWEST (most recent) item.
    age_result = await session.execute(
        text(
            "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(created_at)))/3600 "
            "FROM intel_items WHERE status='processed'"
        )
    )
    newest_item_age_hours: Optional[float] = age_result.scalar()

    # 2. pipeline_lag_seconds: P50 (median) age of items in the pipeline queue.
    # Uses percentile_cont(0.5) instead of MAX to avoid a single stuck old item
    # from dominating the metric and making the pipeline appear broken.
    lag_result = await session.execute(
        text(
            "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (NOW() - created_at))) "
            "FROM intel_items WHERE status IN ('raw','embedded','queued','processing')"
        )
    )
    pipeline_lag_seconds: Optional[float] = lag_result.scalar()

    # 3. items_last_24h: processed items in the last 24 hours
    count_24h_result = await session.execute(
        text(
            "SELECT COUNT(*) FROM intel_items "
            "WHERE status='processed' AND created_at >= NOW() - INTERVAL '24 hours'"
        )
    )
    items_last_24h: int = count_24h_result.scalar() or 0

    # 4. items_last_7d: processed items in the last 7 days
    count_7d_result = await session.execute(
        text(
            "SELECT COUNT(*) FROM intel_items "
            "WHERE status='processed' AND created_at >= NOW() - INTERVAL '7 days'"
        )
    )
    items_last_7d: int = count_7d_result.scalar() or 0

    # 5. failed_items_last_24h: items that failed classification in the last 24 hours
    failed_result = await session.execute(
        text(
            "SELECT COUNT(*) FROM intel_items "
            "WHERE status = 'failed' AND created_at >= NOW() - INTERVAL '24 hours'"
        )
    )
    failed_items_last_24h: int = failed_result.scalar() or 0

    # 6. credits_exhausted: check Redis key set by pipeline worker on APICreditsExhausted
    redis = getattr(request.app.state, "redis", None)
    credits_exhausted: bool = False
    if redis is not None:
        credits_exhausted = (await redis.exists("credits:exhausted")) > 0

    # 7. source_health_summary: aggregated source health by error state
    health_result = await session.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (WHERE is_active AND consecutive_errors = 0) AS healthy,
                COUNT(*) FILTER (WHERE consecutive_errors >= 3) AS degraded,
                COUNT(*) FILTER (WHERE NOT is_active) AS dead,
                COUNT(*) AS total
            FROM sources
            """
        )
    )
    health_row = health_result.fetchone()
    if health_row:
        healthy = int(health_row[0] or 0)
        degraded = int(health_row[1] or 0)
        dead = int(health_row[2] or 0)
        total = int(health_row[3] or 0)
    else:
        healthy = degraded = dead = total = 0

    source_health_summary = {
        "healthy": healthy,
        "degraded": degraded,
        "dead": dead,
        "total": total,
    }

    # 8. coverage_score: healthy sources / total sources (0.0 if no sources)
    coverage_score: float = (healthy / total) if total > 0 else 0.0

    # 9. freshness_guarantee: static contract — pipeline cron runs hourly; worst case 24h gap
    freshness_guarantee = "24h"

    response = SLAResponse(
        newest_item_age_hours=newest_item_age_hours,
        pipeline_lag_seconds=pipeline_lag_seconds,
        items_last_24h=items_last_24h,
        items_last_7d=items_last_7d,
        failed_items_last_24h=failed_items_last_24h,
        credits_exhausted=credits_exhausted,
        coverage_score=coverage_score,
        source_health_summary=source_health_summary,
        freshness_guarantee=freshness_guarantee,
        checked_at=datetime.now(timezone.utc),
    )

    return JSONResponse(content=response.model_dump(mode="json"))
