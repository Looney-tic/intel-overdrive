"""
Admin API key management endpoints.

Endpoints:
  POST   /v1/admin/keys         — create a new API key for the authenticated user
  GET    /v1/admin/keys         — list all keys for the authenticated user
  DELETE /v1/admin/keys/{id}    — revoke (soft-delete) a specific key

All endpoints require a valid X-API-Key header.

NOTE: All handlers return JSONResponse (not Pydantic model instances) because
slowapi's @limiter.limit with headers_enabled=True requires a starlette Response
object — the same pattern used throughout the codebase (see alerts.py).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func as sqla_func

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.schemas import AdminKeyCreate, AdminKeyCreatedResponse, AdminKeyResponse
from src.models.models import APIKey
from src.services.auth_service import AuthService
from src.core.logger import get_logger

logger = get_logger(__name__)
_auth = AuthService()

admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.post(
    "/keys",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new API key",
    description=(
        "Creates a new API key for the authenticated user. "
        "The raw key is returned ONLY in this response — store it securely."
    ),
)
@limiter.limit("10/minute")
async def create_api_key(
    request: Request,
    body: AdminKeyCreate = AdminKeyCreate(),
    api_key: APIKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Create a new API key for the authenticated user."""
    # C-3: Per-user key cap -- max 5 active keys per user
    MAX_KEYS_PER_USER = 5
    active_count_result = await session.execute(
        select(sqla_func.count()).where(
            APIKey.user_id == api_key.user_id,
            APIKey.is_active == True,
        )
    )
    active_count = active_count_result.scalar()
    if active_count >= MAX_KEYS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Maximum {MAX_KEYS_PER_USER} active API keys per user. Revoke an existing key first.",
        )

    raw_key, key_hash = _auth.generate_api_key()
    key_prefix = raw_key[:14]  # "dti_v1_" + 7 unique chars
    name = body.name

    new_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        user_id=api_key.user_id,
        name=name,
        is_active=True,
    )
    session.add(new_key)
    await session.commit()

    logger.info("ADMIN_KEY_CREATED", user_id=str(api_key.user_id), key_id=new_key.id)

    response_obj = AdminKeyCreatedResponse(
        key=raw_key,
        key_prefix=key_prefix,
        id=new_key.id,
        name=name,
        message="Store this key securely — it cannot be retrieved again.",
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=response_obj.model_dump(mode="json"),
    )


@admin_router.get(
    "/keys",
    summary="List API keys",
    description="Lists all API keys for the authenticated user. Raw keys are never returned.",
)
@limiter.limit("30/minute")
async def list_api_keys(
    request: Request,
    api_key: APIKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """List all API keys for the authenticated user (no raw key values)."""
    result = await session.execute(
        select(APIKey).where(APIKey.user_id == api_key.user_id).order_by(APIKey.id)
    )
    keys = result.scalars().all()
    items = [AdminKeyResponse.model_validate(k).model_dump(mode="json") for k in keys]
    return JSONResponse(content=items)


@admin_router.delete(
    "/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
    description=(
        "Soft-deletes (revokes) an API key by setting is_active=False. "
        "Cannot revoke the key currently used for this request."
    ),
)
@limiter.limit("10/minute")
async def revoke_api_key(
    request: Request,
    key_id: int,
    api_key: APIKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke (soft-delete) an API key."""
    # Prevent revoking the key used for this request
    if key_id == api_key.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot revoke the key you are currently using.",
        )

    # Find the key — must belong to the authenticated user
    result = await session.execute(
        select(APIKey).where(
            APIKey.id == key_id,
            APIKey.user_id == api_key.user_id,
        )
    )
    target_key = result.scalar_one_or_none()

    if target_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key {key_id} not found.",
        )

    # Soft delete — preserve audit trail
    target_key.is_active = False
    await session.commit()

    logger.info(
        "ADMIN_KEY_REVOKED",
        user_id=str(api_key.user_id),
        revoked_key_id=key_id,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
