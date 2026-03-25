"""
Tests for admin key management endpoints.

Endpoints:
  POST /v1/admin/keys    — create a new API key for the authenticated user
  GET  /v1/admin/keys    — list all keys for the authenticated user
  DELETE /v1/admin/keys/{id} — revoke (soft-delete) a key

All endpoints require X-API-Key authentication.
"""
import pytest
import pytest_asyncio


@pytest.mark.asyncio
async def test_create_key_returns_raw_key(client, api_key_header):
    """POST /v1/admin/keys → 201 response includes raw key starting with dti_v1_."""
    resp = await client.post(
        "/v1/admin/keys",
        headers=api_key_header["headers"],
        json={"name": "my-agent"},
    )
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "key" in body, f"Expected 'key' in response, got: {body}"
    assert body["key"].startswith(
        "dti_v1_"
    ), f"Key should start with dti_v1_, got: {body['key']}"
    assert "key_prefix" in body
    assert "id" in body
    assert body["name"] == "my-agent"
    assert "message" in body
    # The message should mention secure storage
    assert "securely" in body["message"].lower() or "store" in body["message"].lower()


@pytest.mark.asyncio
async def test_create_key_without_name(client, api_key_header):
    """POST /v1/admin/keys with no name → 201, name is null."""
    resp = await client.post(
        "/v1/admin/keys",
        headers=api_key_header["headers"],
        json={},
    )
    assert resp.status_code == 201, f"Got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["name"] is None


@pytest.mark.asyncio
async def test_list_keys_no_raw_key_exposed(client, api_key_header):
    """GET /v1/admin/keys → 200, list of keys with NO raw key values."""
    resp = await client.get("/v1/admin/keys", headers=api_key_header["headers"])
    assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text}"
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) >= 1  # at least the key used for auth exists

    for item in items:
        assert "key" not in item, "Raw key must NOT appear in list response"
        assert "id" in item
        assert "key_prefix" in item
        assert "is_active" in item
        assert "usage_count" in item
        assert "created_at" in item


@pytest.mark.asyncio
async def test_revoke_key_returns_204(client, session, api_key_header):
    """DELETE /v1/admin/keys/{id} → 204, key becomes inactive."""
    from src.services.auth_service import AuthService
    from src.models.models import APIKey

    auth = AuthService()
    raw_key2, key_hash2 = auth.generate_api_key()
    api_key2 = APIKey(
        key_hash=key_hash2,
        key_prefix="dti_v1_",
        user_id=api_key_header["user_id"],
        is_active=True,
        name="to-revoke",
    )
    session.add(api_key2)
    await session.commit()

    key2_id = api_key2.id

    resp = await client.delete(
        f"/v1/admin/keys/{key2_id}",
        headers=api_key_header["headers"],
    )
    assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

    # Verify the key is now inactive in the DB
    from sqlalchemy import select

    result = await session.execute(
        select(APIKey)
        .where(APIKey.id == key2_id)
        .execution_options(populate_existing=True)
    )
    revoked_key = result.scalar_one_or_none()
    assert revoked_key is not None
    assert revoked_key.is_active is False, "Key should be inactive after revocation"


@pytest.mark.asyncio
async def test_cannot_revoke_own_key(client, api_key_header):
    """DELETE /v1/admin/keys/{own_id} → 400 error (cannot revoke the key you're using)."""
    own_key_id = api_key_header["api_key_id"]
    resp = await client.delete(
        f"/v1/admin/keys/{own_key_id}",
        headers=api_key_header["headers"],
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    # Should have error envelope or at minimum a detail
    assert "error" in body or "detail" in body


@pytest.mark.asyncio
async def test_revoked_key_returns_inactive_error(client, session, api_key_header):
    """After revocation, using the revoked key → 401 INACTIVE_API_KEY."""
    from src.services.auth_service import AuthService
    from src.models.models import APIKey

    auth = AuthService()
    raw_key2, key_hash2 = auth.generate_api_key()
    api_key2 = APIKey(
        key_hash=key_hash2,
        key_prefix="dti_v1_",
        user_id=api_key_header["user_id"],
        is_active=True,
        name="revoke-test",
    )
    session.add(api_key2)
    await session.commit()

    key2_id = api_key2.id

    # Revoke the key
    del_resp = await client.delete(
        f"/v1/admin/keys/{key2_id}",
        headers=api_key_header["headers"],
    )
    assert del_resp.status_code == 204

    # Try to use revoked key
    resp = await client.get("/v1/feed", headers={"X-API-Key": raw_key2})
    assert resp.status_code == 401
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "INACTIVE_API_KEY"


@pytest.mark.asyncio
async def test_admin_endpoints_require_auth(client):
    """All admin endpoints → 401 without X-API-Key."""
    # POST without auth
    resp = await client.post("/v1/admin/keys", json={})
    assert resp.status_code == 401
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "MISSING_API_KEY"

    # GET without auth
    resp = await client.get("/v1/admin/keys")
    assert resp.status_code == 401

    # DELETE without auth
    import uuid

    resp = await client.delete(f"/v1/admin/keys/{uuid.uuid4()}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_cannot_revoke_other_users_key(client, session, api_key_header):
    """DELETE /v1/admin/keys/{id} for another user's key → 404."""
    from src.services.auth_service import AuthService
    from src.models.models import APIKey, User

    auth = AuthService()

    # Create a second user with their own key
    user2 = User(email="user2@example.com", is_active=True, profile={})
    session.add(user2)
    await session.flush()

    raw_key2, key_hash2 = auth.generate_api_key()
    api_key2 = APIKey(
        key_hash=key_hash2,
        key_prefix="dti_v1_",
        user_id=user2.id,
        is_active=True,
        name="user2-key",
    )
    session.add(api_key2)
    await session.commit()

    # Try to revoke user2's key using user1's auth
    resp = await client.delete(
        f"/v1/admin/keys/{api_key2.id}",
        headers=api_key_header["headers"],
    )
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"
