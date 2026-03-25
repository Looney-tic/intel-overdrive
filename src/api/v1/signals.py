"""Community signal endpoints: upvote/bookmark/dismiss items, contrarian detection."""

import json
import uuid as _uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import IntelItem, APIKey
from src.api.deps import get_session, require_api_key
from src.api.schemas import SignalRequest, ItemSignalsResponse
from src.api.limiter import limiter
from src.api.query_logger import log_query
from src.core.logger import get_logger

signals_router = APIRouter(tags=["signals"])
logger = get_logger(__name__)


async def _recompute_contrarian(item_id: str, session: AsyncSession) -> None:
    """Recompute contrarian_signals for a single item using pure ratio math.

    Triggered after each signal write. Fast: single-item aggregation only.
    Thresholds:
      - adoption_risk: dismissal_rate > 0.6
      - hype_gap: upvotes > 10 AND dismissals >= upvotes
      - regression: significance in (breaking, major) AND dismissal_rate > 0.4
      - security_concern: 'security' in tags AND dismissal_rate > 0.3
    Requires total_signals >= 10 to avoid noise from tiny sample sizes.
    """
    agg = await session.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (WHERE action='upvote')   AS upvotes,
                COUNT(*) FILTER (WHERE action='bookmark') AS bookmarks,
                COUNT(*) FILTER (WHERE action='dismiss')  AS dismissals,
                COUNT(*) AS total
            FROM item_signals WHERE item_id = CAST(:iid AS uuid)
            """
        ),
        {"iid": item_id},
    )
    row = agg.mappings().fetchone()
    if row is None or row["total"] < 10:
        return  # not enough signal yet

    upvotes = row["upvotes"]
    dismissals = row["dismissals"]
    total = row["total"]
    dismissal_rate = dismissals / total if total > 0 else 0.0

    # Fetch tags + significance for category detection
    item_row = await session.execute(
        text(
            "SELECT tags, significance FROM intel_items WHERE id = CAST(:iid AS uuid)"
        ),
        {"iid": item_id},
    )
    item = item_row.mappings().fetchone()
    if not item:
        return

    raw_tags = item["tags"] or []
    if isinstance(raw_tags, str):
        raw_tags = json.loads(raw_tags)
    tags = set(raw_tags)
    sig = item["significance"] or ""
    categories = []
    if dismissal_rate > 0.6:
        categories.append("adoption_risk")
    if upvotes > 10 and dismissals >= upvotes:
        categories.append("hype_gap")
    if sig in ("breaking", "major") and dismissal_rate > 0.4:
        categories.append("regression")
    if "security" in tags and dismissal_rate > 0.3:
        categories.append("security_concern")

    contrarian = categories if categories else None
    await session.execute(
        text(
            "UPDATE intel_items SET contrarian_signals = CAST(:cs AS json)"
            " WHERE id = CAST(:iid AS uuid)"
        ),
        {"cs": json.dumps(contrarian), "iid": item_id},
    )
    await session.commit()


@signals_router.post("/items/{item_id}/signal")
@limiter.limit("60/minute")
async def post_signal(
    request: Request,
    item_id: _uuid.UUID,
    body: SignalRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Record a community signal for an item (upvote, bookmark, or dismiss).

    One signal row per (item_id, api_key_id). Changing action replaces the existing row.
    After upsert, contrarian_signals is recomputed inline (fast, single-item aggregation).
    """
    # Verify item exists and is processed
    item_check = await session.execute(
        text(
            "SELECT id FROM intel_items WHERE id = CAST(:iid AS uuid)"
            " AND status = 'processed'"
        ),
        {"iid": str(item_id)},
    )
    if item_check.fetchone() is None:
        raise HTTPException(status_code=404, detail="Item not found")

    # Upsert signal: one row per (item_id, api_key_id)
    await session.execute(
        text(
            """
            INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at)
            VALUES (
                gen_random_uuid(),
                CAST(:item_id AS uuid),
                :api_key_id,
                :action,
                NOW(),
                NOW()
            )
            ON CONFLICT (item_id, api_key_id) DO UPDATE
                SET action = EXCLUDED.action,
                    updated_at = NOW()
            """
        ),
        {
            "item_id": str(item_id),
            "api_key_id": api_key.id,
            "action": body.action,
        },
    )
    await session.commit()

    logger.info(
        "SIGNAL_RECORDED",
        item_id=str(item_id),
        api_key_id=api_key.id,
        action=body.action,
    )

    # Recompute contrarian signals inline (fast: single-item aggregation)
    await _recompute_contrarian(str(item_id), session)

    # Fetch updated counts for response
    counts = await session.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (WHERE action='upvote')   AS upvotes,
                COUNT(*) FILTER (WHERE action='bookmark') AS bookmarks,
                COUNT(*) FILTER (WHERE action='dismiss')  AS dismissals,
                COUNT(*) AS total
            FROM item_signals WHERE item_id = CAST(:iid AS uuid)
            """
        ),
        {"iid": str(item_id)},
    )
    row = counts.mappings().fetchone()
    signal_counts = {
        "upvotes": row["upvotes"] if row else 0,
        "bookmarks": row["bookmarks"] if row else 0,
        "dismissals": row["dismissals"] if row else 0,
        "total": row["total"] if row else 0,
    }

    # Query logging — fire-and-forget, never fails the request
    try:
        await log_query(session, api_key.id, "signal", None, 1)
    except Exception:
        pass

    return JSONResponse(
        content={
            "item_id": str(item_id),
            "action": body.action,
            "signal_counts": signal_counts,
        }
    )


@signals_router.get("/items/{item_id}/signals", response_model=ItemSignalsResponse)
@limiter.limit("100/minute")
async def get_signals(
    request: Request,
    item_id: _uuid.UUID,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Return community signal counts for an item.

    Returns upvotes, bookmarks, dismissals, total, and contrarian_signals
    (if enough signal has accumulated and thresholds are met).
    """
    # Verify item exists
    item_check = await session.execute(
        text(
            "SELECT contrarian_signals FROM intel_items"
            " WHERE id = CAST(:iid AS uuid) AND status = 'processed'"
        ),
        {"iid": str(item_id)},
    )
    item_row = item_check.mappings().fetchone()
    if item_row is None:
        raise HTTPException(status_code=404, detail="Item not found")

    contrarian_signals = item_row["contrarian_signals"]

    # Aggregate signal counts
    counts = await session.execute(
        text(
            """
            SELECT
                COUNT(*) FILTER (WHERE action='upvote')   AS upvotes,
                COUNT(*) FILTER (WHERE action='bookmark') AS bookmarks,
                COUNT(*) FILTER (WHERE action='dismiss')  AS dismissals,
                COUNT(*) AS total
            FROM item_signals WHERE item_id = CAST(:iid AS uuid)
            """
        ),
        {"iid": str(item_id)},
    )
    row = counts.mappings().fetchone()

    response = ItemSignalsResponse(
        item_id=item_id,
        upvotes=row["upvotes"] if row else 0,
        bookmarks=row["bookmarks"] if row else 0,
        dismissals=row["dismissals"] if row else 0,
        total=row["total"] if row else 0,
        contrarian_signals=contrarian_signals,
    )
    return JSONResponse(content=response.model_dump(mode="json"))
