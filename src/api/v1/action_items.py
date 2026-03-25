"""GET /v1/action-items — top items requiring attention, ranked by significance and recency."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from typing import List

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import IntelItemResponse
from src.api.search_utils import collapse_clusters
from src.models.models import APIKey, User

action_items_router = APIRouter(tags=["action-items"])


@action_items_router.get("/action-items")
@limiter.limit("60/minute")
async def get_action_items(
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """
    Returns top 3-5 items requiring attention today: breaking/major significance items
    from the last 7 days that haven't been read, acted_on, or dismissed by this API key.

    Items are ranked by significance (breaking first) then relevance_score.
    If the user has a profile, applies the same tag intersection filter as /diff.
    """
    # 1. Fetch user profile for optional tag intersection
    from src.api.v1.feed import expand_profile_tags

    user_query = select(User.profile).where(User.id == api_key.user_id)
    user_result = await session.execute(user_query)
    profile = user_result.scalar_one_or_none()

    # Determine if we apply profile filter (expanded tags include tools + providers)
    stack_filter = ""
    stack_params: dict = {}
    combined = expand_profile_tags(profile)
    if combined:
        stack_filter = """
  AND EXISTS (
      SELECT 1 FROM jsonb_array_elements_text(CAST(tags AS jsonb)) AS tag
      WHERE tag = ANY(:stack_arr)
  )"""
        stack_params["stack_arr"] = combined

    # 2. Query: breaking/major items in last 7 days, not read/acted_on/dismissed
    sql = text(
        f"""
        SELECT i.id, i.source_id, i.url, i.title, i.excerpt, i.summary,
               i.primary_type, i.tags, i.significance, i.relevance_score,
               i.quality_score, i.quality_score_details, i.confidence_score,
               i.status, i.created_at, i.published_at, i.cluster_id,
               i.contrarian_signals,
               s.name AS source_name
        FROM intel_items i
        LEFT JOIN sources s ON i.source_id = CAST(s.id AS text)
        WHERE i.status = 'processed'
          AND COALESCE(i.published_at, i.created_at) >= NOW() - INTERVAL '7 days'
          AND i.significance IN ('breaking', 'major')
          AND NOT EXISTS (
              SELECT 1 FROM item_signals sig
              WHERE sig.item_id = i.id
                AND sig.api_key_id = :key_id
                AND sig.action IN ('read', 'acted_on', 'dismiss')
          ){stack_filter}
        ORDER BY
          CASE i.significance
            WHEN 'breaking' THEN 0
            WHEN 'major' THEN 1
            ELSE 2
          END ASC,
          i.relevance_score DESC
        LIMIT 5
        """
    )

    params = {"key_id": api_key.id, **stack_params}
    result = await session.execute(sql, params)
    rows = result.mappings().all()

    # Collapse cluster duplicates — same incident from multiple sources collapses to best
    rows = collapse_clusters(rows, rank_key="relevance_score")

    items = []
    for row in rows:
        row_dict = dict(row)
        items.append(IntelItemResponse.model_validate(row_dict))

    total = len(items)
    if total == 0:
        message = "No items require attention — you're caught up!"
    elif total == 1:
        message = "1 item needs your attention"
    else:
        message = f"{total} items need your attention"

    return JSONResponse(
        content={
            "action_items": [item.model_dump(mode="json") for item in items],
            "total": total,
            "message": message,
        }
    )
