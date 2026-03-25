import pytest
from sqlalchemy import select
from src.models.models import APIKey
from src.api.limiter import limiter


@pytest.mark.asyncio
async def test_health_no_auth(client):
    """API-01: Health check is unauthenticated."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_auth_missing_key(client):
    """API-02: Missing API key returns 401 with structured error envelope."""
    response = await client.get("/v1/feed")
    assert response.status_code == 401
    body = response.json()
    # Structured error format: {"error": {"code": "MISSING_API_KEY", ...}}
    assert "error" in body, f"Expected 'error' key, got: {body}"
    assert body["error"]["code"] == "MISSING_API_KEY"
    assert "Missing API key" in body["error"]["message"]


@pytest.mark.asyncio
async def test_auth_invalid_key(client):
    """API-02: Invalid API key returns 401 with structured error envelope."""
    response = await client.get("/v1/feed", headers={"X-API-Key": "invalid_key"})
    assert response.status_code == 401
    body = response.json()
    assert "error" in body, f"Expected 'error' key, got: {body}"
    assert body["error"]["code"] == "INVALID_API_KEY"


@pytest.mark.asyncio
async def test_auth_valid_key(client, api_key_header):
    """API-02: Valid API key returns 200."""
    response = await client.get("/v1/feed", headers=api_key_header["headers"])
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_usage_counter_increments(client, api_key_header, session):
    """API-02: Usage counter increments on each request."""
    # Make 2 requests
    await client.get("/v1/feed", headers=api_key_header["headers"])
    await client.get("/v1/feed", headers=api_key_header["headers"])

    # Reload from DB using populate_existing to avoid stale identity map
    query = (
        select(APIKey)
        .where(APIKey.id == api_key_header["api_key_id"])
        .execution_options(populate_existing=True)
    )
    result = await session.execute(query)
    api_key = result.scalar_one()

    assert api_key.usage_count == 2
    assert api_key.last_used_at is not None


@pytest.mark.asyncio
async def test_rate_limit_headers(client, api_key_header):
    """API-11: Rate limit headers are present on rate-limited endpoints."""
    response = await client.get("/v1/feed", headers=api_key_header["headers"])
    assert response.status_code == 200
    assert "X-RateLimit-Limit" in response.headers
    assert "X-RateLimit-Remaining" in response.headers


@pytest.mark.asyncio
async def test_health_returns_last_ingestion(client):
    """API-health: /health response always includes a 'last_ingestion' key.

    The value may be None when no processed items exist, but the key must
    be present so callers can detect pipeline stalls without a KeyError.
    """
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert (
        "last_ingestion" in body
    ), f"'last_ingestion' key missing from /health response: {body}"


@pytest.mark.asyncio
async def test_rate_limit_429(client, api_key_header, redis_client):
    """API-11: Exceeding rate limit returns 429.

    Strategy: pre-seed the Redis counter for the feed endpoint to the limit (100)
    then make one more request to trigger 429.
    slowapi key format: LIMITER/{path}/{api_key}/{amount}/{granularity_name}
    """
    raw_key = api_key_header["raw_key"]

    # Make one request to let slowapi register the key in Redis
    resp = await client.get("/v1/feed", headers=api_key_header["headers"])
    assert resp.status_code == 200, f"First request failed: {resp.status_code}"

    # Find the actual Redis key pattern slowapi created
    keys = await redis_client.keys("*")
    feed_key = next((k.decode() for k in keys if b"/v1/feed" in k), None)
    assert feed_key is not None, f"No feed rate limit key found in Redis. Keys: {keys}"

    # Set the counter to the limit (100) to trigger 429 on next request
    await redis_client.set(feed_key, 100)

    # Next request should be rate-limited
    resp2 = await client.get("/v1/feed", headers=api_key_header["headers"])
    assert (
        resp2.status_code == 429
    ), f"Expected 429, got: {resp2.status_code} body={resp2.text}"
