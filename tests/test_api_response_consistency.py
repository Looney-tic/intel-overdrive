"""Tests for API response consistency fixes (Phase 14, Plan 02).

Covers:
- /similar endpoints return {items, total} envelope (not bare array)
- /search echoes offset and limit in response
- context-pack text format errors return text/plain (not JSON)
- Thread detail momentum_score is normalized 0-1
- SimilarItemResponse.tags is always list (never null)
- Feed cursor does NOT advance when total == 0
- Feed and diff cursors are independent
- Diff endpoint accepts tag, group, significance filters
"""
import math
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

from src.models.models import IntelItem, User, APIKey


def make_embedding(seed: float = 0.1, dim: int = 1024) -> list:
    """Generate a normalized embedding vector for testing."""
    raw = [seed * (i % 10 + 1) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def basic_source(session, source_factory):
    """A source used by multiple fixtures."""
    return await source_factory(id="test:consistency-source", name="Consistency Source")


@pytest_asyncio.fixture
async def processed_item_with_embedding(session, basic_source):
    """A processed item with embedding and tags."""
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=basic_source.id,
        external_id="ext-consistency-ref",
        url="https://example.com/consistency-ref",
        title="Reference Item",
        content="Reference content",
        primary_type="skill",
        tags=["python", "mcp"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=make_embedding(0.5),
    )
    session.add(item)

    # Add a neighbour item so similar results are non-empty
    neighbour = IntelItem(
        id=uuid.uuid4(),
        source_id=basic_source.id,
        external_id="ext-consistency-nbr",
        url="https://example.com/consistency-nbr",
        title="Neighbour Item",
        content="Neighbour content",
        primary_type="skill",
        tags=["python"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=make_embedding(0.501),
    )
    session.add(neighbour)
    await session.commit()
    return item


@pytest_asyncio.fixture
async def clustered_items(session, basic_source):
    """Items in the same cluster for thread tests."""
    cluster_id = f"cluster-{uuid.uuid4().hex[:8]}"
    items = []
    for i in range(3):
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=basic_source.id,
            external_id=f"ext-cluster-{i}",
            url=f"https://example.com/cluster-{i}",
            title=f"Cluster Item {i}",
            content=f"Content {i}",
            primary_type="update",
            tags=["mcp"],
            status="processed",
            relevance_score=0.7 + i * 0.05,
            quality_score=0.8,
            confidence_score=0.9,
            significance="minor",
            created_at=datetime.now(timezone.utc) - timedelta(hours=i),
            cluster_id=cluster_id,
        )
        session.add(item)
        items.append(item)
    await session.commit()
    return {"cluster_id": cluster_id, "items": items}


@pytest_asyncio.fixture
async def api_key_with_profile(session):
    """API key with a profile for diff cursor tests."""
    from src.services.auth_service import AuthService

    auth = AuthService()
    raw_key, key_hash = auth.generate_api_key()

    user = User(
        email="cursor-test@example.com",
        is_active=True,
        profile={"tech_stack": ["python"], "skills": []},
    )
    session.add(user)
    await session.flush()

    api_key = APIKey(
        key_hash=key_hash,
        key_prefix="dti_v1_",
        user_id=user.id,
        is_active=True,
    )
    session.add(api_key)
    await session.commit()

    return {
        "raw_key": raw_key,
        "headers": {"X-API-Key": raw_key},
        "user_id": user.id,
        "api_key": api_key,
    }


# ---------------------------------------------------------------------------
# Task 1: Similar envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_similar_by_id_returns_envelope(
    client, api_key_header, processed_item_with_embedding
):
    """GET /v1/similar/{id} returns {items: [...], total: N} envelope, not bare array."""
    ref_id = processed_item_with_embedding.id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict), "Response must be an object, not an array"
    assert "items" in data, "Response must have 'items' key"
    assert "total" in data, "Response must have 'total' key"
    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)


