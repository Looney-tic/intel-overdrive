"""GET /v1/diff — personalized delta feed filtered by user profile tag intersection."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import DiffResponse, DiffItemResponse
from src.models.models import APIKey, User

diff_router = APIRouter(tags=["diff"])

IMPACT_LABELS = {
    "breaking": "Breaking change: immediate action required",
    "major": "Major update: adoption recommended",
    "minor": "Minor change: low priority",
}


@diff_router.get("/diff", response_model=DiffResponse)
@limiter.limit("60/minute")
async def get_diff(
    request: Request,
    days: int = Query(7, ge=1, le=90, description="Fallback window if no cursor"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000_000),
    tag: Optional[str] = Query(
        None, description="Filter by tag (exact match in tags array)"
    ),
    group: Optional[str] = Query(
        None, description="Filter by tag group name (expands to all tags in that group)"
    ),
    significance: Optional[str] = Query(
        None,
        description="Filter by significance: breaking, major, minor, informational",
    ),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """
    Returns personalized delta feed — only items matching this API key's profile
    (tech_stack + skills intersection). Items are ordered by significance then
    relevance score. Empty profile returns a helpful message.

    Uses a per-key Redis cursor (diff_cursor:{key_id}) separate from the feed cursor.
    Falls back to `days` window if no cursor exists. Only advances cursor on non-empty results.

    Accepts optional tag, group, and significance filters to narrow results.
    """
    # 1. Fetch user profile
    user_query = select(User.profile).where(User.id == api_key.user_id)
    user_result = await session.execute(user_query)
    profile = user_result.scalar_one_or_none()

    # 2. Expand profile into interest tags (same logic as feed)
    from src.api.v1.feed import expand_profile_tags

    combined = expand_profile_tags(profile)
    profile_stack_size = len(combined)

    # Guard: empty profile — include profile_hint for onboarding
    if not combined:
        response = DiffResponse(
            items=[],
            total=0,
            offset=offset,
            limit=limit,
            profile_stack_size=0,
            message="Set profile via POST /v1/profile for personalized diff",
        )
        resp_dict = response.model_dump(mode="json")
        resp_dict["profile_hint"] = (
            "Set up your profile with POST /v1/profile to get personalized diffs. "
            "Include tools, providers, and interests in your profile."
        )
        return JSONResponse(content=resp_dict)

    # 4. Build cutoff: use Redis diff cursor if available (separate from feed cursor)
    redis = request.app.state.redis
    if redis is None:
        # Fall back to days-based window (no cursor tracking)
        raw_cursor = None
    else:
        cursor_key = f"diff_cursor:{api_key.id}"
        raw_cursor = await redis.get(cursor_key)
    if raw_cursor:
        raw_str = raw_cursor.decode() if isinstance(raw_cursor, bytes) else raw_cursor
        cutoff = datetime.fromisoformat(raw_str)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # 5. Build WHERE clauses dynamically for optional filters
    where_extra = ""
    extra_params: dict = {}

    if tag:
        where_extra += (
            "\n  AND CAST(tags AS jsonb) @> jsonb_build_array(CAST(:tag AS text))"
        )
        extra_params["tag"] = tag
    elif group:
        from src.api.v1.feed import TAG_GROUPS as FEED_TAG_GROUPS

        group_tags = FEED_TAG_GROUPS.get(group)
        if group_tags:
            where_extra += (
                "\n  AND EXISTS ("
                "\n      SELECT 1 FROM jsonb_array_elements_text(CAST(tags AS jsonb)) t"
                "\n      WHERE t = ANY(:group_tags)"
                "\n  )"
            )
            extra_params["group_tags"] = group_tags

    if significance:
        where_extra += "\n  AND significance = :significance"
        extra_params["significance"] = significance

    # 6. Execute SQL with EXISTS subquery for tag array intersection
    sql = text(
        f"""
        SELECT id, title, url, excerpt, summary, primary_type, tags,
               relevance_score, significance, source_name, published_at, created_at
        FROM intel_items
        WHERE status = 'processed'
          AND COALESCE(published_at, created_at) >= :cutoff
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements_text(CAST(tags AS jsonb)) AS tag
              WHERE tag = ANY(:stack_arr)
          ){where_extra}
        ORDER BY
          CASE significance
            WHEN 'breaking' THEN 0
            WHEN 'major' THEN 1
            WHEN 'minor' THEN 2
            ELSE 3
          END ASC,
          relevance_score DESC
        LIMIT :limit OFFSET :offset
        """
    )

    count_sql = text(
        f"""
        SELECT COUNT(*)
        FROM intel_items
        WHERE status = 'processed'
          AND COALESCE(published_at, created_at) >= :cutoff
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements_text(CAST(tags AS jsonb)) AS tag
              WHERE tag = ANY(:stack_arr)
          ){where_extra}
        """
    )

    base_params = {
        "cutoff": cutoff,
        "stack_arr": combined,
        **extra_params,
    }
    params = {**base_params, "limit": limit, "offset": offset}
    count_params = {**base_params}

    result = await session.execute(sql, params)
    rows = result.mappings().all()

    total_result = await session.execute(count_sql, count_params)
    total = total_result.scalar() or 0

    # 7. Advance Redis diff cursor only when results exist
    # Use max(created_at) from actual returned rows, not datetime.now(), to prevent
    # skipping items created between the SELECT and the cursor write.
    if total > 0 and rows and redis is not None:
        max_created = max(row["created_at"] for row in rows)
        if hasattr(max_created, "isoformat"):
            # +1 microsecond to prevent re-delivery (cutoff uses >=)
            cursor_ts = max_created + timedelta(microseconds=1)
            await redis.set(cursor_key, cursor_ts.isoformat(), ex=604800)

    # 8. Post-process: add impact_description
    items = []
    for row in rows:
        row_dict = dict(row)
        sig = row_dict.get("significance") or ""
        row_dict["impact_description"] = IMPACT_LABELS.get(sig, "Informational update")
        items.append(DiffItemResponse.model_validate(row_dict))

    response = DiffResponse(
        items=items,
        total=total,
        offset=offset,
        limit=limit,
        profile_stack_size=profile_stack_size,
    )
    return JSONResponse(content=response.model_dump(mode="json"))
