"""GET /v1/landscape/{domain} — competitive landscape mapping for a tag domain."""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import LandscapeItem, LandscapeResponse
from src.models.models import APIKey

landscape_router = APIRouter(tags=["landscape"])

# All known primary_type values in the ecosystem
ALL_TYPES: set[str] = {"skill", "tool", "update", "practice", "docs"}


@landscape_router.get("/landscape/{domain}", response_model=LandscapeResponse)
@limiter.limit("60/minute")
async def get_landscape(
    domain: str,
    request: Request,
    response: Response,
    days: int = Query(
        30, ge=7, le=365, description="Recency window in days for momentum calculation"
    ),
    limit: int = Query(20, ge=1, le=100, description="Max momentum leaders to return"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> LandscapeResponse:
    """
    Returns a structured competitive landscape for a tag domain (e.g. 'mcp', 'agents').

    momentum_leaders: top items by momentum_weight (recent items count double)
    positioning: distribution of primary_type across domain
    gaps: types absent from this domain (compared to all known types)
    """
    # SQL for momentum leaders — group by title/url, weight recent items 2x and by significance
    leaders_sql = text(
        """
        SELECT
            title,
            url,
            primary_type,
            source_name,
            MAX(relevance_score)    AS relevance_score,
            MAX(significance)       AS significance,
            COUNT(*)                AS item_count,
            SUM(
                CASE WHEN created_at >= NOW() - INTERVAL '7 days' THEN 2 ELSE 1 END
                *
                CASE significance
                    WHEN 'breaking' THEN 8
                    WHEN 'major' THEN 4
                    WHEN 'minor' THEN 1
                    ELSE 1
                END
            )                       AS momentum_weight
        FROM intel_items
        WHERE status = 'processed'
          AND created_at >= NOW() - INTERVAL '1 day' * :days
          AND CAST(tags AS jsonb) @> jsonb_build_array(CAST(:domain AS text))
        GROUP BY title, url, primary_type, source_name
        ORDER BY momentum_weight DESC, relevance_score DESC
        LIMIT :limit
        """
    )

    # SQL for type distribution (positioning)
    positioning_sql = text(
        """
        SELECT primary_type, COUNT(*) AS cnt
        FROM intel_items
        WHERE status = 'processed'
          AND created_at >= NOW() - INTERVAL '1 day' * :days
          AND CAST(tags AS jsonb) @> jsonb_build_array(CAST(:domain AS text))
        GROUP BY primary_type
        """
    )

    # Total item count for the domain
    total_sql = text(
        """
        SELECT COUNT(*) AS total
        FROM intel_items
        WHERE status = 'processed'
          AND created_at >= NOW() - INTERVAL '1 day' * :days
          AND CAST(tags AS jsonb) @> jsonb_build_array(CAST(:domain AS text))
        """
    )

    params = {"days": days, "domain": domain}

    leaders_result = await session.execute(leaders_sql, {**params, "limit": limit})
    leaders_rows = leaders_result.mappings().all()

    positioning_result = await session.execute(positioning_sql, params)
    positioning_rows = positioning_result.mappings().all()

    total_result = await session.execute(total_sql, params)
    total_count = total_result.scalar() or 0

    # Build positioning dict
    positioning: dict[str, int] = {
        row["primary_type"]: row["cnt"] for row in positioning_rows
    }

    # Gap analysis: types missing from this domain
    present_types = set(positioning.keys())
    gaps = sorted(ALL_TYPES - present_types)

    # Build momentum leaders list
    momentum_leaders = [
        LandscapeItem(
            title=row["title"],
            url=row["url"],
            primary_type=row["primary_type"],
            relevance_score=float(row["relevance_score"]),
            significance=row["significance"],
            source_name=row["source_name"],
            item_count=row["item_count"],
        )
        for row in leaders_rows
    ]

    result_obj = LandscapeResponse(
        domain=domain,
        total_items=total_count,
        momentum_leaders=momentum_leaders,
        positioning=positioning,
        gaps=gaps,
        window_days=days,
    )
    return JSONResponse(content=result_obj.model_dump(mode="json"))
