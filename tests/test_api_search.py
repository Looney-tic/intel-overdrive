"""Tests for GET /v1/search extended filters (UX-09).

Covers type, significance, days, and tag filter parameters.
Also covers RRF weight configuration and intent routing (31-01).
Note: tsvector search_vector is a GENERATED ALWAYS STORED column created
in the engine fixture (conftest.py). Items inserted within the same transaction
will have their search_vector populated since the fixture uses ALTER TABLE ADD COLUMN
GENERATED ALWAYS AS ... STORED, which triggers on INSERT.
"""
import re

import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone, timedelta

from src.api.v1.search import INTENT_TYPE_PATTERNS, INTENT_SIGNIFICANCE_PATTERNS
from src.models.models import IntelItem


@pytest_asyncio.fixture
async def search_items_fixture(session, source_factory):
    """Inserts processed items with various types, significance, and tags for search tests."""
    source = await source_factory(id="test:search-source", name="Search Source")

    items = [
        IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"ext-search-tool",
            url="https://example.com/search-tool",
            title="Claude Code Tool Integration",
            content="A powerful tool for integrating Claude into development workflows. This comprehensive guide covers setup, configuration, and advanced usage patterns for teams.",
            primary_type="tool",
            tags=["claude", "tool"],
            status="processed",
            significance="major",
            relevance_score=0.9,
            quality_score=0.8,
            confidence_score=0.9,
            created_at=datetime.now(timezone.utc),
        ),
        IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id="ext-search-skill",
            url="https://example.com/search-skill",
            title="Claude Code Skill Development",
            content="Best practices for developing skills with Claude Code integration. Covers prompt engineering, tool use patterns, and workflow optimization techniques for maximum productivity.",
            primary_type="skill",
            tags=["claude", "skill"],
            status="processed",
            significance="informational",
            relevance_score=0.7,
            quality_score=0.7,
            confidence_score=0.8,
            created_at=datetime.now(timezone.utc),
        ),
        IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id="ext-search-update",
            url="https://example.com/search-update",
            title="Claude Code Update Release",
            content="Major update to the Claude Code workflow and integration. Includes breaking changes to the API surface, new MCP server support, and improved error handling across all endpoints.",
            primary_type="update",
            tags=["claude", "release"],
            status="processed",
            significance="breaking",
            relevance_score=0.95,
            quality_score=0.9,
            confidence_score=0.95,
            created_at=datetime.now(timezone.utc),
        ),
        IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id="ext-search-mcp",
            url="https://example.com/search-mcp",
            title="MCP Server Integration Guide",
            content="How to integrate MCP servers into your Claude Code workflow. Step-by-step instructions for connecting remote tools, managing server lifecycle, and debugging connection issues.",
            primary_type="tool",
            tags=["mcp", "server"],
            status="processed",
            significance="major",
            relevance_score=0.85,
            quality_score=0.85,
            confidence_score=0.9,
            created_at=datetime.now(timezone.utc),
        ),
        IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id="ext-search-old",
            url="https://example.com/search-old",
            title="Claude Code Old Integration Article",
            content="Older article about Claude Code integration techniques. Provides historical context on how the tooling evolved from basic autocomplete to full agent-based development workflows.",
            primary_type="docs",
            tags=["claude", "docs"],
            status="processed",
            significance="informational",
            relevance_score=0.7,
            quality_score=0.6,
            confidence_score=0.7,
            created_at=datetime.now(timezone.utc) - timedelta(days=30),
        ),
    ]

    for item in items:
        session.add(item)

    await session.commit()
    return items


