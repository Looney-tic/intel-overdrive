"""Integration tests for Phase 10 Intelligence Layer endpoints.

Tests cover all 12 INTEL requirements:
- INTEL-01: context-pack
- INTEL-02: trends
- INTEL-03: diff
- INTEL-04: feed new=true cursor
- INTEL-05: feed persona=
- INTEL-06: signals POST/GET
- INTEL-07: watchlists
- INTEL-08: embed formats
- INTEL-09: landscape
- INTEL-10: threads
- INTEL-11: sla
- INTEL-12: contrarian_signals field on IntelItemResponse

All tests use the shared conftest.py async fixtures.
Docker services (Postgres 5434, Redis 6381) are required.
"""

import uuid
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from src.models.models import IntelItem, Source


# ---------------------------------------------------------------------------
# Shared fixture: one processed item for tests that need an item_id
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def one_item(session, source_factory):
    """Create a single processed IntelItem for signal/embed tests."""
    source = await source_factory(id="test:intel-source", name="Intel Test Source")

    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id=f"ext-intel-{uuid.uuid4()}",
        url=f"https://example.com/intel-{uuid.uuid4()}",
        title="MCP Protocol Overview",
        content="Detailed content about MCP",
        primary_type="tool",
        tags=["mcp", "protocol"],
        status="processed",
        relevance_score=0.85,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        significance="major",
        source_name="Intel Test Source",
    )
    session.add(item)
    await session.commit()
    return item


@pytest_asyncio.fixture
async def thread_items(session, source_factory):
    """Create 3 processed IntelItems sharing a cluster_id for thread tests."""
    source = await source_factory(id="test:thread-source", name="Thread Test Source")
    cluster = str(uuid.uuid4())
    items = []
    for i in range(3):
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"ext-thread-{uuid.uuid4()}",
            url=f"https://example.com/thread-{uuid.uuid4()}",
            title=f"Thread Item {i+1}: Claude Code Update",
            content=f"Content about Claude Code update {i+1}",
            primary_type="update",
            tags=["claude-code", "update"],
            status="processed",
            relevance_score=0.7 + i * 0.05,
            quality_score=0.8,
            confidence_score=0.9,
            created_at=datetime.now(timezone.utc) - timedelta(hours=i),
            significance="major",
            source_name="Thread Test Source",
            cluster_id=cluster,
        )
        session.add(item)
        items.append(item)
    await session.commit()
    return cluster, items


