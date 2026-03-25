import pytest
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, text
from src.models.models import IntelItem, Source, User, Feedback, APIKey


# ---------------------------------------------------------------------------
# Health endpoint tests (API-07)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """API-07: /v1/health returns status=healthy (minimal, unauthenticated probe)."""
    response = await client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# Status endpoint tests (API-06)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_endpoint(client, api_key_header, source_factory):
    """API-06: Status endpoint returns summary counts (not full source list)."""
    await source_factory(id="test:status-source", name="Status Source")

    response = await client.get("/v1/status", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    # P0-2: Summary fields instead of full source list
    assert "sources" not in data, "Status should not return full source list"
    assert data["total_sources"] >= 1
    assert isinstance(data["active_sources"], int)
    assert isinstance(data["erroring_sources"], int)
    assert isinstance(data["source_type_counts"], dict)
    assert isinstance(data["daily_spend_remaining"], float)
    assert data["pipeline_health"] in ["healthy", "degraded"]


@pytest.mark.asyncio
async def test_status_requires_auth(client):
    """API-06: Status endpoint requires authentication."""
    response = await client.get("/v1/status")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Profile endpoint tests (API-08)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_create(client, api_key_header):
    """API-08: Profile creation stores tech_stack and skills."""
    payload = {"tech_stack": ["python", "typescript"], "skills": ["mcp"]}
    response = await client.post(
        "/v1/profile", json=payload, headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert "profile" in data
    assert data["profile"]["tech_stack"] == ["python", "typescript"]
    assert data["profile"]["skills"] == ["mcp"]


@pytest.mark.asyncio
async def test_profile_update(client, api_key_header, session):
    """API-08: Second profile POST overwrites the first (upsert semantics)."""
    # First POST
    await client.post(
        "/v1/profile",
        json={"tech_stack": ["python"], "skills": ["mcp"]},
        headers=api_key_header["headers"],
    )

    # Second POST with different data
    payload = {"tech_stack": ["rust", "go"], "skills": ["wasm"]}
    response = await client.post(
        "/v1/profile", json=payload, headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    assert response.json()["profile"]["tech_stack"] == ["rust", "go"]

    # Verify in DB
    query = (
        select(User)
        .where(User.id == api_key_header["user_id"])
        .execution_options(populate_existing=True)
    )
    result = await session.execute(query)
    user = result.scalar_one()
    assert user.profile["tech_stack"] == ["rust", "go"]


# ---------------------------------------------------------------------------
# Feedback endpoint tests (API-09)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feedback_miss(client, api_key_header):
    """API-09: Miss report with URL succeeds."""
    payload = {"report_type": "miss", "url": "https://example.com/missed-article"}
    response = await client.post(
        "/v1/feedback", json=payload, headers=api_key_header["headers"]
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data


@pytest.mark.asyncio
async def test_feedback_noise(client, api_key_header, session, source_factory):
    """API-09: Noise report with item_id succeeds."""
    source = await source_factory(id="test:feedback-noise-source")
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-noise",
        url="https://example.com/noise-item",
        title="Noisy Item",
        content="Content",
        primary_type="skill",
        tags=[],
        status="processed",
        relevance_score=0.8,
    )
    session.add(item)
    await session.commit()

    payload = {"report_type": "noise", "item_id": str(item.id)}
    response = await client.post(
        "/v1/feedback", json=payload, headers=api_key_header["headers"]
    )
    assert response.status_code == 201
    assert "id" in response.json()


@pytest.mark.asyncio
async def test_feedback_invalid_item_id(client, api_key_header):
    """API-09: Noise report with non-existent item_id returns 404."""
    payload = {"report_type": "noise", "item_id": str(uuid.uuid4())}
    response = await client.post(
        "/v1/feedback", json=payload, headers=api_key_header["headers"]
    )
    assert response.status_code == 404
    body = response.json()
    msg = body.get("detail", body.get("error", {}).get("message", ""))
    assert "Item not found" in msg or "not found" in msg.lower()


@pytest.mark.asyncio
async def test_feedback_requires_at_least_one(client, api_key_header):
    """API-09: Feedback with neither item_id nor url returns 422."""
    payload = {"report_type": "miss"}
    response = await client.post(
        "/v1/feedback", json=payload, headers=api_key_header["headers"]
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_feedback_requires_auth(client):
    """API-09: Feedback endpoint requires authentication."""
    payload = {"report_type": "miss", "url": "https://example.com/test"}
    response = await client.post("/v1/feedback", json=payload)
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Phase 4 schema migration assertion test (API-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_phase4_schema(session):
    """API-01: Phase 4 schema changes are present in the test DB.

    Verifies:
    1. search_vector GENERATED column on intel_items
    2. intel_items_search_idx GIN index
    3. feedback table exists
    """
    # 1. search_vector column exists
    col_result = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'intel_items' AND column_name = 'search_vector'
        """
        )
    )
    assert (
        col_result.scalar() == "search_vector"
    ), "search_vector column missing from intel_items"

    # 2. GIN index exists
    idx_result = await session.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE tablename = 'intel_items' AND indexname = 'intel_items_search_idx'
        """
        )
    )
    assert (
        idx_result.scalar() == "intel_items_search_idx"
    ), "intel_items_search_idx GIN index missing"

    # 3. feedback table exists
    tbl_result = await session.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_name = 'feedback'
        """
        )
    )
    assert tbl_result.scalar() == "feedback", "feedback table missing"


# ---------------------------------------------------------------------------
# SLA endpoint tests (INTEL-11)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sla_endpoint_returns_required_fields(
    client, api_key_header, source_factory
):
    """INTEL-11: GET /v1/sla returns all required freshness fields."""
    await source_factory(id="test:sla-source", name="SLA Source")

    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    # All required fields present (newest_item_age_hours renamed from max_item_age_hours)
    assert "newest_item_age_hours" in data
    assert "pipeline_lag_seconds" in data
    assert "items_last_24h" in data
    assert "items_last_7d" in data
    assert "failed_items_last_24h" in data
    assert "credits_exhausted" in data
    assert "coverage_score" in data
    assert "source_health_summary" in data
    assert "freshness_guarantee" in data
    assert "checked_at" in data

    # Static contract
    assert data["freshness_guarantee"] == "24h"

    # Source health summary has expected keys
    summary = data["source_health_summary"]
    assert "healthy" in summary
    assert "degraded" in summary
    assert "dead" in summary
    assert "total" in summary

    # coverage_score is 0.0-1.0
    assert 0.0 <= data["coverage_score"] <= 1.0

    # item counts are non-negative integers
    assert data["items_last_24h"] >= 0
    assert data["items_last_7d"] >= 0


@pytest.mark.asyncio
async def test_sla_requires_auth(client):
    """INTEL-11: SLA endpoint requires authentication."""
    response = await client.get("/v1/sla")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_sla_counts_recent_items(client, api_key_header, session, source_factory):
    """INTEL-11: SLA items_last_24h reflects actual processed items."""
    source = await source_factory(id="test:sla-count-source", name="SLA Count Source")

    # Add a processed item created now
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-sla-recent",
        url="https://example.com/sla-recent",
        title="SLA Recent Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.commit()

    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    # Should see at least 1 item in last 24h
    assert data["items_last_24h"] >= 1
    assert data["items_last_7d"] >= 1


# ---------------------------------------------------------------------------
# Feed cursor (new=true) and persona tests (INTEL-04, INTEL-05)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_new_param_returns_cursor_updated(
    client, api_key_header, session, source_factory
):
    """INTEL-04: feed?new=true sets cursor_updated=True in response."""
    source = await source_factory(id="test:cursor-source", name="Cursor Source")

    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cursor-item",
        url="https://example.com/cursor-item",
        title="Cursor Test Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.commit()

    response = await client.get("/v1/feed?new=true", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["cursor_updated"] is True


@pytest.mark.asyncio
async def test_feed_new_param_repeat_call_returns_zero_items(
    client, api_key_header, session, source_factory
):
    """INTEL-04: Second call with new=true after cursor set returns zero items (incremental)."""
    source = await source_factory(
        id="test:cursor-empty-source", name="Cursor Empty Source"
    )

    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cursor-empty",
        url="https://example.com/cursor-empty",
        title="Cursor Empty Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=10),
    )
    session.add(item)
    await session.commit()

    # First call: sets cursor to now
    resp1 = await client.get("/v1/feed?new=true", headers=api_key_header["headers"])
    assert resp1.status_code == 200
    assert resp1.json()["cursor_updated"] is True

    # Second call: cursor is now in the past (after the item), so no new items
    resp2 = await client.get("/v1/feed?new=true", headers=api_key_header["headers"])
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert (
        data2["cursor_updated"] is False
    )  # cursor no longer advances on empty results
    # All items were created before the cursor timestamp, so should be 0
    assert data2["total"] == 0
    assert len(data2["items"]) == 0


@pytest.mark.asyncio
async def test_feed_persona_agent_builder(
    client, api_key_header, session, source_factory
):
    """INTEL-05: persona=agent-builder applies significance filter (breaking/major only)."""
    source = await source_factory(
        id="test:persona-ab-source", name="Persona Agent Builder Source"
    )

    # Breaking item — should appear
    breaking_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-ab-breaking",
        url="https://example.com/ab-breaking",
        title="Breaking Change",
        content="Content",
        primary_type="update",
        tags=[],
        status="processed",
        significance="breaking",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    # Informational item — should NOT appear with agent-builder preset
    info_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-ab-info",
        url="https://example.com/ab-info",
        title="Informational Item",
        content="Content",
        primary_type="update",
        tags=[],
        status="processed",
        significance="informational",
        relevance_score=0.9,  # Higher score but lower significance
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    session.add(breaking_item)
    session.add(info_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?persona=agent-builder", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    items = response.json()["items"]

    returned_ids = {item["id"] for item in items}
    assert (
        str(breaking_item.id) in returned_ids
    ), "Breaking item should appear for agent-builder"
    assert (
        str(info_item.id) not in returned_ids
    ), "Informational item should be filtered out by agent-builder preset"


@pytest.mark.asyncio
async def test_feed_persona_learner_filters_by_type(
    client, api_key_header, session, source_factory
):
    """INTEL-05: persona=learner filters to docs and practice types."""
    source = await source_factory(
        id="test:persona-learn-source", name="Persona Learner Source"
    )

    docs_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-learn-docs",
        url="https://example.com/learn-docs",
        title="Documentation Item",
        content="Content",
        primary_type="docs",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    tool_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-learn-tool",
        url="https://example.com/learn-tool",
        title="Tool Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.9,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )
    session.add(docs_item)
    session.add(tool_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?persona=learner", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    items = response.json()["items"]

    returned_ids = {item["id"] for item in items}
    assert str(docs_item.id) in returned_ids, "Docs item should appear for learner"
    assert (
        str(tool_item.id) not in returned_ids
    ), "Tool item should be filtered by learner preset"
