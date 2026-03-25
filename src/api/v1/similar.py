"""GET /v1/similar/{item_id} — semantically similar items via pgvector cosine distance.
GET /v1/similar?concept=<text> — concept-based similarity search via Voyage embedding.
"""
import json
import uuid
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.query_logger import log_query
from src.api.schemas import SimilarItemResponse, SimilarResponse
from src.api.cache import (
    make_cache_key,
    get_cached_response,
    set_cached_response,
    is_cache_enabled,
    get_redis_from_request,
)
from src.core.config import get_settings
from src.core.logger import get_logger
from src.models.models import APIKey

logger = get_logger(__name__)
similar_router = APIRouter(tags=["similar"])

_SIM_SQL = """
    SELECT i.id, i.title, i.url, i.excerpt, i.summary, i.primary_type,
           i.tags, i.relevance_score, i.significance, i.created_at,
           i.source_name, i.published_at,
           1.0 - (i.embedding <=> CAST(:embedding AS vector)) AS similarity
    FROM intel_items i
    WHERE i.status = 'processed'
      AND i.embedding IS NOT NULL
      AND i.embedding <=> CAST(:embedding AS vector) < 0.45
    ORDER BY i.embedding <=> CAST(:embedding AS vector)
    LIMIT :limit
"""


async def _embed_concept(
    concept: str, request: Request = None
) -> Optional[List[float]]:
    """Embed a free-text concept using Voyage AI, with Redis cache.

    First checks Redis for a cached vector (key: concept_embed:<sha256>).
    On cache miss, calls Voyage (~$0.00002), stores result in Redis with 24h TTL.
    Repeat queries for the same concept are free.
    """
    import hashlib
    import json as _json

    cache_key = (
        f"concept_embed:{hashlib.sha256(concept.lower().strip().encode()).hexdigest()}"
    )

    # Try Redis cache first
    redis = getattr(request.app.state, "redis", None) if request else None
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                logger.debug("concept_embed_cache_hit", concept=concept[:30])
                return _json.loads(cached)
        except Exception:
            pass  # Cache miss or Redis error — fall through to Voyage

    settings = get_settings()
    if not settings.VOYAGE_API_KEY:
        logger.warning("concept_embed_skipped", reason="VOYAGE_API_KEY not set")
        return None
    try:
        import voyageai

        voyage = voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY, timeout=30.0)
        response = await voyage.embed(
            [concept],
            model=settings.EMBEDDING_MODEL,
            input_type="query",
        )
        embedding = response.embeddings[0]

        # Cache in Redis with 24h TTL
        if redis:
            try:
                await redis.set(cache_key, _json.dumps(embedding), ex=86400)
                logger.debug("concept_embed_cached", concept=concept[:30])
            except Exception:
                pass  # Cache write failure is non-critical

        return embedding
    except Exception as exc:
        logger.error("concept_embed_failed", error=str(exc))
        return None


@similar_router.get("/similar", response_model=SimilarResponse)
@limiter.limit("30/minute")
async def get_similar_by_concept(
    request: Request,
    response: Response,
    concept: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="Free-text concept to find similar items for",
    ),
    limit: int = Query(10, ge=1, le=50, description="Max similar items to return"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """
    Finds intel items semantically similar to the given free-text concept.
    Embeds the concept via Voyage AI (one call per request), then runs a
    pgvector cosine distance query. Returns 503 if embedding service unavailable.
    """
    # Query logging BEFORE cache check — ensures every query is always counted
    try:
        await log_query(session, api_key.id, "similar", concept, 0)
    except Exception:
        pass

    # Response cache check
    _cache_key = None
    _sim_redis = get_redis_from_request(request)
    if is_cache_enabled() and _sim_redis:
        _cache_key = make_cache_key(
            "similar-concept", {"concept": concept, "limit": limit}
        )
        _cached = await get_cached_response(_sim_redis, _cache_key)
        if _cached is not None:
            return JSONResponse(content=json.loads(_cached))

    embedding = await _embed_concept(concept, request=request)
    if embedding is None:
        raise HTTPException(
            status_code=503,
            detail="Embedding service unavailable. VOYAGE_API_KEY may not be configured.",
        )

    embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
    result = await session.execute(
        text(_SIM_SQL), {"embedding": embedding_str, "limit": limit}
    )
    rows = result.mappings().all()
    items = [
        SimilarItemResponse.model_validate(dict(r)).model_dump(mode="json")
        for r in rows
    ]
    _response_dict = {"items": items, "total": len(items)}

    # Cache the response on miss
    if _cache_key and _sim_redis:
        try:
            await set_cached_response(
                _sim_redis, _cache_key, json.dumps(_response_dict, default=str)
            )
        except Exception:
            pass

    return JSONResponse(content=_response_dict)


@similar_router.get("/similar/{item_id}", response_model=SimilarResponse)
@limiter.limit("60/minute")
async def get_similar(
    request: Request,
    response: Response,
    item_id: uuid.UUID,
    limit: int = Query(10, ge=1, le=50, description="Max similar items to return"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> list[SimilarItemResponse]:
    """
    Returns semantically similar processed items to the given item,
    ranked by cosine similarity (highest first).
    Returns 404 if the item doesn't exist or has no embedding yet.
    """
    # Query logging BEFORE cache check
    try:
        await log_query(session, api_key.id, "similar", str(item_id), 0)
    except Exception:
        pass

    # Response cache check
    _id_cache_key = None
    _id_redis = get_redis_from_request(request)
    if is_cache_enabled() and _id_redis:
        _id_cache_key = make_cache_key(
            "similar-id", {"item_id": str(item_id), "limit": limit}
        )
        _id_cached = await get_cached_response(_id_redis, _id_cache_key)
        if _id_cached is not None:
            return JSONResponse(content=json.loads(_id_cached))

    # Guard: reference item must exist with a non-null embedding
    check_sql = text(
        """
        SELECT embedding FROM intel_items
        WHERE id = CAST(:item_id AS uuid) AND embedding IS NOT NULL
        """
    )
    ref = await session.execute(check_sql, {"item_id": str(item_id)})
    ref_row = ref.fetchone()
    if ref_row is None:
        raise HTTPException(
            status_code=404, detail="Item not found or not yet embedded"
        )

    sim_sql = text(
        """
        SELECT i.id, i.title, i.url, i.excerpt, i.summary, i.primary_type,
               i.tags, i.relevance_score, i.significance, i.created_at,
               i.source_name, i.published_at,
               1.0 - (i.embedding <=> ref.embedding) AS similarity
        FROM intel_items i
        CROSS JOIN (
            SELECT embedding FROM intel_items
            WHERE id = CAST(:item_id AS uuid) AND embedding IS NOT NULL
        ) ref
        WHERE i.status = 'processed'
          AND i.id != CAST(:item_id AS uuid)
          AND i.embedding IS NOT NULL
          AND i.embedding <=> ref.embedding < 0.45
        ORDER BY i.embedding <=> ref.embedding
        LIMIT :limit
        """
    )
    result = await session.execute(sim_sql, {"item_id": str(item_id), "limit": limit})
    rows = result.mappings().all()
    items = [
        SimilarItemResponse.model_validate(dict(r)).model_dump(mode="json")
        for r in rows
    ]
    _id_response_dict = {"items": items, "total": len(items)}

    # Cache the response on miss
    if _id_cache_key and _id_redis:
        try:
            await set_cached_response(
                _id_redis, _id_cache_key, json.dumps(_id_response_dict, default=str)
            )
        except Exception:
            pass

    return JSONResponse(content=_id_response_dict)