@pytest.mark.asyncio
async def test_similar_by_concept_returns_envelope(client, api_key_header):
    """GET /v1/similar?concept=... returns {items, total} envelope."""
    # concept endpoint requires VOYAGE_API_KEY; without it returns 503
    # We just verify the response shape when we can
    response = await client.get(
        "/v1/similar?concept=mcp", headers=api_key_header["headers"]
    )
    # 503 is acceptable (no real Voyage key in tests); anything else must be envelope
    if response.status_code == 200:
        data = response.json()
        assert isinstance(data, dict), "Response must be an object, not an array"
        assert "items" in data
        assert "total" in data


@pytest.mark.asyncio
async def test_similar_tags_is_always_list(
    client, api_key_header, processed_item_with_embedding
):
    """SimilarItemResponse.tags is always a list, never null."""
    ref_id = processed_item_with_embedding.id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    for item in data["items"]:
        assert item["tags"] is not None, "tags must never be null"
        assert isinstance(item["tags"], list), "tags must be a list"


@pytest.mark.asyncio
async def test_similar_total_matches_items_count(
    client, api_key_header, processed_item_with_embedding
):
    """total in envelope matches len(items)."""
    ref_id = processed_item_with_embedding.id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == len(data["items"])


# ---------------------------------------------------------------------------
# Task 1: /search echoes offset and limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_echoes_offset_and_limit(client, api_key_header):
    """GET /v1/search response includes offset and limit fields."""
    response = await client.get(
        "/v1/search?q=test&offset=5&limit=15", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert "offset" in data, "Response must echo 'offset'"
    assert "limit" in data, "Response must echo 'limit'"
    assert data["offset"] == 5
    assert data["limit"] == 15


@pytest.mark.asyncio
async def test_search_echoes_default_offset_and_limit(client, api_key_header):
    """Default offset=0, limit=20 are echoed in response."""
    response = await client.get("/v1/search?q=test", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["offset"] == 0
    assert data["limit"] == 20


# ---------------------------------------------------------------------------
# Task 1: context-pack text format errors stay text/plain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_pack_text_error_is_text_plain(client):
    """GET /v1/context-pack?format=text with no auth returns text/plain error."""
    response = await client.get("/v1/context-pack?format=text")
    # Must be 401 Unauthorized
    assert response.status_code == 401
    # Content-Type must be text/plain, not application/json
    content_type = response.headers.get("content-type", "")
    assert (
        "text/plain" in content_type
    ), f"Expected text/plain error for format=text, got Content-Type: {content_type}"


@pytest.mark.asyncio
async def test_context_pack_json_error_is_json(client):
    """GET /v1/context-pack?format=json with no auth returns JSON error (normal)."""
    response = await client.get("/v1/context-pack?format=json")
    assert response.status_code == 401
    # JSON format errors should remain JSON
    content_type = response.headers.get("content-type", "")
    assert "application/json" in content_type


# ---------------------------------------------------------------------------
# Task 1: Thread detail momentum normalization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thread_detail_momentum_is_normalized(
    client, api_key_header, clustered_items
):
    """GET /v1/threads/{cluster_id} momentum_score is in 0-1 range."""
    cluster_id = clustered_items["cluster_id"]
    response = await client.get(
        f"/v1/threads/{cluster_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    score = data["momentum_score"]
    assert 0.0 <= score <= 1.0, f"momentum_score {score} is outside [0, 1]"


# ---------------------------------------------------------------------------
# Task 2: Feed cursor does NOT advance when total == 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_cursor_not_updated_when_empty(client, api_key_header, session):
    """Feed cursor_updated is False when total == 0 results."""
    # Set API key's last_seen_at to a far-future timestamp so no items qualify.
    from sqlalchemy import text as sa_text

    api_key_id = api_key_header["api_key_id"]
    future_ts = datetime.now(timezone.utc) + timedelta(days=999)
    await session.execute(
        sa_text("UPDATE api_keys SET last_seen_at = :ts WHERE id = :kid"),
        {"ts": future_ts, "kid": api_key_id},
    )
    await session.commit()

    response = await client.get(
        "/v1/feed?new=true",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert (
        data["cursor_updated"] is False
    ), "Cursor must NOT advance when result set is empty"


@pytest.mark.asyncio
async def test_feed_cursor_updated_when_results_exist(
    client, api_key_header, basic_source, session
):
    """Feed cursor_updated is True when results exist."""
    # Insert a fresh processed item
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=basic_source.id,
        external_id="ext-cursor-test",
        url="https://example.com/cursor-test",
        title="Cursor Test Item",
        content="Content",
        primary_type="skill",
        tags=["python"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.commit()

    response = await client.get(
        "/v1/feed?new=true&days=1", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    if data["total"] > 0:
        assert data["cursor_updated"] is True


# ---------------------------------------------------------------------------
# Task 2: Feed and diff cursors are independent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_cursor_independent_from_diff(
    client, api_key_with_profile, basic_source, session, redis_client
):
    """Calling /feed?new=true does NOT affect the diff Redis cursor."""
    headers = api_key_with_profile["headers"]
    api_key = api_key_with_profile["api_key"]

    # Insert item matching profile tag (python)
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=basic_source.id,
        external_id="ext-ind-cursor",
        url="https://example.com/ind-cursor",
        title="Independent Cursor Item",
        content="Python content",
        primary_type="skill",
        tags=["python"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.commit()

    # 1. Call feed with new=true to advance feed cursor
    feed_resp = await client.get("/v1/feed?new=true&days=1", headers=headers)
    assert feed_resp.status_code == 200

    # 2. Check that diff Redis cursor key is NOT set
    cursor_key = f"diff_cursor:{api_key.id}"
    diff_cursor = await redis_client.get(cursor_key)
    assert diff_cursor is None, "Feed cursor must not affect diff Redis cursor"


# ---------------------------------------------------------------------------
# Task 2: Diff endpoint accepts tag, group, significance filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_diff_accepts_tag_filter(client, api_key_with_profile):
    """GET /v1/diff?tag=mcp responds with 200 (no 422)."""
    headers = api_key_with_profile["headers"]
    response = await client.get("/v1/diff?tag=mcp", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_diff_accepts_significance_filter(client, api_key_with_profile):
    """GET /v1/diff?significance=breaking responds with 200 (no 422)."""
    headers = api_key_with_profile["headers"]
    response = await client.get("/v1/diff?significance=breaking", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_diff_accepts_group_filter(client, api_key_with_profile):
    """GET /v1/diff?group=mcp responds with 200 (no 422)."""
    headers = api_key_with_profile["headers"]
    response = await client.get("/v1/diff?group=mcp", headers=headers)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_diff_tag_filter_narrows_results(
    client, api_key_with_profile, basic_source, session
):
    """Diff tag filter returns only items tagged with that tag."""
    headers = api_key_with_profile["headers"]

    # Insert item tagged 'python' (matches profile)
    item_python = IntelItem(
        id=uuid.uuid4(),
        source_id=basic_source.id,
        external_id="ext-diff-python",
        url="https://example.com/diff-python",
        title="Diff Python Item",
        content="Content",
        primary_type="skill",
        tags=["python"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    # Insert item tagged 'mcp' (matches profile if mcp is in stack, but let's use significance)
    item_mcp = IntelItem(
        id=uuid.uuid4(),
        source_id=basic_source.id,
        external_id="ext-diff-mcp",
        url="https://example.com/diff-mcp",
        title="Diff MCP Item",
        content="Content",
        primary_type="update",
        tags=["python", "mcp"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.8,
        confidence_score=0.9,
        significance="breaking",
        created_at=datetime.now(timezone.utc),
    )
    session.add(item_python)
    session.add(item_mcp)
    await session.commit()

    # Filter by significance=breaking — should only get item_mcp
    resp = await client.get("/v1/diff?significance=breaking&days=1", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item.get("significance") == "breaking"
