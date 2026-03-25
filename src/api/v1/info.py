import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import IntelItem, APIKey
from src.api.deps import get_session, require_api_key
from src.api.schemas import IntelItemResponse
from src.api.limiter import limiter

info_router = APIRouter(tags=["info"])

@info_router.get("/info/{item_id}", response_model=IntelItemResponse)
@limiter.limit("100/minute")
async def get_item_info(
    request: Request,
    item_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns full details for a specific intelligence item.
    Only returns items with 'processed' status.
    """
    query = select(IntelItem).where(
        IntelItem.id == item_id,
        IntelItem.status == "processed"
    )
    result = await session.execute(query)
    item = result.scalar_one_or_none()
    
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
        
    response_obj = IntelItemResponse.model_validate(item)
    return JSONResponse(content=response_obj.model_dump(mode="json"))
