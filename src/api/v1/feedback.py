import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import Feedback, IntelItem, APIKey
from src.api.deps import get_session, require_api_key
from src.api.schemas import FeedbackRequest, FeedbackResponse, AutoFeedbackRequest
from src.api.limiter import limiter
from src.core.logger import get_logger

logger = get_logger(__name__)

feedback_router = APIRouter(tags=["feedback"])


@feedback_router.post("/feedback", response_model=FeedbackResponse, status_code=201)
@limiter.limit("30/minute")
async def post_feedback(
    request: Request,
    body: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Submit feedback (miss or noise report) for an item.
    Validates item existence if item_id is provided.
    """
    if body.item_id:
        # Validate that the item exists
        item_query = select(IntelItem.id).where(IntelItem.id == body.item_id)
        result = await session.execute(item_query)
        if not result.scalar():
            raise HTTPException(status_code=404, detail="Item not found")

    # Create new feedback record
    feedback = Feedback(
        report_type=body.report_type,
        item_id=body.item_id,
        url=body.url,
        api_key_id=api_key.id,
        notes=body.notes,
    )

    session.add(feedback)
    await session.commit()
    await session.refresh(feedback)

    response_obj = FeedbackResponse(
        message="Feedback recorded successfully", id=feedback.id
    )

    return JSONResponse(status_code=201, content=response_obj.model_dump(mode="json"))


@feedback_router.post(
    "/feedback/auto", response_model=FeedbackResponse, status_code=201
)
@limiter.limit("60/minute")
async def auto_feedback(
    request: Request,
    body: AutoFeedbackRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Auto-feedback endpoint for MCP fire-and-forget signals.

    Accepts auto_miss (search returned no useful results) and
    query_refinement (user refined their query) report types.
    Stores query metadata in the notes field as JSON.
    """
    notes_data = {
        "query": body.query,
        "original_query": body.original_query,
        "result_count": body.result_count,
    }

    feedback = Feedback(
        report_type=body.report_type,
        item_id=None,
        url=None,
        api_key_id=api_key.id,
        notes=json.dumps(notes_data),
    )

    session.add(feedback)
    await session.commit()
    await session.refresh(feedback)

    logger.info(
        "AUTO_FEEDBACK_RECORDED",
        report_type=body.report_type,
        query=body.query,
        result_count=body.result_count,
        feedback_id=str(feedback.id),
    )

    response_obj = FeedbackResponse(
        message="Auto feedback recorded successfully", id=feedback.id
    )

    return JSONResponse(status_code=201, content=response_obj.model_dump(mode="json"))
