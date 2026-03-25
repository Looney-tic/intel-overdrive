"""Tests for src/api/cache.py — Redis response cache helper."""

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.api.cache import (
    make_cache_key,
    get_cached_response,
    set_cached_response,
    is_cache_enabled,
    get_redis_from_request,
)


class TestMakeCacheKey:
    """Tests for deterministic cache key generation."""

    def test_produces_rc_prefix(self):
        key = make_cache_key("search", {"q": "mcp"})
        assert key.startswith("rc:search:")

    def test_deterministic_same_params(self):
        k1 = make_cache_key("feed", {"days": 7, "limit": 20})
        k2 = make_cache_key("feed", {"days": 7, "limit": 20})
        assert k1 == k2

    def test_ignores_param_order(self):
        k1 = make_cache_key("search", {"q": "mcp", "limit": 20, "offset": 0})
        k2 = make_cache_key("search", {"offset": 0, "q": "mcp", "limit": 20})
        assert k1 == k2

    def test_different_params_different_keys(self):
        k1 = make_cache_key("search", {"q": "mcp"})
        k2 = make_cache_key("search", {"q": "agents"})
        assert k1 != k2

    def test_different_endpoints_different_keys(self):
        k1 = make_cache_key("search", {"q": "mcp"})
        k2 = make_cache_key("feed", {"q": "mcp"})
        assert k1 != k2

    def test_none_values_excluded(self):
        k1 = make_cache_key("search", {"q": "mcp", "tag": None})
        k2 = make_cache_key("search", {"q": "mcp"})
        assert k1 == k2

    def test_key_format(self):
        key = make_cache_key("search", {"q": "test"})
        parts = key.split(":")
        assert len(parts) == 3
        assert parts[0] == "rc"
        assert parts[1] == "search"
        assert len(parts[2]) == 16  # sha256 hex truncated to 16 chars


class TestGetCachedResponse:
    """Tests for cache retrieval."""

    @pytest.mark.asyncio
    async def test_returns_cached_string(self):
        redis = AsyncMock()
        redis.get.return_value = '{"items": [], "total": 0}'
        result = await get_cached_response(redis, "rc:search:abc123")
        assert result == '{"items": [], "total": 0}'
        redis.get.assert_called_once_with("rc:search:abc123")

    @pytest.mark.asyncio
    async def test_returns_none_on_miss(self):
        redis = AsyncMock()
        redis.get.return_value = None
        result = await get_cached_response(redis, "rc:search:abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        redis = AsyncMock()
        redis.get.side_effect = ConnectionError("Redis down")
        result = await get_cached_response(redis, "rc:search:abc123")
        assert result is None

    @pytest.mark.asyncio
    async def test_decodes_bytes(self):
        redis = AsyncMock()
        redis.get.return_value = b'{"items": []}'
        result = await get_cached_response(redis, "rc:search:abc123")
        assert result == '{"items": []}'


class TestSetCachedResponse:
    """Tests for cache storage."""

    @pytest.mark.asyncio
    async def test_sets_with_ttl(self):
        redis = AsyncMock()
        await set_cached_response(redis, "rc:search:abc", '{"items": []}', ttl=300)
        redis.set.assert_called_once_with("rc:search:abc", '{"items": []}', ex=300)

    @pytest.mark.asyncio
    async def test_swallows_errors(self):
        redis = AsyncMock()
        redis.set.side_effect = ConnectionError("Redis down")
        # Should not raise
        await set_cached_response(redis, "rc:search:abc", '{"items": []}', ttl=300)

    @pytest.mark.asyncio
    async def test_uses_default_ttl(self):
        redis = AsyncMock()
        with patch("src.api.cache.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(CACHE_TTL_SECONDS=600)
            await set_cached_response(redis, "rc:search:abc", '{"items": []}')
            redis.set.assert_called_once_with("rc:search:abc", '{"items": []}', ex=600)


class TestIsCacheEnabled:
    """Tests for cache enabled check."""

    def test_enabled_by_default(self):
        with patch("src.api.cache.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(CACHE_ENABLED=True)
            assert is_cache_enabled() is True

    def test_can_be_disabled(self):
        with patch("src.api.cache.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(CACHE_ENABLED=False)
            assert is_cache_enabled() is False


class TestGetRedisFromRequest:
    """Tests for Redis extraction from request."""

    def test_returns_redis_from_app_state(self):
        request = MagicMock()
        request.app.state.redis = "mock_redis"
        assert get_redis_from_request(request) == "mock_redis"

    def test_returns_none_when_no_redis(self):
        request = MagicMock()
        del request.app.state.redis
        request.app.state.redis = None
        assert get_redis_from_request(request) is None

    def test_returns_none_on_error(self):
        request = MagicMock()
        type(request).app = property(lambda self: (_ for _ in ()).throw(RuntimeError))
        assert get_redis_from_request(request) is None


class TestContextPackCacheKeyBudget:
    """Verify that different budget values produce different cache keys."""

    def test_context_pack_cache_key_varies_with_budget(self):
        k1 = make_cache_key(
            "context-pack",
            {
                "topic": None,
                "budget": 2000,
                "days": 14,
                "sort": "significance",
                "format": "text",
                "include_library": False,
                "library_budget": 0,
            },
        )
        k2 = make_cache_key(
            "context-pack",
            {
                "topic": None,
                "budget": 8000,
                "days": 14,
                "sort": "significance",
                "format": "text",
                "include_library": False,
                "library_budget": 0,
            },
        )
        assert k1 != k2, "Different budget values must produce different cache keys"
