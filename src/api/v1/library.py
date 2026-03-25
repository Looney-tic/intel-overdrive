"""GET /v1/library — knowledge library endpoints with evergreen scoring.

V1: Computed view over intel_items — zero schema changes, validates evergreen formula.
V1.1 (15-03): recommend, search, signals, suggest endpoints over library_items table.
Topics are derived from the existing tag taxonomy (TAG_GROUPS + raw tags).
"""
import json as _json_mod
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.cache import (
    make_cache_key,
    get_cached_response,
    set_cached_response,
    is_cache_enabled,
    get_redis_from_request,
)
from src.api.schemas import (
    LibraryEntryResponse,
    LibraryIndexResponse,
    LibraryItemSummary,
    LibraryRecommendResponse,
    LibrarySearchResponse,
    LibrarySearchResult,
    LibrarySignalRequest,
    LibrarySuggestRequest,
    LibraryTopicResponse,
    LibraryTopicSummary,
)
from src.api.utils import escape_ilike
from src.api.v1.feed import TAG_GROUPS
from src.api.query_logger import log_query
from src.core.logger import get_logger
from src.models.models import APIKey, LibraryItem, User

logger = get_logger(__name__)

library_router = APIRouter(tags=["library"])

# Human-readable labels for TAG_GROUP keys.
_TOPIC_LABELS: dict[str, str] = {
    "browser-automation": "Browser Automation",
    "database": "Database",
    "ai-agents": "AI Agents",
    "mcp": "Model Context Protocol",
    "claude-code": "Claude Code",
    "devops": "DevOps & Infrastructure",
    "security": "Security",
    "testing": "Testing",
    "api-development": "API Development",
    "documentation": "Documentation",
}

# Brief descriptions per topic for discovery.
_TOPIC_DESCRIPTIONS: dict[str, str] = {
    "browser-automation": "Puppeteer, Playwright, Selenium, and browser-based agent tooling",
    "database": "Postgres, Neon, Supabase, and SQL patterns for modern applications",
    "ai-agents": "Multi-agent orchestration, agentic design, and workflow automation",
    "mcp": "Building, configuring, and securing MCP servers and clients",
    "claude-code": "Claude Code skills, hooks, agents, and automation patterns",
    "devops": "Docker, CI/CD, monitoring, and deployment for AI-adjacent services",
    "security": "Authentication, authorization, and encryption best practices",
    "testing": "TDD, pytest, test automation, and quality gates",
    "api-development": "REST APIs, FastAPI, integration patterns, and server design",
    "documentation": "Writing, maintaining, and structuring technical documentation",
}


# ---------------------------------------------------------------------------
# Route Registration Order (FastAPI uses first-match-wins for same-prefix routes)
# ---------------------------------------------------------------------------
# 1. Concrete literal paths: /library/recommend, /library/search, /library/suggest
# 2. POST /library/{slug}/signals (POST method, no conflict with GETs)
# 3. GET /library (base index)
# 4. GET /library/topics (concrete, before wildcard)
# 5. GET /library/topic/{topic} (specific prefix, before wildcard)
# 6. GET /library/{slug} (wildcard catch-all — MUST be LAST)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Route 1a: GET /library/recommend — profile-matched library entries
# ---------------------------------------------------------------------------


