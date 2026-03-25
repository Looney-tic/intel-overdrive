import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from src.api.deps import optional_api_key
from src.api.schemas import HealthResponse
from src.models.models import APIKey

health_router = APIRouter(tags=["health"])


@health_router.get("/health")
async def get_health(
    request: Request,
    api_key: Optional[APIKey] = Depends(optional_api_key),
):
    """Health probe that checks real DB and Redis connectivity.

    Unauthenticated: returns {"status": "healthy"|"degraded"} only.
    Authenticated: returns full detail (db_connected, redis_connected).

    Both checks run concurrently with a 500ms timeout each to ensure <200ms typical response.
    """
    import src.core.init_db as _init_db

    async def check_db() -> bool:
        try:
            factory = _init_db.async_session_factory
            if factory is not None:
                async with factory() as session:
                    await session.execute(text("SELECT 1"))
                return True
        except Exception:
            pass
        return False

    async def check_redis() -> bool:
        try:
            redis = request.app.state.redis
            if redis is not None:
                await redis.ping()
                return True
        except Exception:
            pass
        return False

    # Run both checks concurrently with 500ms timeout each
    try:
        db_ok, redis_ok = await asyncio.wait_for(
            asyncio.gather(check_db(), check_redis(), return_exceptions=True),
            timeout=0.5,
        )
        # If an exception was returned from gather, treat as failure
        if isinstance(db_ok, Exception):
            db_ok = False
        if isinstance(redis_ok, Exception):
            redis_ok = False
    except asyncio.TimeoutError:
        db_ok = False
        redis_ok = False

    status = "healthy" if (db_ok and redis_ok) else "degraded"

    # Unauthenticated callers get minimal response (no infrastructure details)
    if api_key is None:
        return {"status": status}

    return HealthResponse(status=status, db_connected=db_ok, redis_connected=redis_ok)
