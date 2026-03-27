import json as _json_mod
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, and_, or_, not_, cast, text, bindparam
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import IntelItem, APIKey, User
from src.api.deps import get_session, require_api_key
from src.api.schemas import FeedResponse, IntelItemResponse
from src.api.limiter import limiter
from src.api.query_logger import log_query
from src.api.cache import (
    make_cache_key,
    get_cached_response,
    set_cached_response,
    is_cache_enabled,
    get_redis_from_request,
)
from src.api.search_utils import collapse_clusters
from src.api.v1.similar import _embed_concept
from src.core.logger import get_logger

logger = get_logger(__name__)

feed_router = APIRouter(tags=["feed"])

# Tag groups — semantically related tag clusters for browsing by category.
# Also used by GET /v1/tag-groups in meta.py (imported from here).
TAG_GROUPS: dict[str, list[str]] = {
    "browser-automation": [
        "puppeteer",
        "playwright",
        "chrome",
        "selenium",
        "browser-automation",
        "browsermcp",
    ],
    "database": ["postgres", "supabase", "neon", "database", "sql", "prisma"],
    "ai-agents": [
        "agents",
        "multi-agent",
        "ai-agents",
        "orchestration",
        "agentic-design",
        "workflow-automation",
    ],
    "mcp": ["mcp", "mcp-server", "model-context-protocol", "tools", "plugin"],
    "claude-code": ["claude-code", "claude", "anthropic", "skills"],
    "devops": ["docker", "deployment", "monitoring", "ci-cd", "infra"],
    "security": ["security", "auth", "encryption", "permissions"],
    "testing": ["testing", "tdd", "test-automation", "pytest"],
    "api-development": ["api", "api-integration", "rest", "fastapi", "server"],
    "documentation": ["documentation", "docs", "readme"],
    "rag-embeddings": [
        "rag",
        "embeddings",
        "embedding",
        "vector-database",
        "vector-store",
        "pinecone",
        "weaviate",
        "chroma",
        "qdrant",
        "llamaindex",
        "retrieval-augmented-generation",
    ],
}

# Skill tag expansion — maps skill name to related tags used for feed boosting.
# Extracted to module level so profile.py can import it for validation.
SKILL_TAG_EXPANSION: dict[str, set[str]] = {
    "agentic-engineering": {
        "multi-agent",
        "agents",
        "ai-agents",
        "orchestration",
        "workflow-automation",
        "agentic-design",
    },
    "plugin-development": {"plugin", "hooks", "skills", "mcp", "extension"},
    "multi-agent-orchestration": {
        "multi-agent",
        "orchestration",
        "agents",
        "ai-agents",
        "coordination",
    },
    "pipeline-design": {
        "pipeline",
        "workflow",
        "automation",
        "workflow-automation",
    },
    "browser-automation": {
        "browser-automation",
        "puppeteer",
        "playwright",
        "chrome",
        "selenium",
    },
    "api-development": {"api", "api-integration", "rest", "fastapi", "server"},
    "devops": {"docker", "deployment", "monitoring", "ci-cd"},
    "security": {"security", "auth", "encryption"},
    "testing": {"testing", "tdd", "test-automation"},
    "documentation": {"documentation", "docs", "readme"},
}

# Tool tag expansion — maps IDE/agent name to related tags used for feed boosting.
TOOL_TAG_EXPANSION: dict[str, set[str]] = {
    "claude-code": {
        "claude-code",
        "claude",
        "anthropic",
        "hooks",
        "skills",
        "sub-agents",
        "mcp",
    },
    "cursor": {"cursor", "cursor-rules", "copilot", "ai-ide"},
    "cline": {"cline", "vscode", "ai-ide"},
    "aider": {"aider", "ai-coding", "cli"},
    "copilot": {"copilot", "github-copilot", "ai-ide"},
    "windsurf": {"windsurf", "codeium", "ai-ide"},
}

