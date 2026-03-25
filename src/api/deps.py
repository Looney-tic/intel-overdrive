import uuid
from typing import AsyncGenerator, Optional
from fastapi import Depends, HTTPException, Security, status, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from src.core.init_db import get_session
from src.services.auth_service import AuthService
from src.models.models import APIKey, User
from src.core.logger import get_logger

logger = get_logger(__name__)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_auth = AuthService()


async def get_redis(request: Request) -> aioredis.Redis:
    """Dependency that returns the Redis client from app state."""
    # In tests, this will be overridden to return a test redis client
    return request.app.state.redis


async def require_api_key(
    api_key: str = Security(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> APIKey:
    """Dependency that validates the API key and increments its usage count."""
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    key_hash = _auth.hash_key(api_key)
    key_obj = await _auth.get_key_by_hash(session, key_hash)

    if key_obj is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    if not key_obj.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive API key",
        )

    # H-4: Check User.is_active -- deactivated users cannot authenticate
    user_result = await session.execute(select(User).where(User.id == key_obj.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
        )

    # Log the stored prefix field -- never slice the raw key string
    logger.info("API_KEY_AUTH_SUCCESS", prefix=key_obj.key_prefix)

    await _auth.increment_usage(session, key_hash)
    return key_obj


async def optional_api_key(
    api_key: str = Security(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> Optional[APIKey]:
    """Like require_api_key but returns None instead of 401 when no key is provided.

    Used by endpoints that want to show extra detail to authenticated callers
    while still being accessible without auth.
    """
    if not api_key:
        return None

    key_hash = _auth.hash_key(api_key)
    key_obj = await _auth.get_key_by_hash(session, key_hash)

    if key_obj is None or not key_obj.is_active:
        return None

    # Check User.is_active
    user_result = await session.execute(select(User).where(User.id == key_obj.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    return key_obj
