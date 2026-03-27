import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session, require_api_key
from src.api.schemas import SearchResponse, SearchResultResponse
from src.api.limiter import limiter
from src.api.utils import escape_ilike
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
from src.models.models import APIKey

logger = get_logger(__name__)

search_router = APIRouter(tags=["search"])

# RRF constant k — standard value from the Reciprocal Rank Fusion paper
_RRF_K = 60

# Intent routing: auto-detect query intent when user hasn't specified explicit filters
INTENT_TYPE_PATTERNS = [
    (
        re.compile(
            r"\bmcp\s+server\b|\bserver\s+mcp\b|\bplugin\b|\bmcp\b", re.IGNORECASE
        ),
        "tool",
    ),
]
INTENT_SIGNIFICANCE_PATTERNS = [
    (
        re.compile(
            r"\bbreaking\s+change|\bmigrat(e|ion)\b|\bdeprecate[sd]?\b", re.IGNORECASE
        ),
        "breaking",
    ),
]


def _build_or_tsquery(q: str) -> str:
    """Convert a multi-word query into OR-combined tsquery string.

    'cursor monorepo setup' -> 'cursor | monorepo | setup'
    Strips non-alphanumeric chars to avoid tsquery syntax errors.
    """
    words = re.findall(r"[a-zA-Z0-9]+", q.lower())
    if not words:
        return q
    return " | ".join(words)