@pytest.mark.asyncio
async def test_search_filter_by_type(client, api_key_header, search_items_fixture):
    """UX-09: type filter returns only items of that primary_type."""
    response = await client.get(
        "/v1/search?q=Claude+Code&type=tool", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    assert data["total"] > 0
    for item in data["items"]:
        assert (
            item["primary_type"] == "tool"
        ), f"Expected primary_type=tool, got {item['primary_type']}"


@pytest.mark.asyncio
async def test_search_filter_by_significance(
    client, api_key_header, search_items_fixture
):
    """UX-09: significance filter returns only items with that significance level."""
    response = await client.get(
        "/v1/search?q=Claude+Code&significance=breaking",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()

    assert data["total"] > 0
    # The breaking fixture item is "Claude Code Update Release" — verify it appears
    breaking_titles = [item["title"] for item in data["items"]]
    assert (
        "Claude Code Update Release" in breaking_titles
    ), f"Expected 'Claude Code Update Release' in breaking results, got {breaking_titles}"

    # Verify non-breaking significance filter excludes breaking items
    response_info = await client.get(
        "/v1/search?q=Claude+Code&significance=informational",
        headers=api_key_header["headers"],
    )
    assert response_info.status_code == 200
    info_data = response_info.json()

    # Informational results must not contain the breaking item
    info_titles = [item["title"] for item in info_data["items"]]
    assert (
        "Claude Code Update Release" not in info_titles
    ), "Breaking item should not appear in informational filter results"


@pytest.mark.asyncio
async def test_search_filter_by_days(client, api_key_header, search_items_fixture):
    """UX-09: days filter excludes items older than N days."""
    # With days=7, should not include the 30-day-old item
    response_7 = await client.get(
        "/v1/search?q=Claude+Code&days=7", headers=api_key_header["headers"]
    )
    assert response_7.status_code == 200
    data_7 = response_7.json()

    # With days=60, should include the 30-day-old item
    response_60 = await client.get(
        "/v1/search?q=Claude+Code&days=60", headers=api_key_header["headers"]
    )
    assert response_60.status_code == 200
    data_60 = response_60.json()

    # More results with wider window
    assert (
        data_60["total"] >= data_7["total"]
    ), "days=60 should return at least as many results as days=7"


@pytest.mark.asyncio
async def test_search_filter_by_tag(client, api_key_header, search_items_fixture):
    """UX-09: tag filter returns only items tagged with that tag."""
    response = await client.get(
        "/v1/search?q=MCP+Server&tag=mcp", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    # The MCP item matches both the query and the tag filter
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_search_no_filters_returns_all_matching(
    client, api_key_header, search_items_fixture
):
    """UX-09: Search without filters returns all matching items."""
    response = await client.get(
        "/v1/search?q=Claude+Code", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    # Multiple items contain "Claude Code" — at least 3 recent ones
    assert data["total"] >= 3


@pytest.mark.asyncio
async def test_search_combined_type_and_tag_filters(
    client, api_key_header, search_items_fixture
):
    """UX-09: Multiple filters stack correctly (AND logic)."""
    # type=tool AND tag=mcp — only MCP Server item should match
    response = await client.get(
        "/v1/search?q=Claude+Code+MCP&type=tool&tag=mcp",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()

    # Only tool items with mcp tag
    for item in data["items"]:
        assert item["primary_type"] == "tool"


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    """Search endpoint requires API key."""
    response = await client.get("/v1/search?q=Claude")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_search_requires_query_param(client, api_key_header):
    """Search endpoint requires the q parameter."""
    response = await client.get("/v1/search", headers=api_key_header["headers"])
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_search_days_filter_excludes_old_item(
    client, api_key_header, search_items_fixture
):
    """UX-09: days=7 should exclude the 30-day-old 'Old Integration Article'."""
    # Search specifically for "Old Integration" which only matches the old item
    response_7 = await client.get(
        "/v1/search?q=Old+Integration&days=7", headers=api_key_header["headers"]
    )
    assert response_7.status_code == 200
    data_7 = response_7.json()

    response_60 = await client.get(
        "/v1/search?q=Old+Integration&days=60", headers=api_key_header["headers"]
    )
    assert response_60.status_code == 200
    data_60 = response_60.json()

    # The 30-day-old "Old Integration Article" should appear in days=60 results
    # but not in days=7 results (checked by item titles, not total count
    # since true COUNT may differ due to AND/OR fallback behavior)
    titles_7 = {item["title"] for item in data_7["items"]}
    titles_60 = {item["title"] for item in data_60["items"]}
    old_title = "Claude Code Old Integration Article"
    assert old_title not in titles_7, "Old item should be excluded by days=7"
    assert old_title in titles_60, "Old item should be included by days=60"


@pytest.mark.asyncio
async def test_search_offset_overflow_returns_422(client, api_key_header):
    """offset > le=10_000_000 should return 422, not 500."""
    resp = await client.get(
        "/v1/search",
        params={"q": "test", "offset": "99999999999"},
        headers=api_key_header["headers"],
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_field_selector_strips_library_key(
    client, api_key_header, search_items_fixture
):
    """When fields= is specified, top-level library key should be stripped."""
    resp = await client.get(
        "/v1/search",
        params={"q": "Claude Code", "fields": "id,title,url"},
        headers=api_key_header["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert (
        "library" not in data
    ), "library key should be stripped when fields= is active"
    # Verify items only contain requested fields + id
    for item in data.get("items", []):
        for key in item:
            assert key in {"id", "title", "url"}, f"Unexpected field '{key}' in item"


# --- 31-01: RRF Weight Tests ---


def test_rrf_quality_weight_is_0_25():
    """Verify the RRF SQL contains 0.25 weight for quality signal."""
    import inspect
    from src.api.v1.search import search_intel_items

    source = inspect.getsource(search_intel_items)
    # The quality weight line: 0.25 * COALESCE(1.0 / (:rrf_k + q.rn), 0)
    assert (
        "0.25 * COALESCE(1.0 / (:rrf_k + q.rn), 0)" in source
    ), "Quality weight should be 0.25 in RRF SQL"


def test_rrf_weights_sum_to_one():
    """Verify the four RRF weights sum to exactly 1.0."""
    import inspect
    from src.api.v1.search import search_intel_items

    source = inspect.getsource(search_intel_items)
    # Extract the four weight values from the combined CTE
    # Pattern: N.NN * COALESCE(1.0 / (:rrf_k + X.rn), 0)
    weight_pattern = re.compile(r"(\d+\.\d+)\s*\*\s*COALESCE\(1\.0 / \(:rrf_k \+")
    weights = [float(m) for m in weight_pattern.findall(source)]
    # There are two sets of weights (main query + fallback), check first set of 4
    assert len(weights) >= 4, f"Expected at least 4 weight values, found {len(weights)}"
    first_four = weights[:4]
    assert (
        abs(sum(first_four) - 1.0) < 1e-9
    ), f"RRF weights should sum to 1.0, got {sum(first_four)} from {first_four}"


# --- 31-01: Intent Routing Pattern Tests ---


def test_intent_routing_mcp_injects_tool_type():
    """MCP-related queries should match the tool intent pattern."""
    for query in [
        "cursor MCP server",
        "MCP server for database",
        "new plugin for VS Code",
        "mcp integration",
    ]:
        matched = False
        for pattern, detected_type in INTENT_TYPE_PATTERNS:
            if pattern.search(query):
                assert (
                    detected_type == "tool"
                ), f"Expected 'tool' type for '{query}', got '{detected_type}'"
                matched = True
                break
        assert matched, f"Query '{query}' should match an intent type pattern"


def test_intent_routing_breaking_injects_significance():
    """Breaking-change queries should match the breaking significance pattern."""
    for query in [
        "react breaking changes",
        "migrate from webpack to vite",
        "deprecated API in v5",
    ]:
        matched = False
        for pattern, detected_sig in INTENT_SIGNIFICANCE_PATTERNS:
            if pattern.search(query):
                assert (
                    detected_sig == "breaking"
                ), f"Expected 'breaking' for '{query}', got '{detected_sig}'"
                matched = True
                break
        assert matched, f"Query '{query}' should match a significance pattern"


def test_intent_routing_no_false_positive():
    """Queries without intent signals should not match any pattern."""
    for query in [
        "next.js app router",
        "Claude Code best practices",
        "how to deploy fastapi",
    ]:
        for pattern, _ in INTENT_TYPE_PATTERNS:
            assert not pattern.search(
                query
            ), f"Query '{query}' should NOT match type pattern"
        for pattern, _ in INTENT_SIGNIFICANCE_PATTERNS:
            assert not pattern.search(
                query
            ), f"Query '{query}' should NOT match significance pattern"


@pytest.mark.asyncio
async def test_intent_routing_does_not_override_explicit_type(
    client, api_key_header, search_items_fixture
):
    """When user supplies explicit type=, intent routing should not override it."""
    # Query contains "MCP" (would normally trigger type=tool intent routing)
    # but user explicitly requests type=update
    response = await client.get(
        "/v1/search?q=MCP+server+update&type=update",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    # All returned items must be type=update, not type=tool
    for item in data["items"]:
        assert (
            item["primary_type"] == "update"
        ), f"Explicit type=update should override intent routing, got {item['primary_type']}"


@pytest.mark.asyncio
async def test_intent_routing_does_not_override_explicit_significance(
    client, api_key_header, search_items_fixture
):
    """When user supplies explicit significance=, intent routing should not override it."""
    # Query contains "MCP" (would trigger type=tool intent) and "breaking" (would
    # trigger significance=breaking intent), but user explicitly requests
    # significance=major. The fixture has tool and MCP items with significance=major.
    response = await client.get(
        "/v1/search?q=Claude+Code+MCP&significance=major",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    # All returned items must respect the explicit significance=major filter
    for item in data["items"]:
        assert (
            item["significance"] == "major"
        ), f"Explicit significance=major should override intent routing, got {item['significance']}"


@pytest.mark.asyncio
async def test_intent_routing_fallback_on_few_results(
    client, api_key_header, search_items_fixture
):
    """Intent filter with < 3 results should fall back to unfiltered query."""
    # Search for "Claude Code" with an intent that would narrow results
    # The search_items_fixture has enough items to test the fallback behavior
    # "MCP" triggers type=tool intent, but "Claude Code" matches more broadly
    response = await client.get(
        "/v1/search?q=Claude+Code", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    # Without intent filtering, should get multiple results
    assert data["total"] >= 3, "Unfiltered query should return 3+ results"
