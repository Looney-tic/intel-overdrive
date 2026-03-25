"""GET /v1/trends — tag velocity analysis using two-window item count comparison."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import TrendItem, TrendsResponse
from src.models.models import APIKey

trends_router = APIRouter(tags=["trends"])


def label_velocity(ratio: float | None) -> str:
    """Compute velocity label from window ratio."""
    if ratio is None:
        return "emerging"  # new tag with no prior window data
    if ratio > 1.5:
        return "accelerating"
    if ratio < 0.67:
        return "declining"
    return "plateauing"


@trends_router.get("/trends", response_model=TrendsResponse)
@limiter.limit("60/minute")
async def get_trends(
    request: Request,
    response: Response,
    days: int = Query(
        14,
        ge=7,
        le=90,
        description="Total window in days (split in half for comparison)",
    ),
    min_count: int = Query(3, ge=1, le=100, description="Min items per tag to include"),
    limit: int = Query(20, ge=1, le=100, description="Max tags to return"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> TrendsResponse:
    """
    Returns tags with velocity labels based on item count change across two equal time windows.

    window_1 = most recent days/2 (e.g. last 7 of 14)
    window_2 = preceding days/2 (e.g. days 8-14 of 14)
    velocity_ratio = window_1_count / window_2_count
    label: accelerating (>1.5), plateauing (0.67-1.5), declining (<0.67), emerging (no prior data)
    """
    trends_sql = text(
        """
        WITH tag_counts AS (
            SELECT
                tag,
                COUNT(*) FILTER (WHERE COALESCE(i.published_at, i.created_at) >= NOW() - INTERVAL '1 day' * (:days / 2.0))
                    AS window_1_count,
                COUNT(*) FILTER (
                    WHERE COALESCE(i.published_at, i.created_at) <  NOW() - INTERVAL '1 day' * (:days / 2.0)
                      AND COALESCE(i.published_at, i.created_at) >= NOW() - INTERVAL '1 day' * :days
                ) AS window_2_count,
                COUNT(*) AS total_count,
                COUNT(DISTINCT i.source_id) AS source_count
            FROM intel_items i,
                 jsonb_array_elements_text(CAST(i.tags AS jsonb)) AS tag
            WHERE i.status = 'processed'
              AND COALESCE(i.published_at, i.created_at) >= NOW() - INTERVAL '1 day' * :days
            GROUP BY tag
            HAVING COUNT(*) >= :min_count
        )
        SELECT
            tag,
            window_1_count,
            window_2_count,
            total_count,
            source_count,
            CASE
                WHEN window_2_count = 0 AND window_1_count > 0 THEN 99.0
                WHEN window_2_count = 0 THEN NULL
                ELSE ROUND((window_1_count::numeric / window_2_count), 2)
            END AS velocity_ratio
        FROM tag_counts
        ORDER BY
            -- Diversity-weighted velocity: penalise single-source tags
            -- Tags from 5+ sources get full weight; 1-source tags get 20%
            CASE
                WHEN window_2_count = 0 AND window_1_count > 0 THEN 99.0
                WHEN window_2_count = 0 THEN -1
                ELSE (window_1_count::numeric / window_2_count)
            END
            * (LEAST(source_count, 5)::numeric / 5.0)
            DESC NULLS LAST
        LIMIT :limit
        """
    )

    result = await session.execute(
        trends_sql,
        {"days": days, "min_count": min_count, "limit": limit},
    )
    rows = result.mappings().all()

    half = days // 2
    trends = [
        TrendItem(
            tag=row["tag"],
            window_1_count=row["window_1_count"],
            window_2_count=row["window_2_count"],
            velocity_ratio=float(row["velocity_ratio"])
            if row["velocity_ratio"] is not None
            else None,
            velocity_label=label_velocity(
                float(row["velocity_ratio"])
                if row["velocity_ratio"] is not None
                else None
            ),
            total_count=row["total_count"],
            source_count=row["source_count"],
        )
        for row in rows
    ]

    result_obj = TrendsResponse(
        window_days=days,
        window_1_label=f"last {half} days",
        window_2_label=f"days {half + 1}-{days}",
        trends=trends,
        total=len(trends),
    )
    response_dict = result_obj.model_dump(mode="json")

    # Insufficient data: when ALL trends have window_2_count=0, the system
    # was recently deployed and velocity ratios are meaningless (all 99.0).
    # Return empty trends with a clear message instead of fake-looking data.
    if trends and all(t.window_2_count == 0 for t in trends):
        available_after = datetime.now(timezone.utc) + timedelta(days=days)
        available_after_date = available_after.strftime("%Y-%m-%d")
        return JSONResponse(
            content={
                "trends": [],
                "message": (
                    f"Trend data requires {days}+ days of history. "
                    f"Current deployment is too recent for reliable velocity calculations. "
                    f"Check back after {available_after_date}."
                ),
                "available_after": available_after.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).isoformat(),
            }
        )

    return JSONResponse(content=response_dict)
