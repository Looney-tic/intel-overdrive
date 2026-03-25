"""GET /v1/digest — grouped summary of recent intel items by primary_type."""
from collections import defaultdict

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import DigestGroupResponse, DigestResponse, IntelItemResponse
from src.models.models import APIKey

digest_router = APIRouter(tags=["digest"])


@digest_router.get("/digest", response_model=DigestResponse)
@limiter.limit("100/minute")
async def get_digest(
    request: Request,
    response: Response,
    days: int = Query(7, ge=1, le=90, description="Recency window in days"),
    per_group: int = Query(10, ge=1, le=50, description="Max items per group"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> DigestResponse:
    """
    Returns processed items grouped by primary_type, top N per group by relevance_score,
    for the past `days` days.
    """
    digest_sql = text(
        """
        SELECT * FROM (
            SELECT id, title, url, excerpt, summary, primary_type, tags,
                   relevance_score, quality_score, quality_score_details,
                   confidence_score, significance, status, created_at,
                   source_id, source_name, published_at, cluster_id, contrarian_signals,
                   ROW_NUMBER() OVER (
                       PARTITION BY primary_type
                       ORDER BY relevance_score DESC
                   ) AS rn
            FROM intel_items
            WHERE status = 'processed'
              AND created_at >= NOW() - INTERVAL '1 day' * :days
        ) ranked
        WHERE rn <= :per_group
        """
    )
    result = await session.execute(digest_sql, {"days": days, "per_group": per_group})
    rows = result.mappings().all()

    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[row["primary_type"]].append(row)

    group_list = [
        DigestGroupResponse(
            primary_type=ptype,
            count=len(items),
            items=[IntelItemResponse.model_validate(dict(i)) for i in items],
        )
        for ptype, items in sorted(groups.items())
    ]
    total = sum(g.count for g in group_list)

    result_obj = DigestResponse(days=days, groups=group_list, total=total)
    return JSONResponse(content=result_obj.model_dump(mode="json"))