@search_router.get("/search", response_model=SearchResponse)
@limiter.limit("60/minute")
async def search_intel_items(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    item_type: Optional[str] = Query(
        None,
        alias="type",
        description="Filter by primary_type: skill, tool, update, practice, docs",
    ),
    tag: Optional[str] = Query(
        None, description="Filter by tag (exact match in tags array)"
    ),
    significance: Optional[str] = Query(
        None,
        description="Filter by significance: breaking, major, minor, informational",
    ),
    days: Optional[int] = Query(
        None, ge=1, le=365, description="Limit results to the last N days"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000_000),
    source: Optional[str] = Query(None, description="Filter by source ID"),
    fields: Optional[str] = Query(
        None,
        description="Comma-separated list of fields to include in response items. id is always included.",
    ),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Full-text search using Postgres tsvector and GIN index.
    Tries AND matching first for precision; falls back to OR if no results.
    Results are deduplicated by base URL (stripping fragments).
    Total is a true COUNT, not len(rows).
    """
    # Query logging BEFORE cache check — ensures every query is always counted
    try:
        await log_query(session, api_key.id, "search", q, 0)
    except Exception:
        pass

    # Response cache check
    _cache_key = None
    redis = get_redis_from_request(request)
    if is_cache_enabled() and redis:
        _cache_params = {
            "q": q,
            "type": item_type,
            "tag": tag,
            "significance": significance,
            "days": days,
            "limit": limit,
            "offset": offset,
            "source": source,
            "fields": fields,
        }
        _cache_key = make_cache_key("search", _cache_params)
        _cached = await get_cached_response(redis, _cache_key)
        if _cached is not None:
            return JSONResponse(content=json.loads(_cached))

    # Build extra filter clauses
    extra_clauses = []
    params: dict = {"q": q, "q_or": _build_or_tsquery(q)}

    if item_type:
        extra_clauses.append("primary_type = :type")
        params["type"] = item_type
    if significance:
        extra_clauses.append("significance = :significance")
        params["significance"] = significance
    if days:
        extra_clauses.append("created_at >= NOW() - INTERVAL '1 day' * :days")
        params["days"] = days
    if tag:
        extra_clauses.append(
            "CAST(tags AS jsonb) @> jsonb_build_array(CAST(:tag AS text))"
        )
        params["tag"] = tag
    if source:
        extra_clauses.append("source_id = :source")
        params["source"] = source

    # Auto-detect intent from query when user hasn't specified explicit filters
    _intent_applied = False
    _pre_intent_clauses = list(extra_clauses)
    _pre_intent_params = dict(params)
    if not item_type:
        for pattern, detected_type in INTENT_TYPE_PATTERNS:
            if pattern.search(q):
                extra_clauses.append("primary_type = :intent_type")
                params["intent_type"] = detected_type
                _intent_applied = True
                break
    if not significance:
        for pattern, detected_sig in INTENT_SIGNIFICANCE_PATTERNS:
            if pattern.search(q):
                extra_clauses.append("significance = :intent_significance")
                params["intent_significance"] = detected_sig
                _intent_applied = True
                break

    extra_where = (" AND " + " AND ".join(extra_clauses)) if extra_clauses else ""

    # Over-fetch multiplier for cluster collapsing — fetch 3x requested, collapse later
    overfetch_limit = limit * 3

    # --- P24-02: Try hybrid search (semantic + fulltext + quality + freshness via RRF) ---
    # Attempt to embed query via Voyage; if it succeeds, use 4-signal RRF ranking.
    # On failure (no API key, timeout, etc.), fall back to text-only search below.
    embedding = None
    _fulltext_count = 0  # fulltext match count — used for RRF confidence threshold
    try:
        embedding = await _embed_concept(q, request=request)
    except Exception:
        logger.debug("search_embed_fallback", reason="embed failed, using text-only")

    if embedding is not None:
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
        rrf_params = {
            **params,
            "embedding": embedding_str,
            "overfetch_limit": overfetch_limit,
            "offset": offset,
            "rrf_k": _RRF_K,
        }

        # RRF hybrid search: 4 CTEs (semantic, fulltext, quality, freshness)
        # Each CTE produces ranked candidates; final score is weighted sum of
        # reciprocal ranks: 1/(k + rank_position)
        # weights must sum to 1.0: semantic=0.25 + fulltext=0.35 + quality=0.35 + freshness=0.05
        rrf_sql = text(
            f"""
            WITH semantic AS (
                SELECT id, ROW_NUMBER() OVER (
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                ) AS rn
                FROM intel_items
                WHERE status = 'processed'
                  AND embedding IS NOT NULL
                  AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                  AND LENGTH(COALESCE(content, '')) >= 100
                  AND COALESCE(quality_score, 0) >= 0.40
                  {extra_where}
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT 50
            ),
            fulltext AS (
                SELECT id, ROW_NUMBER() OVER (
                    ORDER BY (ts_rank_cd(search_vector, websearch_to_tsquery('english', :q))
                              + 0.5 * ts_rank(search_vector, phraseto_tsquery('english', :q))) DESC
                ) AS rn
                FROM intel_items,
                     websearch_to_tsquery('english', :q) query
                WHERE status = 'processed'
                  AND search_vector @@ query
                  AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                  AND LENGTH(COALESCE(content, '')) >= 100
                  AND COALESCE(quality_score, 0) >= 0.40
                  {extra_where}
                ORDER BY (ts_rank_cd(search_vector, query)
                          + 0.5 * ts_rank(search_vector, phraseto_tsquery('english', :q))) DESC
                LIMIT 50
            ),
            quality AS (
                SELECT id, ROW_NUMBER() OVER (
                    ORDER BY COALESCE(quality_score, 0) DESC
                ) AS rn
                FROM intel_items
                WHERE status = 'processed'
                  AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                  AND id IN (SELECT id FROM semantic UNION SELECT id FROM fulltext)
                {extra_where if extra_where else ''}
            ),
            freshness AS (
                SELECT id, ROW_NUMBER() OVER (
                    ORDER BY COALESCE(published_at, created_at) DESC
                ) AS rn
                FROM intel_items
                WHERE status = 'processed'
                  AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                  AND id IN (SELECT id FROM semantic UNION SELECT id FROM fulltext)
                {extra_where if extra_where else ''}
            ),
            combined AS (
                SELECT COALESCE(s.id, f.id, q.id, fr.id) AS id,
                    0.25 * COALESCE(1.0 / (:rrf_k + s.rn), 0) +
                    0.35 * COALESCE(1.0 / (:rrf_k + f.rn), 0) +
                    0.35 * COALESCE(1.0 / (:rrf_k + q.rn), 0) +
                    0.05 * COALESCE(1.0 / (:rrf_k + fr.rn), 0) AS rrf_score
                FROM semantic s
                FULL OUTER JOIN fulltext f ON s.id = f.id
                FULL OUTER JOIN quality q ON COALESCE(s.id, f.id) = q.id
                FULL OUTER JOIN freshness fr ON COALESCE(s.id, f.id) = fr.id
            )
            SELECT i.id, i.title, i.excerpt, i.summary, i.primary_type, i.tags,
                   i.url, i.relevance_score, i.quality_score, i.quality_score_details,
                   i.confidence_score, i.significance, i.created_at, i.cluster_id,
                   c.rrf_score AS rank
            FROM combined c
            JOIN intel_items i ON i.id = c.id
            ORDER BY c.rrf_score DESC
            LIMIT :overfetch_limit OFFSET :offset
        """
        )

        try:
            result = await session.execute(rrf_sql, rrf_params)
            rows = result.fetchall()

            # Count total candidates (union of semantic + fulltext)
            rrf_count_sql = text(
                f"""
                WITH semantic AS (
                    SELECT id FROM intel_items
                    WHERE status = 'processed' AND embedding IS NOT NULL
                    AND LENGTH(COALESCE(content, '')) >= 100
                    AND COALESCE(quality_score, 0) >= 0.40
                    {extra_where}
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT 50
                ),
                fulltext AS (
                    SELECT id FROM intel_items,
                        websearch_to_tsquery('english', :q) query
                    WHERE status = 'processed' AND search_vector @@ query
                    AND LENGTH(COALESCE(content, '')) >= 100
                    AND COALESCE(quality_score, 0) >= 0.40
                    {extra_where}
                    LIMIT 50
                )
                SELECT COUNT(DISTINCT id) FROM (
                    SELECT id FROM semantic UNION ALL SELECT id FROM fulltext
                ) AS all_candidates
            """
            )
            count_result = await session.execute(
                rrf_count_sql, {**params, "embedding": embedding_str}
            )
            total = count_result.scalar() or 0

            # P24-01c: Check if fulltext returned any matches (for confidence threshold)
            fulltext_count_sql = text(
                f"""
                SELECT COUNT(*) FROM intel_items,
                    websearch_to_tsquery('english', :q) query
                WHERE status = 'processed' AND search_vector @@ query
                {extra_where}
            """
            )
            ft_result = await session.execute(fulltext_count_sql, params)
            _fulltext_count = ft_result.scalar() or 0

            logger.debug(
                "search_hybrid_rrf",
                query=q[:50],
                results=len(rows),
                total=total,
                fulltext_count=_fulltext_count,
            )

            # Intent fallback: if intent routing produced < 3 results, retry without intent filters
            if len(rows) < 3 and _intent_applied:
                logger.debug(
                    "search_intent_fallback", query=q[:50], intent_results=len(rows)
                )
                fallback_extra_where = (
                    (" AND " + " AND ".join(_pre_intent_clauses))
                    if _pre_intent_clauses
                    else ""
                )
                fallback_rrf_sql = text(
                    f"""
                    WITH semantic AS (
                        SELECT id, ROW_NUMBER() OVER (
                            ORDER BY embedding <=> CAST(:embedding AS vector)
                        ) AS rn
                        FROM intel_items
                        WHERE status = 'processed'
                          AND embedding IS NOT NULL
                          AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                          AND LENGTH(COALESCE(content, '')) >= 100
                          AND COALESCE(quality_score, 0) >= 0.40
                          {fallback_extra_where}
                        ORDER BY embedding <=> CAST(:embedding AS vector)
                        LIMIT 50
                    ),
                    fulltext AS (
                        SELECT id, ROW_NUMBER() OVER (
                            ORDER BY (ts_rank_cd(search_vector, websearch_to_tsquery('english', :q))
                                      + 0.5 * ts_rank(search_vector, phraseto_tsquery('english', :q))) DESC
                        ) AS rn
                        FROM intel_items,
                             websearch_to_tsquery('english', :q) query
                        WHERE status = 'processed'
                          AND search_vector @@ query
                          AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                          AND LENGTH(COALESCE(content, '')) >= 100
                          AND COALESCE(quality_score, 0) >= 0.40
                          {fallback_extra_where}
                        ORDER BY (ts_rank_cd(search_vector, query)
                                  + 0.5 * ts_rank(search_vector, phraseto_tsquery('english', :q))) DESC
                        LIMIT 50
                    ),
                    quality AS (
                        SELECT id, ROW_NUMBER() OVER (
                            ORDER BY COALESCE(quality_score, 0) DESC
                        ) AS rn
                        FROM intel_items
                        WHERE status = 'processed'
                          AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                          AND id IN (SELECT id FROM semantic UNION SELECT id FROM fulltext)
                        {fallback_extra_where if fallback_extra_where else ''}
                    ),
                    freshness AS (
                        SELECT id, ROW_NUMBER() OVER (
                            ORDER BY COALESCE(published_at, created_at) DESC
                        ) AS rn
                        FROM intel_items
                        WHERE status = 'processed'
                          AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                          AND id IN (SELECT id FROM semantic UNION SELECT id FROM fulltext)
                        {fallback_extra_where if fallback_extra_where else ''}
                    ),
                    combined AS (
                        SELECT COALESCE(s.id, f.id, q.id, fr.id) AS id,
                            0.25 * COALESCE(1.0 / (:rrf_k + s.rn), 0) +
                            0.35 * COALESCE(1.0 / (:rrf_k + f.rn), 0) +
                            0.35 * COALESCE(1.0 / (:rrf_k + q.rn), 0) +
                            0.05 * COALESCE(1.0 / (:rrf_k + fr.rn), 0) AS rrf_score
                        FROM semantic s
                        FULL OUTER JOIN fulltext f ON s.id = f.id
                        FULL OUTER JOIN quality q ON COALESCE(s.id, f.id) = q.id
                        FULL OUTER JOIN freshness fr ON COALESCE(s.id, f.id) = fr.id
                    )
                    SELECT i.id, i.title, i.excerpt, i.summary, i.primary_type, i.tags,
                           i.url, i.relevance_score, i.quality_score, i.quality_score_details,
                           i.confidence_score, i.significance, i.created_at, i.cluster_id,
                           c.rrf_score AS rank
                    FROM combined c
                    JOIN intel_items i ON i.id = c.id
                    ORDER BY c.rrf_score DESC
                    LIMIT :overfetch_limit OFFSET :offset
                    """
                )
                fallback_params = {
                    **_pre_intent_params,
                    "embedding": embedding_str,
                    "overfetch_limit": overfetch_limit,
                    "offset": offset,
                    "rrf_k": _RRF_K,
                }
                result = await session.execute(fallback_rrf_sql, fallback_params)
                rows = result.fetchall()

                # Recount with fallback filters
                fallback_count_sql = text(
                    f"""
                    WITH semantic AS (
                        SELECT id FROM intel_items
                        WHERE status = 'processed' AND embedding IS NOT NULL
                        AND LENGTH(COALESCE(content, '')) >= 100
                        AND COALESCE(quality_score, 0) >= 0.40
                        {fallback_extra_where}
                        ORDER BY embedding <=> CAST(:embedding AS vector)
                        LIMIT 50
                    ),
                    fulltext AS (
                        SELECT id FROM intel_items,
                            websearch_to_tsquery('english', :q) query
                        WHERE status = 'processed' AND search_vector @@ query
                        AND LENGTH(COALESCE(content, '')) >= 100
                        AND COALESCE(quality_score, 0) >= 0.40
                        {fallback_extra_where}
                        LIMIT 50
                    )
                    SELECT COUNT(DISTINCT id) FROM (
                        SELECT id FROM semantic UNION ALL SELECT id FROM fulltext
                    ) AS all_candidates
                    """
                )
                count_result = await session.execute(
                    fallback_count_sql,
                    {**_pre_intent_params, "embedding": embedding_str},
                )
                total = count_result.scalar() or 0

                # Recount fulltext for confidence threshold
                fallback_ft_sql = text(
                    f"""
                    SELECT COUNT(*) FROM intel_items,
                        websearch_to_tsquery('english', :q) query
                    WHERE status = 'processed' AND search_vector @@ query
                    {fallback_extra_where}
                    """
                )
                ft_result = await session.execute(fallback_ft_sql, _pre_intent_params)
                _fulltext_count = ft_result.scalar() or 0

        except Exception as exc:
            logger.warning(
                "search_rrf_failed", error=str(exc)[:200], fallback="text-only"
            )
            await session.rollback()  # Clear failed transaction before fallback
            embedding = None  # trigger text-only fallback below

    # --- Text-only search fallback (when embedding unavailable or RRF failed) ---
    if embedding is None:
        # AND query (precise — all words must match)
        # Hybrid ranking: ts_rank * relevance * quality * freshness + phrase proximity boost
        and_sql = text(
            f"""
            SELECT id, title, excerpt, summary, primary_type, tags, url,
                   relevance_score, quality_score, quality_score_details,
                   confidence_score, significance, created_at, cluster_id,
                   (ts_rank(search_vector, websearch_to_tsquery('english', :q))
                     + 0.5 * ts_rank(search_vector, phraseto_tsquery('english', :q)))
                     * COALESCE(relevance_score, 0.5)
                     * (0.5 + 0.5 * COALESCE(quality_score, 0.5))
                     * EXP(LN(0.5) / 7.0 * EXTRACT(EPOCH FROM (NOW() - COALESCE(published_at, created_at))) / 86400.0)
                     AS rank
            FROM intel_items,
                 websearch_to_tsquery('english', :q) query
            WHERE status = 'processed' AND search_vector @@ query
            AND relevance_score >= 0.60
            AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
            AND LENGTH(COALESCE(content, '')) >= 100
            AND COALESCE(quality_score, 0) >= 0.40
            {extra_where}
            ORDER BY rank DESC
            LIMIT :overfetch_limit OFFSET :offset
        """
        )

        # AND count query
        and_count_sql = text(
            f"""
            SELECT COUNT(*) AS total
            FROM intel_items,
                 websearch_to_tsquery('english', :q) query
            WHERE status = 'processed' AND search_vector @@ query
            AND relevance_score >= 0.60
            AND LENGTH(COALESCE(content, '')) >= 100
            AND COALESCE(quality_score, 0) >= 0.40
            {extra_where}
        """
        )

        # OR count query
        or_count_sql = text(
            f"""
            SELECT COUNT(*) AS total
            FROM intel_items,
                 to_tsquery('english', :q_or) query
            WHERE status = 'processed' AND search_vector @@ query
            AND relevance_score >= 0.65
            AND LENGTH(COALESCE(content, '')) >= 100
            AND COALESCE(quality_score, 0) >= 0.40
            {extra_where}
        """
        )

        # Try AND first
        result = await session.execute(
            and_sql, {**params, "overfetch_limit": overfetch_limit, "offset": offset}
        )
        rows = result.fetchall()
        used_or = False

        # Fall back to OR only if AND returns fewer than 3 results.
        if len(rows) < 3:
            or_filtered_sql = text(
                f"""
                SELECT id, title, excerpt, summary, primary_type, tags, url,
                       relevance_score, created_at, cluster_id,
                       (ts_rank(search_vector, to_tsquery('english', :q_or))
                         + 0.5 * ts_rank(search_vector, phraseto_tsquery('english', :q)))
                         * (0.5 + 0.5 * COALESCE(quality_score, 0.5))
                         * EXP(LN(0.5) / 7.0 * EXTRACT(EPOCH FROM (NOW() - COALESCE(published_at, created_at))) / 86400.0)
                         AS rank
                FROM intel_items,
                     to_tsquery('english', :q_or) query
                WHERE status = 'processed' AND search_vector @@ query
                AND relevance_score >= 0.65
                AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                AND LENGTH(COALESCE(content, '')) >= 100
                AND COALESCE(quality_score, 0) >= 0.40
                {extra_where}
                ORDER BY rank DESC
                LIMIT :overfetch_limit OFFSET :offset
            """
            )
            result = await session.execute(
                or_filtered_sql,
                {**params, "overfetch_limit": overfetch_limit, "offset": offset},
            )
            or_rows = result.fetchall()
            and_ids = {r._mapping["id"] for r in rows}
            merged = list(rows)
            for r in or_rows:
                if r._mapping["id"] not in and_ids:
                    merged.append(r)
            rows = merged[:overfetch_limit]
            used_or = len(merged) > len(list(and_ids))

        # True total count via separate COUNT query
        count_sql = or_count_sql if used_or else and_count_sql
        count_result = await session.execute(count_sql, params)
        total = count_result.scalar() or 0

        # Intent fallback for text-only path
        if len(rows) < 3 and _intent_applied:
            logger.debug(
                "search_intent_fallback_text", query=q[:50], intent_results=len(rows)
            )
            fb_extra_where = (
                (" AND " + " AND ".join(_pre_intent_clauses))
                if _pre_intent_clauses
                else ""
            )
            fb_and_sql = text(
                f"""
                SELECT id, title, excerpt, summary, primary_type, tags, url,
                       relevance_score, quality_score, quality_score_details,
                       confidence_score, significance, created_at, cluster_id,
                       (ts_rank(search_vector, websearch_to_tsquery('english', :q))
                         + 0.5 * ts_rank(search_vector, phraseto_tsquery('english', :q)))
                         * COALESCE(relevance_score, 0.5)
                         * (0.5 + 0.5 * COALESCE(quality_score, 0.5))
                         * EXP(LN(0.5) / 7.0 * EXTRACT(EPOCH FROM (NOW() - COALESCE(published_at, created_at))) / 86400.0)
                         AS rank
                FROM intel_items,
                     websearch_to_tsquery('english', :q) query
                WHERE status = 'processed' AND search_vector @@ query
                AND relevance_score >= 0.60
                AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
                AND LENGTH(COALESCE(content, '')) >= 100
                AND COALESCE(quality_score, 0) >= 0.40
                {fb_extra_where}
                ORDER BY rank DESC
                LIMIT :overfetch_limit OFFSET :offset
                """
            )
            result = await session.execute(
                fb_and_sql,
                {
                    **_pre_intent_params,
                    "overfetch_limit": overfetch_limit,
                    "offset": offset,
                },
            )
            rows = result.fetchall()

            fb_count_sql = text(
                f"""
                SELECT COUNT(*) AS total
                FROM intel_items,
                     websearch_to_tsquery('english', :q) query
                WHERE status = 'processed' AND search_vector @@ query
                AND relevance_score >= 0.60
                AND LENGTH(COALESCE(content, '')) >= 100
                AND COALESCE(quality_score, 0) >= 0.40
                {fb_extra_where}
                """
            )
            count_result = await session.execute(fb_count_sql, _pre_intent_params)
            total = count_result.scalar() or 0

    # P24-01a: Collapse cluster duplicates — keep best representative per cluster_id
    rows = collapse_clusters(rows, rank_key="rank")

    # P1-11: Deduplicate by base URL (strip #fragment), keep highest-ranked per base URL
    seen_base_urls: dict[str, bool] = {}
    deduped_rows = []
    for row in rows:
        mapping = row._mapping
        url = mapping.get("url") or ""
        base_url = url.split("#")[0]
        if base_url in seen_base_urls:
            continue
        seen_base_urls[base_url] = True
        deduped_rows.append(row)

    # Trim to requested limit after dedup
    deduped_rows = deduped_rows[:limit]

    # Convert rows to response
    items = []
    for row in deduped_rows:
        mapping = row._mapping
        items.append(
            SearchResultResponse(
                id=mapping["id"],
                title=mapping["title"],
                excerpt=mapping["excerpt"],
                summary=mapping["summary"],
                primary_type=mapping["primary_type"],
                tags=mapping["tags"],
                url=mapping.get("url"),
                relevance_score=mapping["relevance_score"],
                quality_score=mapping.get("quality_score"),
                quality_score_details=mapping.get("quality_score_details"),
                confidence_score=mapping.get("confidence_score"),
                significance=mapping.get("significance"),
                rank=mapping["rank"],
                created_at=mapping["created_at"],
            )
        )

    # P24-01c: Minimum confidence threshold — return empty for out-of-scope queries.
    # Two-path thresholding:
    # - RRF path: check cosine distance + fulltext match presence (RRF scores have a
    #   high floor of ~0.009 due to 1/(k+rn), so raw score thresholds don't work).
    #   If top result has cosine_dist > 0.55 AND no fulltext matches in top results,
    #   the query is semantically distant and has no keyword overlap — off-topic.
    # - Text-only fallback: use rank threshold (ts_rank * relevance * quality * freshness).
    MIN_RANK_THRESHOLD = 0.001
    _MIN_FULLTEXT_COUNT = 3
    _MIN_AVG_RELEVANCE = 0.60
    if embedding is not None and items:
        # RRF path: two-layer confidence check
        # Layer 1: if < 3 fulltext matches, query words barely appear in corpus → off-topic
        # Layer 2: if fulltext matches exist but avg relevance of top results is low,
        #   the matched items are borderline (e.g., "iPhone review" hits tangential items
        #   with relevance 0.50-0.63 while "Claude Code hooks" hits core items at 0.77+)
        _low_confidence = _fulltext_count < _MIN_FULLTEXT_COUNT
        if not _low_confidence and len(items) >= 3:
            top_n = min(len(items), 5)
            avg_rel = sum((it.relevance_score or 0) for it in items[:top_n]) / top_n
            _low_confidence = avg_rel < _MIN_AVG_RELEVANCE
    elif items:
        _low_confidence = items[0].rank < MIN_RANK_THRESHOLD
    else:
        _low_confidence = False
    if _low_confidence:
        note = (
            "No high-confidence results found for this query. "
            "Try different keywords or check if this topic is within "
            "Intel Overdrive's coverage (AI coding tools & practices)."
        )
        search_response = SearchResponse(
            items=[], total=0, offset=offset, limit=limit, warning=note
        )
        response_dict = search_response.model_dump(mode="json")
        response_dict["note"] = note

        # Cache the empty response
        if _cache_key and redis:
            try:
                response_json = json.dumps(response_dict, default=str)
                await set_cached_response(redis, _cache_key, response_json)
            except Exception:
                pass

        return JSONResponse(content=response_dict)

    # P1-17: Out-of-scope detection — warn when avg relevance is low
    warning = None
    if items:
        avg_relevance = sum((item.relevance_score or 0) for item in items) / len(items)
        if avg_relevance < 0.3:
            warning = (
                "These results may be outside Intel Overdrive's coverage area "
                "(AI coding tools & practices). Results shown are best-effort matches."
            )

    search_response = SearchResponse(
        items=items, total=total, offset=offset, limit=limit, warning=warning
    )
    response_dict = search_response.model_dump(mode="json")

    # Also search the synthesized knowledge library (max 3 results)
    # P1-12: Escape ILIKE wildcards in user query words
    or_words = re.findall(r"[a-zA-Z0-9]+", q.lower())
    if or_words:
        # Score: topic_path match = 10, title match = 5, tldr match = 2, body match = 1
        score_parts = []
        lib_params: dict = {}
        for i, w in enumerate(or_words):
            p = f"lw{i}"
            lib_params[p] = f"%{escape_ilike(w)}%"
            score_parts.append(
                f"(CASE WHEN li.topic_path ILIKE :{p} THEN 10 ELSE 0 END "
                f"+ CASE WHEN li.title ILIKE :{p} THEN 5 ELSE 0 END "
                f"+ CASE WHEN li.tldr ILIKE :{p} THEN 2 ELSE 0 END)"
            )
        score_expr = " + ".join(score_parts)

        # Only match on topic_path, title, or tldr — NOT body (too broad)
        match_clauses = " OR ".join(
            f"li.topic_path ILIKE :lw{i} OR li.title ILIKE :lw{i} OR li.tldr ILIKE :lw{i}"
            for i in range(len(or_words))
        )

        lib_sql = text(
            f"""
            SELECT li.slug, li.title, li.tldr, li.key_points, li.gotchas,
                   li.topic_path, ({score_expr}) AS match_score
            FROM library_items li
            WHERE li.status = 'active' AND li.is_current = TRUE
            AND ({match_clauses})
            ORDER BY match_score DESC
            LIMIT 3
        """
        )
        lib_result = await session.execute(lib_sql, lib_params)
        lib_rows = lib_result.fetchall()
        if lib_rows:
            response_dict["library"] = [
                {
                    "slug": r.slug,
                    "title": r.title,
                    "tldr": r.tldr,
                    "key_points": r.key_points,
                    "gotchas": r.gotchas,
                    "topic": r.topic_path,
                }
                for r in lib_rows
            ]

    # Field selector
    if fields:
        requested = {f.strip() for f in fields.split(",") if f.strip()}
        requested.add("id")
        response_dict["items"] = [
            {k: v for k, v in item.items() if k in requested}
            for item in response_dict["items"]
        ]
        # Strip top-level library section too -- it's not in the requested fields
        response_dict.pop("library", None)

    # Cache the response on miss
    if _cache_key and redis:
        try:
            response_json = json.dumps(response_dict, default=str)
            await set_cached_response(redis, _cache_key, response_json)
        except Exception:
            pass

    return JSONResponse(content=response_dict)
