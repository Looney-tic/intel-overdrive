"""GET /v1/threads — storyline threading using cluster_id from Phase 9.

Groups related intel items by cluster_id into story threads, ordered by
recency and signal momentum. Narrative summaries are built from item titles
and tags using pure Python string concatenation — no LLM calls.
"""

import math
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import (
    IntelItemResponse,
    ThreadDetailResponse,
    ThreadResponse,
    ThreadsListResponse,
    ThreadTopItem,
)
from src.models.models import APIKey
from src.core.logger import get_logger

threads_router = APIRouter(tags=["threads"])
logger = get_logger(__name__)


def _build_narrative(items: list, thread: dict) -> str:
    """Build deterministic narrative from items + thread metadata. No LLM.

    Leads with the most significant item's summary (actionable intelligence),
    then gives a count line with significance distribution.
    """
    # Collect top tags across all items
    all_tags: list[str] = []
    for item in items:
        all_tags.extend(item.get("tags") or [])
    tag_counts: dict[str, int] = {}
    for t in all_tags:
        tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tags = (
        ", ".join(sorted(tag_counts, key=lambda k: -tag_counts[k])[:3]) or "general"
    )

    n = thread.get("item_count", len(items))

    # Lead with most significant item's summary
    best_item = items[0] if items else None
    lead = ""
    if best_item:
        summary = best_item.get("summary") or best_item.get("title", "")
        lead = summary[:150] + "..." if len(summary) > 150 else summary

    # Significance distribution
    sig_counts: dict[str, int] = {}
    for item in items:
        s = item.get("significance") or "informational"
        sig_counts[s] = sig_counts.get(s, 0) + 1
    sig_order = {"breaking": 0, "major": 1, "minor": 2, "informational": 3}
    sig_dist = ", ".join(
        f"{v} {k}"
        for k, v in sorted(sig_counts.items(), key=lambda x: sig_order.get(x[0], 4))
    )

    first_seen = thread.get("first_seen")
    last_seen = thread.get("last_seen")
    first = first_seen.strftime("%b %d") if first_seen else "?"
    last = last_seen.strftime("%b %d") if last_seen else "?"

    return (
        f"{lead}\n\n"
        f"{n} items about {top_tags} ({first} \u2013 {last}). "
        f"Breakdown: {sig_dist}."
    )


