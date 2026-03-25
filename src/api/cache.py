"""Redis response cache for high-traffic API endpoints.

Cache key = hash of (endpoint_name + sorted query params).
api_key_id is NOT included — results are the same for all users.
Key prefix: rc: (response cache) for easy KEYS rc:* inspection.
"""

import hashlib
import json
from typing import Any

from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)


def make_cache_key(endpoint_name: str, params: dict[str, Any]) -> str:
    """Build a deterministic cache key from endpoint name and sorted params.

    Format: rc:{endpoint}:{sha256_hex[:16]}
    Params are sorted by key for determinism. None values are excluded.
    """
    # Filter out None values and sort for determinism
    filtered = {k: v for k, v in sorted(params.items()) if v is not None}
    raw = f"{endpoint_name}:{json.dumps(filtered, sort_keys=True, default=str)}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"rc:{endpoint_name}:{digest}"


async def get_cached_response(redis: Any, cache_key: str) -> str | None:
    """Get cached JSON response string from Redis. Returns None on miss or error."""
    try:
        cached = await redis.get(cache_key)
        if cached:
            logger.debug("cache_hit", key=cache_key)
            return cached if isinstance(cached, str) else cached.decode()
        return None
    except Exception:
        logger.debug("cache_get_error", key=cache_key, exc_info=True)
        return None


async def set_cached_response(
    redis: Any, cache_key: str, response_json: str, ttl: int | None = None
) -> None:
    """Store JSON response string in Redis with TTL. Swallows all exceptions."""
    if ttl is None:
        ttl = get_settings().CACHE_TTL_SECONDS
    try:
        await redis.set(cache_key, response_json, ex=ttl)
        logger.debug("cache_set", key=cache_key, ttl=ttl)
    except Exception:
        logger.debug("cache_set_error", key=cache_key, exc_info=True)


def is_cache_enabled() -> bool:
    """Check if response caching is enabled via settings."""
    return get_settings().CACHE_ENABLED


def get_redis_from_request(request: Any) -> Any | None:
    """Extract Redis client from request.app.state, or return None."""
    try:
        return getattr(request.app.state, "redis", None)
    except Exception:
        return None