# Provider tag expansion — maps LLM provider name to related tags.
PROVIDER_TAG_EXPANSION: dict[str, set[str]] = {
    "anthropic": {"anthropic", "claude", "haiku", "sonnet", "opus"},
    "openai": {"openai", "gpt", "chatgpt", "o1", "o3"},
    "google": {"google", "gemini", "gemma", "vertex"},
    "mistral": {"mistral", "codestral"},
    "meta": {"meta", "llama"},
}


def expand_profile_tags(profile: dict | None) -> list[str]:
    """Expand a user profile into a flat list of interest tags for feed boosting.

    Combines tech_stack + skill expansion + tool expansion + provider expansion.
    Used by feed, diff, action-items, and library recommend endpoints.
    """
    if not profile:
        return []

    tags: set[str] = set()

    # tech_stack: pass through as-is
    tags.update(profile.get("tech_stack") or [])

    # skills: expand via SKILL_TAG_EXPANSION
    for skill in profile.get("skills") or []:
        if skill in SKILL_TAG_EXPANSION:
            tags |= SKILL_TAG_EXPANSION[skill]
        else:
            tags.add(skill)

    # tools: expand via TOOL_TAG_EXPANSION
    for tool in profile.get("tools") or []:
        if tool in TOOL_TAG_EXPANSION:
            tags |= TOOL_TAG_EXPANSION[tool]
        else:
            tags.add(tool)

    # providers: expand via PROVIDER_TAG_EXPANSION
    for provider in profile.get("providers") or []:
        if provider in PROVIDER_TAG_EXPANSION:
            tags |= PROVIDER_TAG_EXPANSION[provider]
        else:
            tags.add(provider)

    return list(tags)


# Persona presets — pure Python dict, no LLM cost.
# Preset values act as defaults — explicit caller params override persona presets.
PERSONA_PRESETS = {
    "agent-builder": {
        "significance_filter": ["breaking", "major"],
        "sort": "significance",
        "days_override": 14,
    },
    "curator": {
        "days_override": 7,
        "sort": "significance",
        "limit_override": 50,
    },
    "learner": {
        "type_filter": ["docs", "practice"],
        "days_override": 90,
    },
    "agent": {
        "new_override": True,
        "sort": "significance",
        "days_override": 7,
    },
}


def _collapse_clusters(items: list) -> list:
    """Collapse cluster duplicates — delegates to shared search_utils.collapse_clusters."""
    return collapse_clusters(items, rank_key="relevance_score")