@threads_router.get("/threads", response_model=ThreadsListResponse)
@limiter.limit("60/minute")
async def list_threads(
    request: Request,
    days: int = Query(
        30, ge=7, le=365, description="Recency window for thread discovery"
    ),
    min_items: int = Query(
        2, ge=2, le=50, description="Minimum items to form a thread"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10_000_000),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns active storyline threads grouped by cluster_id.

    Each thread groups related items discovered by the cluster worker (Phase 9).
    Threads are ordered by recency (most recently updated first).
    Momentum score combines avg relevance, item count, and recency factor.
    Narrative summary is built from item titles and tags — no LLM calls.
    """
    # Aggregate thread stats via GROUP BY cluster_id
    thread_sql = text(
        """
        SELECT
            i.cluster_id,
            COUNT(DISTINCT i.id)                                                               AS item_count,
            MIN(i.created_at)                                                                  AS first_seen,
            MAX(i.created_at)                                                                  AS last_seen,
            AVG(i.relevance_score)                                                             AS avg_relevance,
            CASE WHEN MAX(i.created_at) >= NOW() - INTERVAL '7 days' THEN 2.0 ELSE 1.0 END   AS recency_factor,
            COALESCE(SUM(sig_counts.upvotes), 0)                                               AS total_upvotes,
            MODE() WITHIN GROUP (ORDER BY i.significance)                                      AS dominant_significance
        FROM intel_items i
        LEFT JOIN (
            SELECT item_id, COUNT(*) FILTER (WHERE action = 'upvote') AS upvotes
            FROM item_signals
            GROUP BY item_id
        ) sig_counts ON sig_counts.item_id = i.id
        WHERE i.status = 'processed'
          AND i.cluster_id IS NOT NULL
          AND i.created_at >= NOW() - INTERVAL '1 day' * :days
        GROUP BY i.cluster_id
        HAVING COUNT(DISTINCT i.id) >= :min_items
        ORDER BY MAX(i.created_at) DESC
        LIMIT :limit OFFSET :offset
        """
    )

    result = await session.execute(
        thread_sql,
        {"days": days, "min_items": min_items, "limit": limit, "offset": offset},
    )
    rows = result.mappings().all()

    # Count total threads (without pagination)
    count_sql = text(
        """
        SELECT COUNT(*) FROM (
            SELECT i.cluster_id
            FROM intel_items i
            WHERE i.status = 'processed'
              AND i.cluster_id IS NOT NULL
              AND i.created_at >= NOW() - INTERVAL '1 day' * :days
            GROUP BY i.cluster_id
            HAVING COUNT(DISTINCT i.id) >= :min_items
        ) t
        """
    )
    count_result = await session.execute(
        count_sql, {"days": days, "min_items": min_items}
    )
    total = count_result.scalar() or 0

    if not rows:
        return JSONResponse(
            content=ThreadsListResponse(
                threads=[], total=0, offset=offset, limit=limit
            ).model_dump(mode="json")
        )

    # Compute raw momentum scores for normalization
    thread_rows = [dict(r) for r in rows]
    raw_momentums = []
    for tr in thread_rows:
        avg_rel = float(tr.get("avg_relevance") or 0.0)
        cnt = int(tr.get("item_count") or 1)
        rec_factor = float(tr.get("recency_factor") or 1.0)
        raw_momentums.append(avg_rel * math.log(1 + cnt) * rec_factor)

    max_momentum = max(raw_momentums) if raw_momentums else 1.0

    # Single batch query: top 3 items per cluster using ROW_NUMBER() PARTITION BY.
    # Replaces N per-thread queries with exactly 1 query for all thread top items.
    cluster_ids = [tr["cluster_id"] for tr in thread_rows]
    top_items_sql = text(
        """
        SELECT * FROM (
            SELECT id, title, url, significance, primary_type, source_name,
                   created_at, summary, excerpt, tags, cluster_id,
                   ROW_NUMBER() OVER (PARTITION BY cluster_id ORDER BY relevance_score DESC) AS rn
            FROM intel_items
            WHERE cluster_id = ANY(:cluster_ids) AND status = 'processed'
        ) ranked
        WHERE rn <= 3
        """
    )
    all_top_result = await session.execute(top_items_sql, {"cluster_ids": cluster_ids})
    all_top_rows = [dict(r) for r in all_top_result.mappings().all()]

    # Group by cluster_id for O(1) lookup in the build loop below
    top_by_cluster: dict[str, list[dict]] = {}
    for row in all_top_rows:
        top_by_cluster.setdefault(row["cluster_id"], []).append(row)

    # Build ThreadResponse objects
    threads: list[ThreadResponse] = []
    for tr, raw_mom in zip(thread_rows, raw_momentums):
        cluster_id = tr["cluster_id"]
        momentum_score = raw_mom / max_momentum if max_momentum > 0 else 0.0

        top_rows = top_by_cluster.get(cluster_id, [])

        narrative = _build_narrative(top_rows, tr)

        top_items_out = [
            ThreadTopItem(
                id=row["id"],
                title=row["title"],
                url=row["url"],
                summary=row.get("summary"),
                primary_type=row["primary_type"],
                significance=row.get("significance"),
                source_name=row.get("source_name"),
                created_at=row["created_at"],
            )
            for row in top_rows
        ]

        threads.append(
            ThreadResponse(
                thread_id=cluster_id,
                item_count=int(tr["item_count"]),
                first_seen=tr["first_seen"],
                last_seen=tr["last_seen"],
                momentum_score=round(momentum_score, 4),
                total_upvotes=int(tr["total_upvotes"]),
                dominant_significance=tr.get("dominant_significance"),
                narrative_summary=narrative,
                top_items=top_items_out,
            )
        )

    response = ThreadsListResponse(
        threads=threads,
        total=total,
        offset=offset,
        limit=limit,
    )
    return JSONResponse(content=response.model_dump(mode="json"))


@threads_router.get("/threads/{cluster_id}", response_model=ThreadDetailResponse)
@limiter.limit("60/minute")
async def get_thread_detail(
    request: Request,
    cluster_id: str,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns full detail for a single storyline thread.

    Returns all items in the cluster sorted by recency, plus narrative_summary,
    momentum_score, and signal counts. Returns 404 if the cluster does not exist
    or contains fewer than 2 processed items.
    """
    # Fetch all items in this cluster
    items_sql = text(
        """
        SELECT i.id, i.source_id, i.url, i.title, i.excerpt, i.summary,
               i.primary_type, i.tags, i.significance, i.relevance_score,
               i.quality_score, i.quality_score_details, i.confidence_score,
               i.status, i.created_at, i.published_at, i.source_name,
               i.cluster_id, i.contrarian_signals
        FROM intel_items i
        WHERE i.cluster_id = :cid AND i.status = 'processed'
        ORDER BY i.created_at DESC
        """
    )
    items_result = await session.execute(items_sql, {"cid": cluster_id})
    item_rows = [dict(r) for r in items_result.mappings().all()]

    if len(item_rows) < 2:
        raise HTTPException(
            status_code=404,
            detail=f"Thread '{cluster_id}' not found or has fewer than 2 items",
        )

    # Compute thread-level aggregates in Python
    avg_relevance = sum(r.get("relevance_score") or 0.0 for r in item_rows) / len(
        item_rows
    )
    first_seen: Optional[datetime] = min(
        r["created_at"] for r in item_rows if r.get("created_at")
    )
    last_seen: Optional[datetime] = max(
        r["created_at"] for r in item_rows if r.get("created_at")
    )
    recency_factor = (
        2.0
        if last_seen
        and (last_seen >= datetime.now(last_seen.tzinfo) - timedelta(days=7))
        else 1.0
    )
    raw_momentum = avg_relevance * math.log(1 + len(item_rows)) * recency_factor
    # Normalize to 0-1 using a fixed ceiling of 10.0.
    # Theoretical max: avg_relevance(≤1.0) * log(1+50) * 2.0 ≈ 7.8; 10.0 is a safe ceiling.
    momentum_score = min(1.0, raw_momentum / 10.0)

    # Signal upvote counts
    upvotes_sql = text(
        """
        SELECT COALESCE(SUM(upvotes), 0) AS total_upvotes
        FROM (
            SELECT COUNT(*) FILTER (WHERE action = 'upvote') AS upvotes
            FROM item_signals
            WHERE item_id IN (
                SELECT id FROM intel_items WHERE cluster_id = :cid AND status = 'processed'
            )
        ) s
        """
    )
    upvotes_result = await session.execute(upvotes_sql, {"cid": cluster_id})
    total_upvotes = int(upvotes_result.scalar() or 0)

    # Build narrative from top 3 items by relevance
    top_by_relevance = sorted(
        item_rows, key=lambda r: r.get("relevance_score") or 0.0, reverse=True
    )[:3]
    thread_meta = {
        "item_count": len(item_rows),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "dominant_significance": max(
            set(r.get("significance") or "informational" for r in item_rows),
            key=lambda s: {
                "breaking": 4,
                "major": 3,
                "minor": 2,
                "informational": 1,
            }.get(s, 0),
        ),
    }
    narrative = _build_narrative(top_by_relevance, thread_meta)

    items_out = [
        IntelItemResponse(
            id=r["id"],
            source_id=r["source_id"],
            url=r["url"],
            title=r["title"],
            excerpt=r.get("excerpt"),
            summary=r.get("summary"),
            primary_type=r["primary_type"],
            tags=r.get("tags") or [],
            significance=r.get("significance"),
            relevance_score=r.get("relevance_score") or 0.0,
            quality_score=r.get("quality_score") or 0.0,
            quality_score_details=r.get("quality_score_details"),
            confidence_score=r.get("confidence_score") or 0.0,
            status=r["status"],
            created_at=r["created_at"],
            published_at=r.get("published_at"),
            source_name=r.get("source_name"),
            cluster_id=r.get("cluster_id"),
            contrarian_signals=r.get("contrarian_signals"),
        )
        for r in item_rows
    ]

    response = ThreadDetailResponse(
        thread_id=cluster_id,
        narrative_summary=narrative,
        momentum_score=round(momentum_score, 4),
        total_upvotes=total_upvotes,
        items=items_out,
    )
    return JSONResponse(content=response.model_dump(mode="json"))