@library_router.get("/library/recommend", response_model=LibraryRecommendResponse)
@limiter.limit("30/minute")
async def get_library_recommend(
    request: Request,
    response: Response,
    limit: int = Query(5, ge=1, le=20, description="Max entries to return"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Return profile-matched library entries for the authenticated user.

    Uses expanded profile tags (tech_stack + skills + tools + providers) to boost entries.
    Returns 422 if the user has no profile set.
    Authenticated. Rate limited: 30/minute.
    """
    from src.api.v1.feed import expand_profile_tags

    # Load user profile via api_key -> user_id
    user_result = await session.execute(select(User).where(User.id == api_key.user_id))
    user = user_result.scalar_one_or_none()
    profile = user.profile if user else {}
    interest_tags = expand_profile_tags(profile)

    if not interest_tags:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "no_profile",
                "detail": "Set your profile first (POST /v1/profile)",
            },
        )

    # Build tag overlap query — boost by expanded profile tags intersection with entry tags
    # Uses jsonb_array_elements_text to count matching tags per entry
    sql = text(
        """
        SELECT
            li.slug,
            li.title,
            li.tldr,
            li.entry_type,
            li.confidence,
            li.staleness_risk,
            li.topic_path,
            li.tags,
            (
                SELECT COUNT(*)
                FROM jsonb_array_elements_text(li.tags::jsonb) t
                WHERE t = ANY(:interest_tags)
            )::float AS overlap_count
        FROM library_items li
        WHERE li.status = 'active'
          AND li.is_current = TRUE
        ORDER BY overlap_count DESC, li.graduation_score DESC, li.helpful_count DESC
        LIMIT :limit
        """
    )
    rows = (
        (await session.execute(sql, {"interest_tags": interest_tags, "limit": limit}))
        .mappings()
        .all()
    )

    # Collect which profile tags actually matched
    matched_tags: set[str] = set()
    entries: list[LibrarySearchResult] = []
    for row in rows:
        import json as _json

        tags_raw = row["tags"]
        tags_list: list[str] = (
            _json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
        )
        overlap = [t for t in tags_list if t in interest_tags]
        matched_tags.update(overlap)
        entries.append(
            LibrarySearchResult(
                slug=row["slug"],
                title=row["title"],
                tldr=row["tldr"],
                entry_type=row["entry_type"],
                confidence=row["confidence"],
                staleness_risk=row["staleness_risk"],
                topic_path=row["topic_path"],
                match_score=round(float(row["overlap_count"] or 0.0), 4),
            )
        )

    result = LibraryRecommendResponse(
        entries=entries,
        profile_tags_matched=sorted(matched_tags),
    )
    return JSONResponse(content=result.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Route 1b: GET /library/search — semantic + full-text search
# ---------------------------------------------------------------------------


@library_router.get("/library/search", response_model=LibrarySearchResponse)
@limiter.limit("30/minute")
async def get_library_search(
    request: Request,
    response: Response,
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    topic: Optional[str] = Query(None, description="Filter by topic_path prefix"),
    entry_type: Optional[str] = Query(None, description="Filter by entry_type"),
    limit: int = Query(5, ge=1, le=20, description="Max results to return"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Search library entries using semantic (embedding) and full-text (ILIKE) search.

    When library_items have embeddings, uses Voyage to embed query and runs cosine search.
    Always runs full-text ILIKE search on title + tldr + body.
    Results are merged (embedding hits first), deduplicated by slug.
    Authenticated. Rate limited: 30/minute.
    """
    # Query logging BEFORE cache check — ensures every query is always counted
    try:
        await log_query(session, api_key.id, "library", q, 0)
    except Exception:
        pass

    # Response cache check
    _cache_key = None
    _lib_redis = get_redis_from_request(request)
    if is_cache_enabled() and _lib_redis:
        _cache_params = {
            "q": q,
            "topic": topic,
            "entry_type": entry_type,
            "limit": limit,
        }
        _cache_key = make_cache_key("library-search", _cache_params)
        _cached = await get_cached_response(_lib_redis, _cache_key)
        if _cached is not None:
            return JSONResponse(content=_json_mod.loads(_cached))

    from src.api.v1.similar import _embed_concept

    # Normalize Unicode input (NFC) to prevent encoding crashes with emoji/CJK
    q = unicodedata.normalize("NFC", q)

    embedding_results: list[dict] = []
    embedding_available = False

    # Try semantic search first (optional — degrades gracefully if no embeddings)
    try:
        embedding = await _embed_concept(q, request=request)
    except Exception:
        logger.warning("library_search_embed_failed", query=q[:100])
        embedding = None
    if embedding is not None:
        embedding_available = True
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
        topic_filter = "AND li.topic_path LIKE :topic_prefix" if topic else ""
        type_filter = "AND li.entry_type = :entry_type" if entry_type else ""
        sem_params: dict = {"embedding": embedding_str, "limit": limit}
        if topic:
            sem_params["topic_prefix"] = f"{topic}%"
        if entry_type:
            sem_params["entry_type"] = entry_type

        sem_sql = text(
            f"""
            SELECT
                li.slug, li.title, li.tldr, li.entry_type,
                li.confidence, li.staleness_risk, li.topic_path,
                1.0 - (li.embedding <=> CAST(:embedding AS vector)) AS match_score
            FROM library_items li
            WHERE li.status = 'active'
              AND li.is_current = TRUE
              AND li.embedding IS NOT NULL
              {topic_filter}
              {type_filter}
            ORDER BY li.embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
            """
        )
        sem_rows = (await session.execute(sem_sql, sem_params)).mappings().all()
        embedding_results = [dict(r) for r in sem_rows]

    # Per-word ILIKE scoring: split query into words, score each word match
    # independently across fields. Fixes exact-phrase matching that broke
    # multi-word queries like "MCP best practices".
    # Pattern: replicated from src/api/v1/search.py inline library search.
    or_words = re.findall(r"[a-zA-Z0-9]+", q.lower())
    topic_filter_txt = "AND li.topic_path LIKE :topic_prefix" if topic else ""
    type_filter_txt = "AND li.entry_type = :entry_type" if entry_type else ""
    txt_params: dict = {"limit": limit}
    if topic:
        txt_params["topic_prefix"] = f"{topic}%"
    if entry_type:
        txt_params["entry_type"] = entry_type

    if not or_words:
        or_words = [""]  # fallback: match everything

    score_parts: list[str] = []
    for i, w in enumerate(or_words):
        p = f"lw{i}"
        txt_params[p] = f"%{escape_ilike(w)}%"
        score_parts.append(
            f"(CASE WHEN li.topic_path ILIKE :{p} THEN 10 ELSE 0 END "
            f"+ CASE WHEN li.title ILIKE :{p} THEN 5 ELSE 0 END "
            f"+ CASE WHEN li.tags::text ILIKE :{p} THEN 4 ELSE 0 END "
            f"+ CASE WHEN li.tldr ILIKE :{p} THEN 3 ELSE 0 END "
            f"+ CASE WHEN li.body ILIKE :{p} THEN 1 ELSE 0 END)"
        )
    score_expr = " + ".join(score_parts)

    # Match clause: any word matches in title, tldr, topic_path, or tags
    match_clauses = " OR ".join(
        f"li.topic_path ILIKE :lw{i} OR li.title ILIKE :lw{i} "
        f"OR li.tldr ILIKE :lw{i} OR li.tags::text ILIKE :lw{i}"
        for i in range(len(or_words))
    )

    txt_sql = text(
        f"""
        SELECT
            li.slug, li.title, li.tldr, li.entry_type,
            li.confidence, li.staleness_risk, li.topic_path,
            ({score_expr}) AS match_score
        FROM library_items li
        WHERE li.status = 'active'
          AND li.is_current = TRUE
          AND ({match_clauses})
          {topic_filter_txt}
          {type_filter_txt}
        ORDER BY match_score DESC
        LIMIT :limit
        """
    )
    try:
        txt_rows = (await session.execute(txt_sql, txt_params)).mappings().all()
        text_results = [dict(r) for r in txt_rows]
    except Exception:
        logger.warning("library_search_ilike_failed", query=q[:100])
        text_results = []

    # Merge: embedding first, then text hits, deduped by slug
    seen_slugs: set[str] = set()
    merged: list[dict] = []
    for row in embedding_results + text_results:
        slug = row["slug"]
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            merged.append(row)

    results: list[LibrarySearchResult] = []
    for row in merged[:limit]:
        results.append(
            LibrarySearchResult(
                slug=row["slug"],
                title=row["title"],
                tldr=row["tldr"],
                entry_type=row["entry_type"],
                confidence=row["confidence"],
                staleness_risk=row["staleness_risk"],
                topic_path=row["topic_path"],
                match_score=round(float(row.get("match_score") or 0.5), 4),
            )
        )

    # If zero results, set suggest_topic hint via response header (non-breaking)
    if not results:
        response.headers[
            "X-Suggest"
        ] = f"No results for '{q}'. Try /v1/search or POST /v1/library/suggest"

    resp = LibrarySearchResponse(
        items=results,
        total=len(results),
        query_understood=embedding_available,
    )

    resp_dict = resp.model_dump(mode="json")

    # Cache the response on miss
    if _cache_key and _lib_redis:
        try:
            await set_cached_response(
                _lib_redis, _cache_key, _json_mod.dumps(resp_dict, default=str)
            )
        except Exception:
            pass

    return JSONResponse(content=resp_dict)


# ---------------------------------------------------------------------------
# Route 1c: POST /library/suggest — log a topic suggestion
# ---------------------------------------------------------------------------


@library_router.post("/library/suggest", status_code=201)
@limiter.limit("5/minute")
async def post_library_suggest(
    request: Request,
    body: LibrarySuggestRequest,
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Accept a topic suggestion from an authenticated user.

    Logs the suggestion (topic + description + user) as a structured log entry.
    Returns 201 with suggestion_id and status "received".
    Rate limited: 5/minute.
    """
    suggestion_id = str(uuid.uuid4())
    logger.info(
        "library_suggestion_received",
        suggestion_id=suggestion_id,
        topic=body.topic,
        description=body.description[:200],
        api_key_id=api_key.id,
    )
    return JSONResponse(
        status_code=201,
        content={
            "suggestion_id": suggestion_id,
            "status": "received",
            "topic": body.topic,
        },
    )


# ---------------------------------------------------------------------------
# Route 1d: POST /library/{slug}/signals — helpful/outdated feedback
# ---------------------------------------------------------------------------


@library_router.post("/library/{slug}/signals")
@limiter.limit("10/minute")
async def post_library_signal(
    slug: str,
    request: Request,
    body: LibrarySignalRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Record a signal (helpful or outdated) for a library entry.

    helpful: increments helpful_count, bumps last_confirmed_at.
    outdated: sets flagged_outdated = TRUE.
    Returns updated slug, helpful_count, flagged_outdated.
    Authenticated. Rate limited: 10/minute.
    """
    result = await session.execute(
        select(LibraryItem).where(
            LibraryItem.slug == slug, LibraryItem.is_current.is_(True)
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Library entry '{slug}' not found")

    if body.action == "helpful":
        item.helpful_count = (item.helpful_count or 0) + 1
        item.last_confirmed_at = datetime.now(timezone.utc)
    elif body.action == "outdated":
        item.flagged_outdated = True

    session.add(item)
    await session.commit()
    await session.refresh(item)

    return JSONResponse(
        content={
            "slug": item.slug,
            "helpful_count": item.helpful_count,
            "flagged_outdated": item.flagged_outdated,
        }
    )


# ---------------------------------------------------------------------------
# Route 2: GET /library — authenticated topic index
# ---------------------------------------------------------------------------


@library_router.get("/library", response_model=LibraryIndexResponse)
@limiter.limit("60/minute")
async def get_library_index(
    request: Request,
    response: Response,
    min_items: int = Query(
        3, ge=1, description="Minimum items required for a topic to appear"
    ),
    limit: int = Query(50, ge=1, le=200, description="Max topics to return"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Return topic index ranked by avg composite quality (quality_score * relevance_score).

    Authenticated. Aggregates processed intel_items by tag to compute per-topic stats.
    """
    sql = text(
        """
        SELECT
            tag,
            COUNT(*)                                    AS item_count,
            AVG(quality_score * relevance_score)        AS avg_composite,
            MAX(created_at)                             AS last_updated
        FROM intel_items,
             jsonb_array_elements_text(CAST(tags AS jsonb)) AS tag
        WHERE status = 'processed'
        GROUP BY tag
        HAVING COUNT(*) >= :min_items
        ORDER BY avg_composite DESC, item_count DESC
        LIMIT :limit
        """
    )
    rows = (
        (await session.execute(sql, {"min_items": min_items, "limit": limit}))
        .mappings()
        .all()
    )

    topics: list[LibraryTopicSummary] = []
    for row in rows:
        tag = row["tag"]
        # Derive label: use _TOPIC_LABELS for known TAG_GROUP keys, else capitalize.
        label = _TOPIC_LABELS.get(tag, tag.replace("-", " ").title())
        description = _TOPIC_DESCRIPTIONS.get(tag)
        # Compute subtopics: other tags in the same TAG_GROUP that share this prefix.
        subtopics: list[str] = []
        for group_key, variants in TAG_GROUPS.items():
            if tag in variants and tag != group_key:
                subtopics = [v for v in variants if v != tag]
                break

        topics.append(
            LibraryTopicSummary(
                topic=tag,
                label=label,
                description=description,
                item_count=int(row["item_count"]),
                avg_quality=round(float(row["avg_composite"] or 0.0), 4),
                last_updated=row["last_updated"],
                subtopics=subtopics,
            )
        )

    result = LibraryIndexResponse(
        topics=topics,
        total=len(topics),
        generated_at=datetime.now(timezone.utc),
    )
    return JSONResponse(content=result.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Route 3: GET /library/topics — unauthenticated topic listing
# ---------------------------------------------------------------------------


@library_router.get("/library/topics")
@limiter.limit("30/minute")
async def get_library_topics(
    request: Request,
    response: Response,
    min_items: int = Query(
        3, ge=1, description="Minimum items required for a topic to appear"
    ),
    limit: int = Query(50, ge=1, le=200, description="Max topics to return"),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Return unauthenticated topic listing with entry_count and subtopics.

    No authentication required — discovery entry point for agents and developers.
    """
    sql = text(
        """
        SELECT
            tag,
            COUNT(*)                                    AS item_count,
            AVG(quality_score * relevance_score)        AS avg_composite,
            MAX(created_at)                             AS last_updated
        FROM intel_items,
             jsonb_array_elements_text(CAST(tags AS jsonb)) AS tag
        WHERE status = 'processed'
        GROUP BY tag
        HAVING COUNT(*) >= :min_items
        ORDER BY avg_composite DESC, item_count DESC
        LIMIT :limit
        """
    )
    rows = (
        (await session.execute(sql, {"min_items": min_items, "limit": limit}))
        .mappings()
        .all()
    )

    topics: list[dict] = []
    for row in rows:
        tag = row["tag"]
        label = _TOPIC_LABELS.get(tag, tag.replace("-", " ").title())
        description = _TOPIC_DESCRIPTIONS.get(tag)
        subtopics: list[str] = []
        for group_key, variants in TAG_GROUPS.items():
            if tag in variants and tag != group_key:
                subtopics = [v for v in variants if v != tag]
                break

        topics.append(
            {
                "topic": tag,
                "label": label,
                "description": description,
                "entry_count": int(row["item_count"]),
                "last_updated": row["last_updated"].isoformat()
                if row["last_updated"]
                else None,
                "subtopics": subtopics,
            }
        )

    return JSONResponse(
        content={
            "topics": topics,
            "total": len(topics),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# Route 4: GET /library/topic/{topic} — authenticated topic detail with evergreen scoring
# (Renamed from /library/{topic} to avoid shadowing by /library/{slug})
# ---------------------------------------------------------------------------


@library_router.get("/library/topic/{topic}", response_model=LibraryTopicResponse)
@limiter.limit("60/minute")
async def get_library_topic(
    topic: str,
    request: Request,
    response: Response,
    limit: int = Query(20, ge=1, le=100, description="Max items to return"),
    entry_type: Optional[str] = Query(None, description="Filter by primary_type"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Return items for a topic ranked by evergreen_score (descending).

    Evergreen score = base_quality * signal_boost * recency_decay.
    - base_quality: weighted sum of quality_score (0.35), relevance_score (0.35),
      confidence_score (0.10), and significance bonus (0.0-0.20)
    - signal_boost: 1.0 + min(upvotes + bookmarks, 10) * 0.05  [1.0-1.5x]
    - recency_decay: exp(-0.693 * age_days / 90)  [90-day half-life]

    Unknown topics return 200 with empty items list (not 404).
    Authenticated.
    """
    # Determine tag filter: if topic is a TAG_GROUP key, use semantic expansion
    # (ANY(:tag_variants) EXISTS subquery) to match all variant tags.
    if topic in TAG_GROUPS:
        tag_variants = TAG_GROUPS[topic]
        tag_filter_clause = (
            "EXISTS ("
            "  SELECT 1 FROM jsonb_array_elements_text(CAST(i.tags AS jsonb)) t"
            "  WHERE t = ANY(:tag_variants)"
            ")"
        )
        params: dict = {"tag_variants": tag_variants, "limit": limit}
    else:
        tag_filter_clause = (
            "CAST(i.tags AS jsonb) @> jsonb_build_array(CAST(:topic AS text))"
        )
        params = {"topic": topic, "limit": limit}

    # Optional primary_type filter
    type_clause = "AND i.primary_type = :entry_type" if entry_type else ""
    if entry_type:
        params["entry_type"] = entry_type

    sql = text(
        f"""
        WITH tag_items AS (
            SELECT
                i.id,
                i.title,
                i.url,
                i.excerpt,
                i.summary,
                i.primary_type,
                i.tags,
                i.significance,
                i.relevance_score,
                i.quality_score,
                i.confidence_score,
                i.published_at,
                i.created_at,
                i.source_name,
                COALESCE(sig.upvotes, 0)   AS upvote_count,
                COALESCE(sig.bookmarks, 0) AS bookmark_count,
                (
                    i.quality_score * 0.35
                    + i.relevance_score * 0.35
                    + i.confidence_score * 0.10
                    + CASE i.significance
                        WHEN 'breaking' THEN 0.20
                        WHEN 'major'    THEN 0.15
                        WHEN 'minor'    THEN 0.05
                        ELSE 0.0
                      END
                )
                * (1.0 + LEAST(
                       COALESCE(sig.upvotes, 0) + COALESCE(sig.bookmarks, 0), 10
                   ) * 0.05)
                * EXP(
                    -0.693
                    * EXTRACT(EPOCH FROM (NOW() - i.created_at))
                    / (90 * 86400)
                  )
                AS evergreen_score
            FROM intel_items i
            LEFT JOIN (
                SELECT
                    item_id,
                    COUNT(*) FILTER (WHERE action = 'upvote')   AS upvotes,
                    COUNT(*) FILTER (WHERE action = 'bookmark') AS bookmarks
                FROM item_signals
                GROUP BY item_id
            ) sig ON sig.item_id = i.id
            WHERE i.status = 'processed'
              AND {tag_filter_clause}
              {type_clause}
        )
        SELECT * FROM tag_items
        ORDER BY evergreen_score DESC
        LIMIT :limit
        """
    )

    rows = (await session.execute(sql, params)).mappings().all()

    items: list[LibraryItemSummary] = []
    for row in rows:
        tags_raw = row["tags"]
        if isinstance(tags_raw, str):
            import json

            tags_list = json.loads(tags_raw)
        elif isinstance(tags_raw, list):
            tags_list = tags_raw
        else:
            tags_list = []

        items.append(
            LibraryItemSummary(
                id=str(row["id"]),
                title=row["title"],
                url=row["url"],
                summary=row["summary"] or row["excerpt"],
                primary_type=row["primary_type"],
                tags=tags_list,
                significance=row["significance"],
                relevance_score=float(row["relevance_score"])
                if row["relevance_score"] is not None
                else None,
                quality_score=float(row["quality_score"])
                if row["quality_score"] is not None
                else None,
                evergreen_score=round(float(row["evergreen_score"] or 0.0), 6),
                upvote_count=int(row["upvote_count"]),
                bookmark_count=int(row["bookmark_count"]),
                source_name=row["source_name"],
                published_at=row["published_at"],
                created_at=row["created_at"],
            )
        )

    label = _TOPIC_LABELS.get(topic, topic.replace("-", " ").title())
    description = _TOPIC_DESCRIPTIONS.get(topic)

    result = LibraryTopicResponse(
        topic=topic,
        description=description,
        item_count=len(items),
        items=items,
        generated_at=datetime.now(timezone.utc),
    )
    return JSONResponse(content=result.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Route 5: GET /library/{slug} — full entry detail with ETag (MUST be last —
# wildcard catch-all that would shadow /topics and /topic/{topic} if registered first)
# ---------------------------------------------------------------------------


@library_router.get("/library/{slug}", response_model=LibraryEntryResponse)
@limiter.limit("60/minute")
async def get_library_entry(
    slug: str,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Return a full library entry by slug with related entries and source items.

    Includes:
    - related_entries: top 3 by cosine similarity (threshold 0.85), excluding self
    - source_items: intel_items referenced by source_item_ids
    - ETag header from content_hash for 304 caching
    Authenticated. Rate limited: 60/minute.
    """
    result = await session.execute(
        select(LibraryItem).where(
            LibraryItem.slug == slug, LibraryItem.is_current.is_(True)
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Library entry '{slug}' not found")

    # P2-25: ETag header from content_hash + If-None-Match → 304
    if item.content_hash:
        etag_value = f'"{item.content_hash}"'
        if_none_match = request.headers.get("if-none-match")
        if if_none_match == etag_value:
            return Response(status_code=304)
        response.headers["ETag"] = etag_value

    # Related entries via cosine similarity (only when embedding exists)
    related_entries: list[dict] = []
    if item.embedding is not None:
        embedding_str = "[" + ",".join(str(v) for v in item.embedding) + "]"
        related_sql = text(
            """
            SELECT
                li.slug, li.title,
                1.0 - (li.embedding <=> CAST(:embedding AS vector)) AS relevance
            FROM library_items li
            WHERE li.is_current = TRUE
              AND li.slug != :slug
              AND li.embedding IS NOT NULL
              AND 1.0 - (li.embedding <=> CAST(:embedding AS vector)) >= 0.85
            ORDER BY li.embedding <=> CAST(:embedding AS vector)
            LIMIT 3
            """
        )
        rel_rows = (
            (
                await session.execute(
                    related_sql, {"embedding": embedding_str, "slug": slug}
                )
            )
            .mappings()
            .all()
        )
        related_entries = [
            {
                "slug": r["slug"],
                "title": r["title"],
                "relevance": round(float(r["relevance"]), 4),
            }
            for r in rel_rows
        ]

    # Source items from intel_items
    source_items: list[dict] = []
    if item.source_item_ids:
        import json as _json

        src_ids = item.source_item_ids
        if isinstance(src_ids, str):
            src_ids = _json.loads(src_ids)

        if src_ids:
            # Filter to valid UUIDs — synthesized entries may store URLs instead
            _uuid_re = re.compile(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                re.IGNORECASE,
            )
            valid_uuids = [sid for sid in src_ids if _uuid_re.match(str(sid))]

            if valid_uuids:
                src_sql = text(
                    """
                    SELECT id, title, url, published_at
                    FROM intel_items
                    WHERE id = ANY(CAST(:ids AS uuid[]))
                    """
                )
                src_rows = (
                    (await session.execute(src_sql, {"ids": valid_uuids}))
                    .mappings()
                    .all()
                )
                source_items = [
                    {
                        "id": str(r["id"]),
                        "title": r["title"],
                        "url": r["url"],
                        "published_at": r["published_at"].isoformat()
                        if r["published_at"]
                        else None,
                    }
                    for r in src_rows
                ]

    entry = LibraryEntryResponse(
        slug=item.slug,
        title=item.title,
        tldr=item.tldr,
        body=item.body,
        key_points=item.key_points or [],
        gotchas=item.gotchas or [],
        topic_path=item.topic_path,
        entry_type=item.entry_type,
        assumed_context=item.assumed_context,
        role_relevance=item.role_relevance or [],
        related_entries=related_entries,
        source_items=source_items,
        meta={
            "last_updated": item.updated_at.isoformat() if item.updated_at else None,
            "confidence": item.confidence,
            "staleness_risk": item.staleness_risk,
            "content_hash": item.content_hash,
            "valid_until": item.valid_until.isoformat() if item.valid_until else None,
            "helpful_count": item.helpful_count,
            "flagged_outdated": item.flagged_outdated,
            "human_reviewed": item.human_reviewed,
            "version": item.version,
        },
        agent_hint=item.agent_hint,
    )
    return JSONResponse(content=entry.model_dump(mode="json"))