@feed_router.get("/feed", response_model=FeedResponse)
@limiter.limit("100/minute")
async def get_feed(
    request: Request,
    type: Optional[str] = Query(
        None, description="Filter by primary_type: skill, tool, update, practice, docs"
    ),
    tag: Optional[list[str]] = Query(
        None,
        description="Filter by tag(s). Multiple values are AND-matched: all specified tags must be present.",
    ),
    group: Optional[str] = Query(
        None, description="Filter by tag group name (expands to all tags in that group)"
    ),
    significance: Optional[str] = Query(
        None,
        description="Filter by significance: breaking, major, minor, informational",
    ),
    days: int = Query(7, ge=1, le=90, description="Recency window in days (default 7)"),
    since: Optional[datetime] = Query(
        None,
        description="Return only items newer than this timestamp (ISO 8601). Overrides `days` when provided.",
    ),
    sort: Optional[str] = Query(
        None,
        pattern="^(significance|score)$",
        description="Sort order: 'significance' (breaking first) or 'score' (default relevance)",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000_000),
    new: bool = Query(
        False,
        description="Return only items not seen by this API key since last call. Updates cursor after response.",
    ),
    q: Optional[str] = Query(
        None,
        min_length=1,
        max_length=200,
        description="Filter feed items by search query (full-text match on title/excerpt/content)",
    ),
    source: Optional[str] = Query(None, description="Filter by source ID"),
    persona: Optional[str] = Query(
        None,
        pattern="^(agent-builder|curator|learner|agent)$",
        description="Pre-configured feed preset for role",
    ),
    fields: Optional[str] = Query(
        None,
        description="Comma-separated list of fields to include in response items. id is always included.",
    ),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns a paginated list of processed intelligence items.
    Filtered by type, tag, significance, and date range.
    If a user profile exists, items matching your tech_stack are boosted to the top.
    Use `persona=` for role-based presets or `new=true` for incremental polling.
    """
    # Query logging BEFORE cache check — ensures every query is always counted
    try:
        await log_query(session, api_key.id, "feed", q, 0)
    except Exception:
        pass

    # Response cache check — skip when new=True (cursor mode is per-key)
    _cache_key = None
    _feed_redis = get_redis_from_request(request)
    if is_cache_enabled() and _feed_redis and not new:
        _cache_params = {
            "type": type,
            "tag": tuple(tag) if tag else None,
            "group": group,
            "significance": significance,
            "days": days,
            "since": str(since) if since else None,
            "sort": sort,
            "limit": limit,
            "offset": offset,
            "q": q,
            "source": source,
            "persona": persona,
            "fields": fields,
            "api_key_id": api_key.id,  # include user identity — feed is profile-personalized
        }
        _cache_key = make_cache_key("feed", _cache_params)
        _cached = await get_cached_response(_feed_redis, _cache_key)
        if _cached is not None:
            return JSONResponse(content=_json_mod.loads(_cached))

    # P3-51: Validate type and significance parameters against known values
    VALID_TYPES = {"skill", "tool", "update", "practice", "docs"}
    VALID_SIGNIFICANCES = {"breaking", "major", "minor", "informational"}
    if type and type not in VALID_TYPES:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_type",
                    "message": f"Invalid type '{type}'. Valid types: {', '.join(sorted(VALID_TYPES))}",
                }
            },
        )
    if significance and significance not in VALID_SIGNIFICANCES:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_significance",
                    "message": f"Invalid significance '{significance}'. Valid values: {', '.join(sorted(VALID_SIGNIFICANCES))}",
                }
            },
        )

    # Apply persona presets BEFORE building filters.
    # Preset values act as defaults — explicit caller params override persona presets.
    if persona and persona in PERSONA_PRESETS:
        preset = PERSONA_PRESETS[persona]
        # Apply new_override (e.g. "agent" persona enables cursor mode)
        if preset.get("new_override") and not new:
            new = True
        # Apply sort override if caller didn't specify sort
        if preset.get("sort") and sort is None:
            sort = preset["sort"]
        # Apply days override if caller didn't change from the default of 7
        if preset.get("days_override") and days == 7:
            days = preset["days_override"]
        # Apply limit override if caller didn't change from the default of 20
        if preset.get("limit_override") and limit == 20:
            limit = preset["limit_override"]
        # Apply type filter if caller didn't specify type
        if preset.get("type_filter") and type is None:
            # type_filter is a list; we'll handle multi-type below if needed.
            # For now apply as a single-value filter when list has one entry,
            # or skip if multiple (handled via separate logic below).
            preset_types = preset["type_filter"]
        else:
            preset_types = None
        # Apply significance filter if caller didn't specify significance
        if preset.get("significance_filter") and significance is None:
            preset_significances = preset["significance_filter"]
        else:
            preset_significances = None
    else:
        preset_types = None
        preset_significances = None

    # Resolve cutoff: `since` overrides `days` when provided
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        cutoff = since
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # CURSOR LOGIC (when new=True after persona resolution):
    # If api_key.last_seen_at is set, use it as cutoff (overrides days/since).
    # If first call (last_seen_at is None), use the days/since cutoff as normal.
    cursor_updated = False
    if new and api_key.last_seen_at is not None:
        cutoff = api_key.last_seen_at
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

    # Base filters — use published_at when available, created_at as fallback.
    # This prevents old items (published 2022) from appearing as "new this week"
    # just because they were ingested today.
    filters = [
        IntelItem.status == "processed",
        func.coalesce(IntelItem.published_at, IntelItem.created_at) >= cutoff,
        # Quality floor: exclude items below 0.3 quality; always include breaking/major significance items
        or_(
            IntelItem.quality_score >= 0.3,
            IntelItem.significance.in_(["breaking", "major"]),
        ),
    ]

    if source:
        filters.append(IntelItem.source_id == source)
    if type:
        filters.append(IntelItem.primary_type == type)
    elif preset_types:
        # Persona preset: filter by multiple types (IN clause)
        filters.append(IntelItem.primary_type.in_(preset_types))

    if tag:
        # JSONB array containment filter — AND logic for multiple tags
        # Each specified tag must be present in the item's tags array
        for t in tag:
            filters.append(
                cast(IntelItem.tags, JSONB).op("@>")(func.jsonb_build_array(t))
            )

    if group and group in TAG_GROUPS:
        # Expand group to all tags in that group — OR match across all group tags
        group_tags = TAG_GROUPS[group]
        # Build OR filter across all tags in the group
        group_filters = [
            cast(IntelItem.tags, JSONB).op("@>")(func.jsonb_build_array(t))
            for t in group_tags
        ]
        filters.append(or_(*group_filters))

    # Embed query for semantic ranking (when q is provided)
    q_embedding = None
    if q:
        try:
            q_embedding = await _embed_concept(q, request=request)
        except Exception:
            logger.debug("feed_embed_fallback", reason="embed failed")

    if q and q_embedding is not None:
        # Semantic filter + ranking: cosine distance < 0.50 filters clearly irrelevant
        # items while keeping niche queries with genuine matches.
        # Sorting by distance (added below) ensures the best matches surface first.
        embedding_str = "[" + ",".join(str(v) for v in q_embedding) + "]"
        filters.append(
            text(
                "embedding IS NOT NULL AND embedding <=> CAST(:q_embed AS vector) < 0.50"
            )
        )
        filters[-1] = filters[-1].bindparams(
            bindparam("q_embed", value=embedding_str, type_=String)
        )
    elif q:
        # Fallback: full-text search when embedding unavailable
        filters.append(
            text("search_vector @@ websearch_to_tsquery('english', :q_feed)")
        )
        filters[-1] = filters[-1].bindparams(bindparam("q_feed", value=q, type_=String))

    if significance:
        filters.append(IntelItem.significance == significance)
    elif preset_significances:
        # Persona preset: filter by multiple significance values (IN clause)
        filters.append(IntelItem.significance.in_(preset_significances))

    # Profile-based personalization: fetch profile BEFORE building query so
    # we can inject the interest tags into the SQL ORDER BY (not Python post-sort).
    # This ensures items matching the user's profile surface on page 1 regardless
    # of their absolute position in the unfiltered result set.
    user_query = select(User.profile).where(User.id == api_key.user_id)
    user_result = await session.execute(user_query)
    profile = user_result.scalar_one_or_none()

    # Expand profile into interest tags (zero LLM cost — static mapping)
    interest_tags = expand_profile_tags(profile)

    # Build ORDER BY clause.
    # When q + embedding available, semantic distance is the primary sort
    # so the most relevant items surface first (significance becomes secondary).
    order_clause = []

    if q and q_embedding is not None:
        embedding_str_order = "[" + ",".join(str(v) for v in q_embedding) + "]"
        order_clause.append(
            text("embedding <=> CAST(:q_embed_order AS vector) ASC").bindparams(
                bindparam("q_embed_order", value=embedding_str_order, type_=String)
            )
        )

    if sort == "significance":
        order_clause.append(
            text(
                "CASE significance "
                "WHEN 'breaking' THEN 0 "
                "WHEN 'major' THEN 1 "
                "WHEN 'minor' THEN 2 "
                "ELSE 3 END ASC"
            ),
        )

    # Composite ranking score: weighted combination of relevance*freshness, quality,
    # profile match, and source tier. Replaces the old lexicographic multi-column ORDER BY
    # where quality was column 5 (near-zero influence). Now quality has 30% weight.
    # Weights: 0.40 relevance*freshness + 0.30 quality + 0.20 profile + 0.10 tier
    if interest_tags:
        profile_component = (
            "(SELECT COUNT(*)::float / GREATEST(1, :n_interest_tags)"
            " FROM jsonb_array_elements_text(CAST(tags AS jsonb)) t"
            " WHERE t = ANY(:interest_tags))"
        )
        composite_bindparams = [
            bindparam("interest_tags", value=interest_tags, type_=ARRAY(String)),
            bindparam("n_interest_tags", value=max(len(interest_tags), 1)),
        ]
    else:
        profile_component = "0.0"
        composite_bindparams = []

    composite_expr = text(
        "("
        "  0.40 * COALESCE(relevance_score, 0.5)"
        "       * EXP(LN(0.5) / 7.0 * EXTRACT(EPOCH FROM (NOW() - COALESCE(published_at, created_at))) / 86400.0)"
        "  + 0.30 * COALESCE(quality_score, 0.5)"
        "  + 0.20 * "
        + profile_component
        + "  + 0.10 * (SELECT CASE COALESCE(s.tier, 'tier3')"
        "             WHEN 'tier1' THEN 1.0 WHEN 'tier2' THEN 0.5 ELSE 0.2 END"
        "             FROM sources s WHERE s.id = intel_items.source_id)"
        ") DESC"
    )
    if composite_bindparams:
        composite_expr = composite_expr.bindparams(*composite_bindparams)
    order_clause.append(composite_expr)

    # Base query for items
    query = (
        select(IntelItem)
        .where(and_(*filters))
        .order_by(*order_clause)
        .limit(int(limit * 1.3) + 1)
        .offset(offset)
    )

    # Query for total count
    count_query = select(func.count()).select_from(IntelItem).where(and_(*filters))

    # Execute both queries
    result = await session.execute(query)
    items = list(result.scalars().all())

    total_result = await session.execute(count_query)
    total = total_result.scalar() or 0

    # P1-15: Collapse cluster duplicates — group by cluster_id, keep best representative.
    # Items with NULL cluster_id are treated as unique (not grouped).
    items = _collapse_clusters(items)

    # Deduplicate star milestone events — keep only the latest per base URL.
    # github-deep star milestones create separate items for the same repo
    # (e.g., "repo X reached 100 stars", "500 stars", "1000 stars") with URLs
    # like https://github.com/owner/repo#star-milestone-500.
    # Items are already ordered by relevance/significance, so the first seen
    # per base URL is the one we keep.
    seen_base_urls: dict[str, bool] = {}
    deduped_items: list = []
    for item in items:
        base_url = item.url.split("#")[0] if item.url else item.url
        if base_url in seen_base_urls:
            continue
        seen_base_urls[base_url] = True
        deduped_items.append(item)
    items = deduped_items[:limit]

    # Convert to response objects (profile boost already applied via SQL ORDER BY)
    response_items = [IntelItemResponse.model_validate(item) for item in items]

    # Update cursor after building results (new=True mode):
    # Only advance cursor when there are actual results AND not paginating (offset=0).
    # - Advancing on empty would cause the next call to miss items created between calls.
    # - Advancing during pagination (offset>0) would skip items on subsequent pages.
    # - Use max(created_at) from actual returned rows, not datetime.now(), to prevent
    #   skipping items created between the SELECT and the cursor write.
    if new and total > 0 and offset == 0 and response_items:
        # Use max(created_at) from actual returned rows + 1 microsecond to prevent
        # re-delivery (cutoff uses >= so exact match would re-include the same item).
        max_created = max(item.created_at for item in response_items)
        cursor_ts = max_created + timedelta(microseconds=1)
        await session.execute(
            text("UPDATE api_keys SET last_seen_at = :ts WHERE id = :kid"),
            {"ts": cursor_ts, "kid": api_key.id},
        )
        await session.commit()
        cursor_updated = True

    feed_response = FeedResponse(
        items=response_items,
        total=total,
        offset=offset,
        limit=limit,
        cursor_updated=cursor_updated,
    )

    response_dict = feed_response.model_dump(mode="json")

    # Empty feed: enrich with pipeline health metadata so callers know the system
    # is working even when there are no new items.
    if total == 0:
        health_sql = text(
            """
            SELECT MAX(last_successful_poll) AS last_ingestion,
                   COUNT(*) FILTER (WHERE is_active = true) AS sources_active
            FROM sources
            """
        )
        health_result = await session.execute(health_sql)
        health_row = health_result.mappings().fetchone()
        last_ingestion = health_row["last_ingestion"] if health_row else None
        sources_active = int(health_row["sources_active"] or 0) if health_row else 0

        pipeline_healthy = False
        if last_ingestion is not None:
            age = (
                datetime.now(timezone.utc) - last_ingestion.replace(tzinfo=timezone.utc)
                if last_ingestion.tzinfo is None
                else datetime.now(timezone.utc) - last_ingestion
            )
            pipeline_healthy = age.total_seconds() < 3600

        response_dict["status"] = "no_new_items"
        response_dict["pipeline_healthy"] = pipeline_healthy
        response_dict["last_ingestion"] = (
            last_ingestion.isoformat() if last_ingestion else None
        )
        response_dict["sources_active"] = sources_active

        # Zero-result coverage metadata: help callers distinguish
        # "monitored but quiet" from "not monitored" topics.
        first_tag = (
            tag[0]
            if isinstance(tag, list) and tag
            else (tag if isinstance(tag, str) else None)
        )
        topic_term = (
            first_tag or (q or "").split()[0] if (first_tag or q) else ""
        ).strip()
        if topic_term:
            coverage_sql = text(
                "SELECT COUNT(*) FROM sources "
                "WHERE is_active = true "
                "AND (name ILIKE :pattern "
                "OR CAST(url AS text) ILIKE :pattern "
                "OR CAST(config AS text) ILIKE :pattern)"
            )
            coverage_result = await session.execute(
                coverage_sql, {"pattern": f"%{topic_term}%"}
            )
            topic_count = int(coverage_result.scalar() or 0)
            response_dict["topic_sources_monitored"] = topic_count
            if topic_count > 0:
                response_dict["coverage_note"] = (
                    f"Intel Overdrive monitors {topic_count} source(s) related to "
                    f"'{topic_term}' -- no matching items in the last {days} days."
                )
            else:
                response_dict["coverage_note"] = (
                    f"'{topic_term}' may not be in Intel Overdrive's coverage. "
                    "Check /v1/status for monitored sources."
                )

    # Profile onboarding hint: nudge new users who haven't set a profile yet
    if not profile or profile == {}:
        response_dict["profile_hint"] = (
            "Set up your profile for personalized results: "
            "POST /v1/profile or run 'overdrive-intel profile --sync'"
        )

    # Field selector: strip each item dict to only requested fields (id always included)
    if fields:
        requested = {f.strip() for f in fields.split(",") if f.strip()}
        requested.add("id")  # always include id
        response_dict["items"] = [
            {k: v for k, v in item.items() if k in requested}
            for item in response_dict["items"]
        ]

    # Cache the response on miss
    if _cache_key and _feed_redis:
        try:
            _resp_json = _json_mod.dumps(response_dict, default=str)
            await set_cached_response(_feed_redis, _cache_key, _resp_json)
        except Exception:
            pass

    return JSONResponse(content=response_dict)