# ---------------------------------------------------------------------------
# INTEL-01: context-pack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_01_context_pack_returns_ok(client, api_key_header, one_item):
    """INTEL-01: GET /v1/context-pack returns 200 with text/plain or JSON."""
    # Default format: text/plain
    response = await client.get(
        "/v1/context-pack?topic=mcp&budget=500",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_intel_01_context_pack_json_format(client, api_key_header, one_item):
    """INTEL-01: context-pack with format=json returns structured metadata."""
    response = await client.get(
        "/v1/context-pack?topic=mcp&budget=500&format=json",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "meta" in data
    assert "items" in data
    meta = data["meta"]
    assert "topic" in meta
    assert meta["topic"] == "mcp"
    assert "budget_tokens" in meta
    assert "items_included" in meta


# ---------------------------------------------------------------------------
# INTEL-02: trends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_02_trends_returns_ok(client, api_key_header, one_item):
    """INTEL-02: GET /v1/trends returns 200 with trends list."""
    response = await client.get("/v1/trends", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "trends" in data
    assert "window_days" in data
    assert "total" in data
    assert isinstance(data["trends"], list)


@pytest.mark.asyncio
async def test_intel_02_trends_velocity_label_values(client, api_key_header, one_item):
    """INTEL-02: trends velocity_label values are within known set."""
    response = await client.get("/v1/trends", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    known_labels = {"accelerating", "plateauing", "declining", "emerging"}
    for trend in data["trends"]:
        assert (
            trend["velocity_label"] in known_labels
        ), f"Unexpected velocity_label: {trend['velocity_label']}"


# ---------------------------------------------------------------------------
# INTEL-03: diff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_03_diff_no_profile_returns_message(client, api_key_header):
    """INTEL-03: GET /v1/diff with no profile returns helpful message."""
    response = await client.get("/v1/diff", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "message" in data
    assert "profile_stack_size" in data
    assert data["profile_stack_size"] == 0


@pytest.mark.asyncio
async def test_intel_03_diff_with_profile_returns_items(
    client, api_key_header, one_item
):
    """INTEL-03: GET /v1/diff with matching profile returns items with impact_description."""
    # Set profile matching our test item tags
    profile_resp = await client.post(
        "/v1/profile",
        json={"tech_stack": ["mcp"], "skills": []},
        headers=api_key_header["headers"],
    )
    assert profile_resp.status_code == 200

    response = await client.get(
        "/v1/diff?days=30",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "profile_stack_size" in data
    assert "message" in data  # field exists (may be None)
    # If items returned, check impact_description field
    for item in data["items"]:
        assert "impact_description" in item


# ---------------------------------------------------------------------------
# INTEL-04: feed new=true cursor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_04_feed_new_cursor_updates(client, api_key_header, one_item):
    """INTEL-04: GET /v1/feed?new=true updates cursor only when results exist."""
    # First call — should find the item and advance cursor
    resp1 = await client.get(
        "/v1/feed?new=true&days=30",
        headers=api_key_header["headers"],
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert "cursor_updated" in data1
    assert data1["cursor_updated"] is True

    # Second call — cursor is now past the item's created_at, returns 0 items
    # cursor_updated must be False (no items = no advance)
    resp2 = await client.get(
        "/v1/feed?new=true&days=30",
        headers=api_key_header["headers"],
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert "cursor_updated" in data2
    # Cursor does NOT advance when there are no new items (correct behavior)
    assert data2["cursor_updated"] is False


@pytest.mark.asyncio
async def test_intel_04_feed_new_second_call_subset(client, api_key_header, one_item):
    """INTEL-04: Second new=true call returns ≤ first call count (cursor advances)."""
    resp1 = await client.get(
        "/v1/feed?new=true&days=30", headers=api_key_header["headers"]
    )
    assert resp1.status_code == 200
    count1 = len(resp1.json()["items"])

    resp2 = await client.get(
        "/v1/feed?new=true&days=30", headers=api_key_header["headers"]
    )
    assert resp2.status_code == 200
    count2 = len(resp2.json()["items"])

    # After cursor advancement, count2 should be ≤ count1
    assert (
        count2 <= count1
    ), f"Second new=true call should return ≤ first ({count1}), got {count2}"


# ---------------------------------------------------------------------------
# INTEL-05: feed persona=
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_05_persona_agent_builder(client, api_key_header, one_item):
    """INTEL-05: GET /v1/feed?persona=agent-builder returns valid FeedResponse."""
    response = await client.get(
        "/v1/feed?persona=agent-builder",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert "offset" in data
    assert "limit" in data


@pytest.mark.asyncio
async def test_intel_05_persona_curator(client, api_key_header, one_item):
    """INTEL-05: GET /v1/feed?persona=curator applies limit_override."""
    response = await client.get(
        "/v1/feed?persona=curator",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    # curator preset has limit_override=50
    assert data["limit"] == 50


@pytest.mark.asyncio
async def test_intel_05_persona_learner(client, api_key_header, one_item):
    """INTEL-05: GET /v1/feed?persona=learner uses docs/practice type filter."""
    response = await client.get(
        "/v1/feed?persona=learner",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    # All returned items should be docs or practice type (or empty)
    for item in data["items"]:
        assert item["primary_type"] in (
            "docs",
            "practice",
        ), f"learner persona should only return docs/practice, got {item['primary_type']}"


# ---------------------------------------------------------------------------
# INTEL-06: signals POST/GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_06_post_signal_upvote(client, api_key_header, one_item):
    """INTEL-06: POST /v1/items/{id}/signal records upvote and returns counts."""
    response = await client.post(
        f"/v1/items/{one_item.id}/signal",
        json={"action": "upvote"},
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "item_id" in data
    assert "action" in data
    assert data["action"] == "upvote"
    assert "signal_counts" in data
    assert data["signal_counts"]["upvotes"] >= 1


@pytest.mark.asyncio
async def test_intel_06_get_signals_has_upvotes_field(client, api_key_header, one_item):
    """INTEL-06: GET /v1/items/{id}/signals returns upvotes field."""
    # Create a signal first
    await client.post(
        f"/v1/items/{one_item.id}/signal",
        json={"action": "upvote"},
        headers=api_key_header["headers"],
    )

    response = await client.get(
        f"/v1/items/{one_item.id}/signals",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "upvotes" in data
    assert "bookmarks" in data
    assert "dismissals" in data
    assert "total" in data
    assert data["upvotes"] >= 1


# ---------------------------------------------------------------------------
# INTEL-07: watchlists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_07_create_watchlist(client, api_key_header):
    """INTEL-07: POST /v1/watchlists creates a watchlist (201) with WatchlistResponse."""
    response = await client.post(
        "/v1/watchlists",
        json={
            "name": "MCP Tools",
            "concept": "MCP tools and protocol development",
            "similarity_threshold": 0.8,
        },
        headers=api_key_header["headers"],
    )
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert "name" in data
    assert data["name"] == "MCP Tools"
    assert "concept" in data
    assert "is_active" in data
    assert data["is_active"] is True
    assert "has_embedding" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_intel_07_list_watchlists(client, api_key_header):
    """INTEL-07: GET /v1/watchlists returns list of user's watchlists."""
    # Create one first
    await client.post(
        "/v1/watchlists",
        json={"name": "Agents Watch", "concept": "AI agents and multi-agent systems"},
        headers=api_key_header["headers"],
    )

    response = await client.get(
        "/v1/watchlists",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    watchlist = data[0]
    assert "id" in watchlist
    assert "name" in watchlist
    assert "is_active" in watchlist


# ---------------------------------------------------------------------------
# INTEL-08: embed formats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_08_embed_markdown(client, api_key_header, one_item):
    """INTEL-08: GET /v1/items/{id}/embed?format=markdown returns text/markdown."""
    response = await client.get(
        f"/v1/items/{one_item.id}/embed?format=markdown",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/markdown" in content_type or "text/" in content_type


@pytest.mark.asyncio
async def test_intel_08_embed_slack(client, api_key_header, one_item):
    """INTEL-08: GET /v1/items/{id}/embed?format=slack returns JSON with blocks."""
    response = await client.get(
        f"/v1/items/{one_item.id}/embed?format=slack",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "blocks" in data
    assert isinstance(data["blocks"], list)
    assert len(data["blocks"]) >= 1


@pytest.mark.asyncio
async def test_intel_08_embed_terminal(client, api_key_header, one_item):
    """INTEL-08: GET /v1/items/{id}/embed?format=terminal returns plain text."""
    response = await client.get(
        f"/v1/items/{one_item.id}/embed?format=terminal",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/" in content_type
    # Should contain the item title
    assert one_item.title in response.text


# ---------------------------------------------------------------------------
# INTEL-09: landscape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_09_landscape_returns_ok(client, api_key_header, one_item):
    """INTEL-09: GET /v1/landscape/{domain} returns 200 with required fields."""
    response = await client.get(
        "/v1/landscape/mcp",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "domain" in data
    assert data["domain"] == "mcp"
    assert "momentum_leaders" in data
    assert "positioning" in data
    assert "gaps" in data
    assert isinstance(data["gaps"], list)


@pytest.mark.asyncio
async def test_intel_09_landscape_gaps_are_known_types(
    client, api_key_header, one_item
):
    """INTEL-09: landscape gaps contain valid primary_type values."""
    response = await client.get(
        "/v1/landscape/mcp",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    known_types = {"skill", "tool", "update", "practice", "docs"}
    for gap in data["gaps"]:
        assert gap in known_types, f"Unexpected gap type: {gap}"


# ---------------------------------------------------------------------------
# INTEL-10: threads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_10_threads_returns_ok_empty(client, api_key_header):
    """INTEL-10: GET /v1/threads returns 200 with threads list (may be empty)."""
    response = await client.get(
        "/v1/threads",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "threads" in data
    assert "total" in data
    assert "offset" in data
    assert "limit" in data
    assert isinstance(data["threads"], list)


@pytest.mark.asyncio
async def test_intel_10_threads_with_clustered_items(
    client, api_key_header, thread_items
):
    """INTEL-10: GET /v1/threads returns threads with required fields."""
    cluster_id, items = thread_items
    response = await client.get(
        "/v1/threads?days=30",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["threads"]) >= 1

    # Verify thread structure
    thread = data["threads"][0]
    assert "thread_id" in thread
    assert "item_count" in thread
    assert "first_seen" in thread
    assert "last_seen" in thread
    assert "momentum_score" in thread
    assert "total_upvotes" in thread
    assert "narrative_summary" in thread
    assert "top_items" in thread
    assert thread["item_count"] >= 2
    assert 0.0 <= thread["momentum_score"] <= 1.0
    assert len(thread["narrative_summary"]) > 0


@pytest.mark.asyncio
async def test_intel_10_thread_detail(client, api_key_header, thread_items):
    """INTEL-10: GET /v1/threads/{cluster_id} returns full detail."""
    cluster_id, items = thread_items
    response = await client.get(
        f"/v1/threads/{cluster_id}",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["thread_id"] == cluster_id
    assert "narrative_summary" in data
    assert "momentum_score" in data
    assert "total_upvotes" in data
    assert "items" in data
    assert len(data["items"]) == 3  # all 3 thread items returned


@pytest.mark.asyncio
async def test_intel_10_thread_detail_404_missing(client, api_key_header):
    """INTEL-10: GET /v1/threads/{nonexistent_id} returns 404."""
    fake_id = str(uuid.uuid4())
    response = await client.get(
        f"/v1/threads/{fake_id}",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_intel_10_narrative_summary_deterministic(
    client, api_key_header, thread_items
):
    """INTEL-10: narrative_summary is deterministic (same output on repeated calls)."""
    cluster_id, _ = thread_items
    resp1 = await client.get(
        f"/v1/threads/{cluster_id}", headers=api_key_header["headers"]
    )
    resp2 = await client.get(
        f"/v1/threads/{cluster_id}", headers=api_key_header["headers"]
    )
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["narrative_summary"] == resp2.json()["narrative_summary"]


# ---------------------------------------------------------------------------
# INTEL-11: sla
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_11_sla_returns_ok(client, api_key_header):
    """INTEL-11: GET /v1/sla returns 200 with required fields."""
    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "newest_item_age_hours" in data  # renamed from max_item_age_hours
    assert "coverage_score" in data
    assert "freshness_guarantee" in data
    assert "items_last_24h" in data
    assert "items_last_7d" in data
    assert "failed_items_last_24h" in data
    assert "credits_exhausted" in data
    assert "source_health_summary" in data
    assert "checked_at" in data


@pytest.mark.asyncio
async def test_intel_11_sla_freshness_guarantee(client, api_key_header):
    """INTEL-11: freshness_guarantee is the static '24h' contract."""
    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    assert response.json()["freshness_guarantee"] == "24h"


@pytest.mark.asyncio
async def test_intel_11_sla_coverage_score_range(client, api_key_header):
    """INTEL-11: coverage_score is in [0.0, 1.0]."""
    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    score = response.json()["coverage_score"]
    assert 0.0 <= score <= 1.0, f"coverage_score out of range: {score}"


# ---------------------------------------------------------------------------
# INTEL-12: contrarian_signals field on IntelItemResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intel_12_contrarian_signals_field_exists(
    client, api_key_header, one_item
):
    """INTEL-12: IntelItemResponse has contrarian_signals field (may be null)."""
    response = await client.get(
        f"/v1/info/{one_item.id}",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    # Field must exist (value can be None/null)
    assert "contrarian_signals" in data


@pytest.mark.asyncio
async def test_intel_12_contrarian_signals_in_feed(client, api_key_header, one_item):
    """INTEL-12: contrarian_signals field present on feed items (may be null)."""
    response = await client.get(
        "/v1/feed?days=30",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert (
            "contrarian_signals" in item
        ), f"contrarian_signals field missing from feed item {item.get('id')}"


@pytest.mark.asyncio
async def test_intel_12_contrarian_signals_in_threads(
    client, api_key_header, thread_items
):
    """INTEL-12: contrarian_signals field present on thread detail items."""
    cluster_id, _ = thread_items
    response = await client.get(
        f"/v1/threads/{cluster_id}",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1
    for item in items:
        assert (
            "contrarian_signals" in item
        ), f"contrarian_signals field missing from thread item {item.get('id')}"
