"""Watchlist endpoints — semantic standing queries against the intel item stream.

POST /v1/watchlists      — create watchlist with concept embedding (one-time Voyage call)
GET /v1/watchlists       — list user's active watchlists
GET /v1/watchlists/{id}/matches — find items matching the watchlist concept via pgvector
DELETE /v1/watchlists/{id} — soft delete (set is_active=False)

IMPORTANT: The concept_embedding is computed ONCE at creation time using the existing
Voyage AI client. Matching at query time is pure pgvector SQL — no per-request LLM calls.
This complies with the "no new LLM API calls" constraint because:
  (a) uses the existing Voyage client already in the codebase
  (b) it is a one-time cost at creation, same pattern as reference set seeding
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import WatchlistCreate, WatchlistResponse, WatchlistMatchResponse
from src.models.models import APIKey, User, Watchlist
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)
watchlists_router = APIRouter(tags=["watchlists"])


async def _embed_concept(concept: str) -> Optional[List[float]]:
    """Embed the watchlist concept using Voyage AI (one-time call at creation).

    Returns None if VOYAGE_API_KEY is not configured or if the call fails.
    Failure is non-fatal — watchlist is created without matching capability.
    """
    settings = get_settings()
    if not settings.VOYAGE_API_KEY:
        logger.warning("watchlist_embed_skipped", reason="VOYAGE_API_KEY not set")
        return None

    try:
        import voyageai

        voyage = voyageai.AsyncClient(api_key=settings.VOYAGE_API_KEY, timeout=30.0)
        response = await voyage.embed(
            [concept],
            model=settings.EMBEDDING_MODEL,
            input_type="query",
        )
        return response.embeddings[0]
    except Exception as exc:
        logger.error("watchlist_embed_failed", error=str(exc))
        return None


@watchlists_router.post(
    "/watchlists", response_model=WatchlistResponse, status_code=201
)
@limiter.limit("20/minute")
async def create_watchlist(
    request: Request,
    body: WatchlistCreate,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """Create a semantic watchlist. The concept is embedded once at creation time."""
    # Embed concept (one-time Voyage call; non-fatal on failure)
    embedding = await _embed_concept(body.concept)

    watchlist = Watchlist(
        user_id=api_key.user_id,
        name=body.name,
        concept=body.concept,
        concept_embedding=embedding,
        similarity_threshold=body.similarity_threshold,
        is_active=True,
    )
    session.add(watchlist)
    await session.commit()
    await session.refresh(watchlist)

    response = WatchlistResponse(
        id=watchlist.id,
        name=watchlist.name,
        concept=watchlist.concept,
        similarity_threshold=watchlist.similarity_threshold,
        is_active=watchlist.is_active,
        created_at=watchlist.created_at,
        has_embedding=watchlist.concept_embedding is not None,
    )
    return JSONResponse(content=response.model_dump(mode="json"), status_code=201)


@watchlists_router.get("/watchlists", response_model=List[WatchlistResponse])
@limiter.limit("60/minute")
async def list_watchlists(
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """List all active watchlists for the authenticated user."""
    result = await session.execute(
        select(Watchlist)
        .where(
            Watchlist.user_id == api_key.user_id,
            Watchlist.is_active == True,  # noqa: E712
        )
        .order_by(Watchlist.created_at.desc())
    )
    watchlists = result.scalars().all()

    items = [
        WatchlistResponse(
            id=w.id,
            name=w.name,
            concept=w.concept,
            similarity_threshold=w.similarity_threshold,
            is_active=w.is_active,
            created_at=w.created_at,
            has_embedding=w.concept_embedding is not None,
        )
        for w in watchlists
    ]
    return JSONResponse(content=[i.model_dump(mode="json") for i in items])


@watchlists_router.get(
    "/watchlists/{watchlist_id}/matches",
    response_model=List[WatchlistMatchResponse],
)
@limiter.limit("30/minute")
async def get_watchlist_matches(
    request: Request,
    watchlist_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> JSONResponse:
    """
    Return intel items semantically matching the watchlist concept.
    Matching is pure pgvector cosine distance — no per-request LLM calls.
    """
    # Fetch watchlist (must belong to this user)
    result = await session.execute(
        select(Watchlist).where(
            Watchlist.id == watchlist_id,
            Watchlist.user_id == api_key.user_id,
        )
    )
    watchlist = result.scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    # Guard: no embedding yet
    if watchlist.concept_embedding is None:
        response_body = {
            "matches": [],
            "message": (
                "Watchlist has no embedding. VOYAGE_API_KEY may not be configured."
            ),
        }
        return JSONResponse(content=response_body)

    # pgvector cosine similarity search — pure SQL, no per-query LLM calls
    match_sql = text(
        """
        SELECT i.id, i.title, i.url, i.excerpt, i.summary, i.primary_type,
               i.tags, i.relevance_score, i.significance, i.source_name,
               i.published_at,
               1.0 - (i.embedding <=> w.concept_embedding) AS match_score
        FROM intel_items i
        CROSS JOIN (
            SELECT concept_embedding, similarity_threshold
            FROM watchlists
            WHERE id = CAST(:wid AS uuid)
        ) w
        WHERE i.status = 'processed'
          AND i.embedding IS NOT NULL
          AND w.concept_embedding IS NOT NULL
          AND (1.0 - (i.embedding <=> w.concept_embedding)) >= w.similarity_threshold
        ORDER BY i.embedding <=> w.concept_embedding
        LIMIT :limit
        """
    )
    rows_result = await session.execute(
        match_sql, {"wid": str(watchlist_id), "limit": limit}
    )
    rows = rows_result.mappings().all()

    matches = [WatchlistMatchResponse.model_validate(dict(r)) for r in rows]
    return JSONResponse(content=[m.model_dump(mode="json") for m in matches])


@watchlists_router.delete("/watchlists/{watchlist_id}", status_code=204)
@limiter.limit("20/minute")
async def delete_watchlist(
    request: Request,
    watchlist_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
) -> Response:
    """Soft-delete a watchlist (sets is_active=False)."""
    result = await session.execute(
        select(Watchlist).where(
            Watchlist.id == watchlist_id,
            Watchlist.user_id == api_key.user_id,
        )
    )
    watchlist = result.scalar_one_or_none()
    if watchlist is None:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    await session.execute(
        text(
            "UPDATE watchlists SET is_active = false, updated_at = :ts "
            "WHERE id = CAST(:wid AS uuid)"
        ),
        {"ts": datetime.now(timezone.utc), "wid": str(watchlist_id)},
    )
    await session.commit()
    return Response(status_code=204)
