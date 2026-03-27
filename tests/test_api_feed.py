import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from src.models.models import IntelItem, Source
from src.api.v1.feed import expand_profile_tags


@pytest_asyncio.fixture
async def intel_items_fixture(session, source_factory):
    """Inserts 5-10 processed IntelItem rows for testing."""
    source = await source_factory(id="test:feed-source", name="Feed Source")

    items_data = [
        {
            "title": "Claude Code Python SDK",
            "primary_type": "skill",
            "tags": ["python", "claude"],
        },
        {"title": "MCP Server Tool", "primary_type": "tool", "tags": ["mcp", "server"]},
        {
            "title": "Claude Update v3",
            "primary_type": "update",
            "tags": ["claude", "release"],
        },
        {
            "title": "Testing Best Practices",
            "primary_type": "practice",
            "tags": ["testing"],
        },
        {"title": "API Documentation Guide", "primary_type": "docs", "tags": ["api"]},
    ]

    items = []
    for data in items_data:
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"ext-{uuid.uuid4()}",
            url=f"https://example.com/{uuid.uuid4()}",
            title=data["title"],
            content="Sample content for testing the intelligence feed pipeline. This provides enough text to pass the minimum content length quality gate for search indexing.",
            primary_type=data["primary_type"],
            tags=data["tags"],
            status="processed",
            relevance_score=0.8,
            quality_score=0.8,
            confidence_score=0.9,
            created_at=datetime.now(timezone.utc),
        )
        session.add(item)
        items.append(item)

    # One old item (30 days ago)
    old_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id=f"ext-old",
        url=f"https://example.com/old",
        title="Old Item",
        content="Old content that was published a while ago. This article covers legacy integration patterns that have since been superseded by newer approaches in the ecosystem.",
        primary_type="skill",
        tags=["python"],
        status="processed",
        relevance_score=0.5,
        quality_score=0.5,
        confidence_score=0.7,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    session.add(old_item)
    items.append(old_item)

    # One pending item
    pending_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id=f"ext-pending",
        url=f"https://example.com/pending",
        title="Pending Item",
        content="Pending content awaiting processing by the classification pipeline. Once processed, this item will be scored for quality and relevance to the developer ecosystem.",
        primary_type="skill",
        tags=["python"],
        status="pending",
        relevance_score=0.5,
    )
    session.add(pending_item)

    await session.commit()
    return items


@pytest.mark.asyncio
async def test_feed_returns_paginated(client, api_key_header, intel_items_fixture):
    """API-03: Feed returns paginated results."""
    response = await client.get("/v1/feed", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert data["limit"] == 20
    assert data["offset"] == 0
    # 5 recent items, 1 old, 1 pending. Default 'days=7' should return 5.
    assert len(data["items"]) == 5
    assert data["total"] == 5


@pytest.mark.asyncio
async def test_feed_filter_by_type(client, api_key_header, intel_items_fixture):
    """API-03: Filter feed by type."""
    response = await client.get(
        "/v1/feed?type=skill", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["primary_type"] == "skill"


@pytest.mark.asyncio
async def test_feed_filter_by_tag(client, api_key_header, intel_items_fixture):
    """API-03: Filter feed by tag."""
    response = await client.get("/v1/feed?tag=mcp", headers=api_key_header["headers"])
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert "mcp" in items[0]["tags"]


@pytest.mark.asyncio
async def test_feed_filter_by_days(client, api_key_header, intel_items_fixture):
    """API-03: Filter feed by days."""
    # 30 days ago item should be included with days=60
    response = await client.get("/v1/feed?days=60", headers=api_key_header["headers"])
    assert response.status_code == 200
    assert response.json()["total"] == 6


@pytest.mark.asyncio
async def test_search_full_text(client, api_key_header, intel_items_fixture):
    """API-04: Full-text search works."""
    response = await client.get(
        "/v1/search?q=Python SDK", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert "Python SDK" in data["items"][0]["title"]
    assert "rank" in data["items"][0]


@pytest.mark.asyncio
async def test_info_endpoint(client, api_key_header, intel_items_fixture):
    """API-05: Info endpoint returns details."""
    item_id = intel_items_fixture[0].id
    response = await client.get(
        f"/v1/info/{item_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    assert response.json()["title"] == intel_items_fixture[0].title


@pytest.mark.asyncio
async def test_info_not_found(client, api_key_header, intel_items_fixture):
    """API-05: Non-existent item ID returns 404."""
    response = await client.get(
        f"/v1/info/{uuid.uuid4()}", headers=api_key_header["headers"]
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_info_non_processed_hidden(
    client, api_key_header, intel_items_fixture, session
):
    """API-05: Non-processed items (status != 'processed') return 404 from info endpoint."""
    # Get existing source from intel_items_fixture
    result = await session.execute(select(Source).limit(1))
    source = result.scalar_one()

    pending = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-pending-direct",
        url="https://example.com/pending-direct",
        title="Pending Direct",
        content="Content",
        primary_type="skill",
        tags=[],
        status="pending",
        relevance_score=0.5,
    )
    session.add(pending)
    await session.commit()

    response = await client.get(
        f"/v1/info/{pending.id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_feed_pagination(client, api_key_header, intel_items_fixture):
    """API-03: Pagination offset/limit works correctly with no overlap."""
    resp1 = await client.get(
        "/v1/feed?limit=2&offset=0", headers=api_key_header["headers"]
    )
    assert resp1.status_code == 200
    page1 = resp1.json()["items"]
    assert len(page1) == 2

    resp2 = await client.get(
        "/v1/feed?limit=2&offset=2", headers=api_key_header["headers"]
    )
    assert resp2.status_code == 200
    page2 = resp2.json()["items"]
    assert len(page2) == 2

    # No overlap between pages
    page1_ids = {item["id"] for item in page1}
    page2_ids = {item["id"] for item in page2}
    assert page1_ids.isdisjoint(page2_ids), "Pages overlap"


@pytest.mark.asyncio
async def test_search_no_results(client, api_key_header, intel_items_fixture):
    """API-04: Search returns empty list for no matches."""
    response = await client.get(
        "/v1/search?q=nonexistent_xyzzy_foobar", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


@pytest.mark.asyncio
async def test_feed_since_filter(client, api_key_header, session, source_factory):
    """UX-01: since= param returns only items newer than the given timestamp."""
    source = await source_factory(id="test:since-source", name="Since Source")

    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=12)

    # Recent item — should be returned
    recent_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-since-recent",
        url="https://example.com/since-recent",
        title="Recent Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )

    # Old item (25 hours ago) — should be excluded by since filter
    old_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-since-old",
        url="https://example.com/since-old",
        title="Old Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )

    session.add(recent_item)
    session.add(old_item)
    await session.commit()

    # Format without timezone offset sign to avoid URL encoding issues
    since_param = cutoff_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    response = await client.get(
        f"/v1/feed?since={since_param}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    returned_ids = [item["id"] for item in data["items"]]
    assert str(recent_item.id) in returned_ids, "Recent item should be returned"
    assert (
        str(old_item.id) not in returned_ids
    ), "Old item should be excluded by since filter"


@pytest.mark.asyncio
async def test_feed_sort_significance(client, api_key_header, session, source_factory):
    """UX-08: sort=significance returns breaking items before informational."""
    source = await source_factory(id="test:sort-source", name="Sort Source")

    # Informational item with slightly higher relevance_score (to test sort overrides score)
    info_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-sort-info",
        url="https://example.com/sort-info",
        title="Informational Update",
        content="Content",
        primary_type="update",
        tags=[],
        status="processed",
        significance="informational",
        relevance_score=0.95,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )

    # Breaking item with lower relevance_score — should appear first with sort=significance
    breaking_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-sort-breaking",
        url="https://example.com/sort-breaking",
        title="Breaking Change",
        content="Content",
        primary_type="update",
        tags=[],
        status="processed",
        significance="breaking",
        relevance_score=0.7,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
    )

    session.add(info_item)
    session.add(breaking_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?sort=significance&days=7", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]

    assert len(items) >= 2
    # Find the indices of breaking and informational items
    breaking_indices = [
        i for i, item in enumerate(items) if item["id"] == str(breaking_item.id)
    ]
    info_indices = [
        i for i, item in enumerate(items) if item["id"] == str(info_item.id)
    ]

    assert breaking_indices, "Breaking item should appear in results"
    assert info_indices, "Informational item should appear in results"
    assert min(breaking_indices) < min(
        info_indices
    ), "Breaking item should appear before informational item with sort=significance"


@pytest.mark.asyncio
async def test_feed_items_have_source_name_field(
    client, api_key_header, session, source_factory
):
    """UX-04: Feed items include source_name field."""
    source = await source_factory(
        id="test:source-name-source", name="Source Name Source"
    )

    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-source-name",
        url="https://example.com/source-name",
        title="Item with Source Name",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        source_name="Example Source",
    )
    session.add(item)
    await session.commit()

    response = await client.get("/v1/feed?days=7", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    found_item = next((i for i in data["items"] if i["id"] == str(item.id)), None)
    assert found_item is not None, "Item should appear in feed"
    assert "source_name" in found_item, "Feed items should include source_name field"
    assert found_item["source_name"] == "Example Source"


@pytest.mark.asyncio
async def test_feed_items_have_published_at_field(
    client, api_key_header, session, source_factory
):
    """UX-04: Feed items include published_at field."""
    source = await source_factory(
        id="test:published-at-source", name="Published At Source"
    )

    published_time = datetime.now(timezone.utc) - timedelta(hours=2)

    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-published-at",
        url="https://example.com/published-at",
        title="Item with Published At",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        published_at=published_time,
    )
    session.add(item)
    await session.commit()

    response = await client.get("/v1/feed?days=7", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    found_item = next((i for i in data["items"] if i["id"] == str(item.id)), None)
    assert found_item is not None, "Item should appear in feed"
    assert "published_at" in found_item, "Feed items should include published_at field"
    assert found_item["published_at"] is not None


@pytest.mark.asyncio
async def test_prefilter_with_profile(client, api_key_header, intel_items_fixture):
    """API-10: Profile tech_stack boosts matching items to top of result page."""
    # Set user profile with tech_stack=["python"]
    profile_resp = await client.post(
        "/v1/profile",
        json={"tech_stack": ["python"], "skills": []},
        headers=api_key_header["headers"],
    )
    assert profile_resp.status_code == 200

    # Fetch the feed — items tagged "python" should appear before others
    resp = await client.get(
        "/v1/feed?limit=10&days=60", headers=api_key_header["headers"]
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) > 0

    # Find where the last python-tagged item appears and the first non-python item
    python_indices = [
        i for i, item in enumerate(items) if "python" in item.get("tags", [])
    ]
    non_python_indices = [
        i for i, item in enumerate(items) if "python" not in item.get("tags", [])
    ]

    if python_indices:
        # Profile boost should place at least one python-tagged item in top 3
        assert min(python_indices) <= 2, (
            f"At least one python-tagged item should be in top 3 "
            f"with profile boost, but first appears at index {min(python_indices)}"
        )


# ---------------------------------------------------------------------------
# expand_profile_tags unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_result_coverage_monitored(
    client, api_key_header, session, source_factory
):
    """PIPE-07: Zero-result response includes coverage count for monitored topic."""
    # Create a source with "claude" in the name so coverage query matches
    await source_factory(id="test:coverage-claude", name="Claude Releases")

    # Query for "claude" with very short window so no items match
    response = await client.get(
        "/v1/feed?q=claude&days=1", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    # With no items in the last day, total should be 0 and coverage metadata present
    if data["total"] == 0:
        assert "topic_sources_monitored" in data
        assert data["topic_sources_monitored"] >= 1
        assert "coverage_note" in data
        assert "monitors" in data["coverage_note"]
    # If items happen to exist, the coverage metadata won't be present (only on zero results)


@pytest.mark.asyncio
async def test_zero_result_coverage_unmonitored(client, api_key_header):
    """PIPE-07: Zero-result response for unmonitored topic shows count 0."""
    response = await client.get(
        "/v1/feed?q=CompletelyUnknownTopic12345zzz&days=1",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["topic_sources_monitored"] == 0
    assert "may not be in" in data["coverage_note"]


@pytest.mark.asyncio
async def test_zero_result_no_coverage_without_query(
    client, api_key_header, session, source_factory
):
    """PIPE-07: Zero-result response without q/tag omits coverage metadata."""
    # Use a very narrow type filter to get zero results
    response = await client.get(
        "/v1/feed?type=docs&days=1", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    if data["total"] == 0:
        assert "topic_sources_monitored" not in data
        assert "coverage_note" not in data


@pytest.mark.asyncio
async def test_tier_boost_in_feed_order(
    client, api_key_header, session, source_factory
):
    """PIPE-07: tier1 source items rank above tier3 items at similar relevance."""
    tier1_source = await source_factory(
        id="test:tier1-boost", name="Official Tier1", tier="tier1"
    )
    tier3_source = await source_factory(
        id="test:tier3-boost", name="Community Tier3", tier="tier3"
    )

    now = datetime.now(timezone.utc)

    # Both items have identical relevance, quality, confidence, and timestamps
    tier1_item = IntelItem(
        id=uuid.uuid4(),
        source_id=tier1_source.id,
        external_id="ext-tier1-boost",
        url=f"https://example.com/tier1-boost-{uuid.uuid4()}",
        title="Tier1 Official Item",
        content="Official content from tier1 source for ranking test. This item should surface above community tier3 items when relevance scores are similar.",
        primary_type="tool",
        tags=["tier-test"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )
    tier3_item = IntelItem(
        id=uuid.uuid4(),
        source_id=tier3_source.id,
        external_id="ext-tier3-boost",
        url=f"https://example.com/tier3-boost-{uuid.uuid4()}",
        title="Tier3 Community Item",
        content="Community content from tier3 source for ranking test. This item should rank below official tier1 items when relevance scores are similar.",
        primary_type="tool",
        tags=["tier-test"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )

    session.add(tier1_item)
    session.add(tier3_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=tier-test&days=7", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    assert len(items) == 2, f"Expected 2 tier-test items, got {len(items)}"

    # tier1 item should appear first (index 0)
    assert items[0]["id"] == str(
        tier1_item.id
    ), f"tier1 item should rank first but got {items[0]['title']}"
    assert items[1]["id"] == str(
        tier3_item.id
    ), f"tier3 item should rank second but got {items[1]['title']}"


# ---------------------------------------------------------------------------
# expand_profile_tags unit tests
# ---------------------------------------------------------------------------


def test_expand_profile_tags_none():
    """None profile returns empty list."""
    assert expand_profile_tags(None) == []


def test_expand_profile_tags_empty():
    """Empty profile dict returns empty list."""
    assert expand_profile_tags({}) == []


def test_expand_profile_tags_tech_stack_only():
    """tech_stack items pass through as-is."""
    result = expand_profile_tags({"tech_stack": ["python", "fastapi"]})
    assert "python" in result
    assert "fastapi" in result


def test_expand_profile_tags_skills_expanded():
    """Known skills expand to related tags."""
    result = expand_profile_tags({"skills": ["browser-automation"]})
    assert "playwright" in result
    assert "puppeteer" in result
    assert "browser-automation" in result


def test_expand_profile_tags_tools_expanded():
    """Known tools expand to related tags."""
    result = expand_profile_tags({"tools": ["claude-code"]})
    assert "claude-code" in result
    assert "claude" in result
    assert "anthropic" in result
    assert "mcp" in result


def test_expand_profile_tags_providers_expanded():
    """Known providers expand to related tags."""
    result = expand_profile_tags({"providers": ["anthropic"]})
    assert "anthropic" in result
    assert "claude" in result
    assert "haiku" in result
    assert "sonnet" in result


def test_expand_profile_tags_unknown_pass_through():
    """Unknown tools/providers/skills pass through as-is."""
    result = expand_profile_tags(
        {
            "skills": ["unknown-skill"],
            "tools": ["unknown-tool"],
            "providers": ["unknown-provider"],
        }
    )
    assert "unknown-skill" in result
    assert "unknown-tool" in result
    assert "unknown-provider" in result


def test_expand_profile_tags_full_profile():
    """Full profile combines all expansion sources without duplicates."""
    result = expand_profile_tags(
        {
            "tech_stack": ["python"],
            "skills": ["agentic-engineering"],
            "tools": ["claude-code"],
            "providers": ["anthropic"],
        }
    )
    tags = set(result)
    # tech_stack
    assert "python" in tags
    # skill expansion
    assert "multi-agent" in tags
    # tool expansion
    assert "hooks" in tags
    # provider expansion
    assert "haiku" in tags
    # No duplicates (claude appears in both tool and provider expansion)
    assert len(result) == len(tags)


# ---------------------------------------------------------------------------
# Feed quality floor tests (Phase 31)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_excludes_quality_below_0_3(
    client, api_key_header, session, source_factory
):
    """Phase 31: Items with quality_score < 0.3 are excluded from feed."""
    source = await source_factory(id="test:quality-floor", name="Quality Floor Source")

    now = datetime.now(timezone.utc)

    # Low quality item -- should be excluded
    low_q_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-low-quality",
        url=f"https://example.com/low-quality-{uuid.uuid4()}",
        title="Low Quality Noise Item",
        content="Low quality content that should be filtered out by the quality floor. This item has a score below the 0.3 threshold.",
        primary_type="tool",
        tags=["quality-floor-test"],
        status="processed",
        relevance_score=0.5,
        quality_score=0.1,
        confidence_score=0.5,
        significance="minor",
        created_at=now,
    )

    # Good quality item -- should be included
    good_q_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-good-quality",
        url=f"https://example.com/good-quality-{uuid.uuid4()}",
        title="Good Quality Item",
        content="Good quality content that should pass the quality floor filter. This item has a score above the 0.3 threshold.",
        primary_type="tool",
        tags=["quality-floor-test"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.5,
        confidence_score=0.9,
        significance="minor",
        created_at=now,
    )

    session.add(low_q_item)
    session.add(good_q_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=quality-floor-test&days=7", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data["items"]}

    assert str(good_q_item.id) in returned_ids, "Good quality item should be in feed"
    assert (
        str(low_q_item.id) not in returned_ids
    ), "Low quality item (0.1) should be excluded by quality floor"


@pytest.mark.asyncio
async def test_feed_includes_breaking_despite_low_quality(
    client, api_key_header, session, source_factory
):
    """Phase 31: Breaking significance items bypass the quality floor."""
    source = await source_factory(
        id="test:breaking-bypass", name="Breaking Bypass Source"
    )

    now = datetime.now(timezone.utc)

    breaking_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-breaking-low-q",
        url=f"https://example.com/breaking-low-q-{uuid.uuid4()}",
        title="Breaking Change Despite Low Quality",
        content="A breaking change item with low quality score. Should still appear in feed because breaking significance bypasses the quality floor.",
        primary_type="update",
        tags=["breaking-bypass-test"],
        status="processed",
        relevance_score=0.5,
        quality_score=0.1,
        confidence_score=0.5,
        significance="breaking",
        created_at=now,
    )

    session.add(breaking_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=breaking-bypass-test&days=7", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data["items"]}

    assert (
        str(breaking_item.id) in returned_ids
    ), "Breaking item should appear despite quality_score=0.1"


@pytest.mark.asyncio
async def test_feed_includes_major_despite_low_quality(
    client, api_key_header, session, source_factory
):
    """Phase 31: Major significance items bypass the quality floor."""
    source = await source_factory(id="test:major-bypass", name="Major Bypass Source")

    now = datetime.now(timezone.utc)

    major_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-major-low-q",
        url=f"https://example.com/major-low-q-{uuid.uuid4()}",
        title="Major Update Despite Low Quality",
        content="A major update item with low quality score. Should still appear in feed because major significance bypasses the quality floor.",
        primary_type="update",
        tags=["major-bypass-test"],
        status="processed",
        relevance_score=0.5,
        quality_score=0.1,
        confidence_score=0.5,
        significance="major",
        created_at=now,
    )

    session.add(major_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=major-bypass-test&days=7", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data["items"]}

    assert (
        str(major_item.id) in returned_ids
    ), "Major item should appear despite quality_score=0.1"


# ---------------------------------------------------------------------------
# Seed script verification test (Phase 31)
# ---------------------------------------------------------------------------


def test_phase31_seed_sources_defined():
    """Phase 31: Seed script defines >= 7 framework sources with valid configs."""
    import sys

    sys.path.insert(0, ".")
    # Import the source list from the seed script
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed_phase31", "scripts/seed_phase31_sources.py"
    )
    mod = importlib.util.module_from_spec(spec)
    # Only load module-level constants, don't execute seed()
    spec.loader.exec_module(mod)

    sources = mod.FRAMEWORK_SOURCES
    assert len(sources) >= 7, f"Expected >= 7 sources, got {len(sources)}"

    ids = [s["id"] for s in sources]
    assert "github-releases:vercel/next.js" in ids
    assert "github-releases:facebook/react" in ids
    assert "github-releases:microsoft/TypeScript" in ids
    assert "github-releases:nodejs/node" in ids

    # All sources use github-releases type
    for s in sources:
        assert (
            s["type"] == "github-releases"
        ), f"Source {s['id']} should be github-releases, got {s['type']}"
        assert s["url"].endswith(".atom"), f"Source {s['id']} URL should end with .atom"

    # Node.js should use daily poll interval
    node_src = next(s for s in sources if s["id"] == "github-releases:nodejs/node")
    assert (
        node_src["poll_interval_seconds"] == 86400
    ), f"Node.js should use 86400s poll interval, got {node_src['poll_interval_seconds']}"


# ---------------------------------------------------------------------------
# Composite feed ranking tests (Phase 32)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_composite_ranking_quality_matters(
    client, api_key_header, session, source_factory
):
    """Phase 32: Quality score has real weight in composite ranking (not a tiebreaker)."""
    source = await source_factory(
        id="test:composite-quality", name="Composite Quality Source"
    )

    now = datetime.now(timezone.utc)

    # Two items: same significance (minor), same tier, same relevance, same age
    # Only difference: quality_score (0.9 vs 0.3)
    high_q_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-composite-high-q",
        url=f"https://example.com/composite-high-q-{uuid.uuid4()}",
        title="High Quality Composite Item",
        content="High quality content for composite ranking test. This item should rank above the low quality item due to the 30 percent quality weight in the composite score.",
        primary_type="tool",
        tags=["composite-test"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.9,
        confidence_score=0.9,
        significance="minor",
        created_at=now,
    )

    low_q_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-composite-low-q",
        url=f"https://example.com/composite-low-q-{uuid.uuid4()}",
        title="Low Quality Composite Item",
        content="Low quality content for composite ranking test. This item should rank below the high quality item due to the 30 percent quality weight in the composite score.",
        primary_type="tool",
        tags=["composite-test"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.3,
        confidence_score=0.9,
        significance="minor",
        created_at=now,
    )

    session.add(high_q_item)
    session.add(low_q_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=composite-test&days=7", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    assert len(items) == 2, f"Expected 2 composite-test items, got {len(items)}"

    # High quality item should rank first due to composite score
    assert items[0]["id"] == str(high_q_item.id), (
        f"High quality item (0.9) should rank above low quality item (0.3) "
        f"with composite scoring, but got {items[0]['title']} first"
    )


@pytest.mark.asyncio
async def test_feed_significance_partition_preserved(
    client, api_key_header, session, source_factory
):
    """Phase 32: Significance partition is a hard sort -- breaking beats minor regardless of quality."""
    source = await source_factory(
        id="test:sig-partition", name="Significance Partition Source"
    )

    now = datetime.now(timezone.utc)

    # Breaking item with LOW quality (0.2) -- should still appear first
    breaking_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-sig-breaking",
        url=f"https://example.com/sig-breaking-{uuid.uuid4()}",
        title="Breaking With Low Quality",
        content="A breaking change with very low quality score. Should still rank first because significance is a hard partition in the composite ranking.",
        primary_type="update",
        tags=["sig-partition-test"],
        status="processed",
        relevance_score=0.5,
        quality_score=0.2,
        confidence_score=0.5,
        significance="breaking",
        created_at=now,
    )

    # Minor item with HIGH quality (0.95) -- should appear after breaking
    minor_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-sig-minor",
        url=f"https://example.com/sig-minor-{uuid.uuid4()}",
        title="Minor With High Quality",
        content="A minor update with very high quality score. Should still rank after the breaking item because significance is a hard partition in the composite ranking.",
        primary_type="update",
        tags=["sig-partition-test"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.95,
        confidence_score=0.9,
        significance="minor",
        created_at=now,
    )

    session.add(breaking_item)
    session.add(minor_item)
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=sig-partition-test&sort=significance&days=7",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    assert len(items) == 2, f"Expected 2 sig-partition-test items, got {len(items)}"

    # Breaking must be first despite low quality
    assert items[0]["id"] == str(breaking_item.id), (
        f"Breaking item (quality=0.2) should rank above minor item (quality=0.95) "
        f"because significance is a hard partition, but got {items[0]['title']} first"
    )


@pytest.mark.asyncio
async def test_feed_semantic_distance_primary_with_query(
    client, api_key_header, session, source_factory
):
    """Phase 32: When q param is provided with embedding, semantic distance is primary sort."""
    source = await source_factory(id="test:sem-primary", name="Semantic Primary Source")

    now = datetime.now(timezone.utc)

    # Create two items with distinct content. When a q is provided,
    # the endpoint attempts embedding-based search. If embedding fails
    # (test env has no Voyage API key), it falls back to full-text search.
    # Either way, the item matching the query should rank above the unrelated one.
    matching_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-sem-match",
        url=f"https://example.com/sem-match-{uuid.uuid4()}",
        title="Playwright Browser Automation Testing Framework",
        content="Playwright is a browser automation testing framework for end-to-end testing of web applications. It supports Chromium Firefox and WebKit browsers with a single API.",
        primary_type="tool",
        tags=["sem-primary-test", "playwright"],
        status="processed",
        relevance_score=0.5,
        quality_score=0.5,
        confidence_score=0.9,
        significance="minor",
        created_at=now,
    )

    unrelated_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-sem-unrelated",
        url=f"https://example.com/sem-unrelated-{uuid.uuid4()}",
        title="Database Migration Strategy",
        content="Database migration strategies for moving from MySQL to PostgreSQL including schema conversion and data validation techniques.",
        primary_type="docs",
        tags=["sem-primary-test", "database"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.9,
        confidence_score=0.9,
        significance="minor",
        created_at=now,
    )

    session.add(matching_item)
    session.add(unrelated_item)
    await session.commit()

    # Query for "playwright browser automation" -- matching_item should rank first
    # even though unrelated_item has higher relevance and quality scores
    response = await client.get(
        "/v1/feed?q=playwright+browser+automation&tag=sem-primary-test&days=7",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]

    # In test env, embedding may not be available -- full-text search fallback is used.
    # Either way, the matching item should rank first (full-text match on title/content).
    if len(items) >= 2:
        assert items[0]["id"] == str(matching_item.id), (
            f"Query-matching item should rank above unrelated item when q is provided "
            f"(semantic/full-text distance is primary), but got {items[0]['title']} first"
        )


# ---------------------------------------------------------------------------
# Source filter tests (Phase 34, RANK-05)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_source_filter(client, api_key_header, session, source_factory):
    """RANK-05: source parameter filters feed results to a specific source ID."""
    source_a = await source_factory(id="test:feed-src-a", name="Feed Source A")
    source_b = await source_factory(id="test:feed-src-b", name="Feed Source B")

    now = datetime.now(timezone.utc)

    item_a = IntelItem(
        id=uuid.uuid4(),
        source_id=source_a.id,
        external_id="ext-feed-src-a1",
        url=f"https://example.com/feed-src-a1-{uuid.uuid4()}",
        title="Feed Source A Item",
        content="Content from source A for feed source filter test. This provides enough text to pass minimum content length quality gates.",
        primary_type="tool",
        tags=["source-filter-test"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )
    item_b = IntelItem(
        id=uuid.uuid4(),
        source_id=source_b.id,
        external_id="ext-feed-src-b1",
        url=f"https://example.com/feed-src-b1-{uuid.uuid4()}",
        title="Feed Source B Item",
        content="Content from source B for feed source filter test. This provides enough text to pass minimum content length quality gates.",
        primary_type="tool",
        tags=["source-filter-test"],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )

    session.add(item_a)
    session.add(item_b)
    await session.commit()

    # Filter by source A
    response = await client.get(
        f"/v1/feed?tag=source-filter-test&source={source_a.id}&days=7",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data["items"]}

    assert str(item_a.id) in returned_ids, "Source A item should be in filtered feed"
    assert (
        str(item_b.id) not in returned_ids
    ), "Source B item should be excluded from feed"


# ---------------------------------------------------------------------------
# Source-type diversity cap tests (Phase 34, RANK-03)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_source_type_diversity(
    client, api_key_header, session, source_factory
):
    """RANK-03: Feed caps items per source_type so one type doesn't dominate."""
    # Create 3 bluesky sources and 1 rss source
    bs_source1 = await source_factory(
        id="test:diversity-bs1", name="Bluesky 1", type="bluesky"
    )
    bs_source2 = await source_factory(
        id="test:diversity-bs2", name="Bluesky 2", type="bluesky"
    )
    bs_source3 = await source_factory(
        id="test:diversity-bs3", name="Bluesky 3", type="bluesky"
    )
    rss_source = await source_factory(
        id="test:diversity-rss", name="RSS Feed", type="rss"
    )

    now = datetime.now(timezone.utc)
    all_items = []

    # Insert 8 bluesky items across the 3 sources (exceeds MAX_PER_SOURCE_TYPE=5)
    for i in range(8):
        bs_src = [bs_source1, bs_source2, bs_source3][i % 3]
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=bs_src.id,
            external_id=f"ext-diversity-bs-{i}",
            url=f"https://example.com/diversity-bs-{i}-{uuid.uuid4()}",
            title=f"Bluesky Item {i}",
            content="Bluesky post content for diversity cap test. Enough text to pass minimum content length quality gates for proper indexing.",
            primary_type="update",
            tags=["diversity-cap-test"],
            status="processed",
            relevance_score=0.8,
            quality_score=0.8,
            confidence_score=0.9,
            created_at=now - timedelta(minutes=i),
        )
        session.add(item)
        all_items.append(("bluesky", item))

    # Insert 3 rss items
    for i in range(3):
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=rss_source.id,
            external_id=f"ext-diversity-rss-{i}",
            url=f"https://example.com/diversity-rss-{i}-{uuid.uuid4()}",
            title=f"RSS Item {i}",
            content="RSS content for diversity cap test. Enough text to pass minimum content length quality gates for proper indexing.",
            primary_type="update",
            tags=["diversity-cap-test"],
            status="processed",
            relevance_score=0.8,
            quality_score=0.8,
            confidence_score=0.9,
            created_at=now - timedelta(minutes=i),
        )
        session.add(item)
        all_items.append(("rss", item))

    await session.commit()

    response = await client.get(
        "/v1/feed?tag=diversity-cap-test&days=7&limit=20",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]

    # Count bluesky items in results
    bluesky_item_ids = {str(it.id) for stype, it in all_items if stype == "bluesky"}
    rss_item_ids = {str(it.id) for stype, it in all_items if stype == "rss"}

    returned_bluesky = [item for item in items if item["id"] in bluesky_item_ids]
    returned_rss = [item for item in items if item["id"] in rss_item_ids]

    # Diversity cap: at most 5 bluesky items
    assert len(returned_bluesky) <= 5, (
        f"Expected at most 5 bluesky items (MAX_PER_SOURCE_TYPE), "
        f"got {len(returned_bluesky)}"
    )

    # RSS items should be present
    assert len(returned_rss) >= 1, "RSS items should appear in diversified feed"


# ---------------------------------------------------------------------------
# Comma-separated significance filter (Phase 34 RANK-01)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_comma_separated_significance(
    client, api_key_header, session, source_factory
):
    """Phase 34 RANK-01: significance=breaking,major returns both breaking and major items."""
    source = await source_factory(id="test:comma-sig", name="Comma Significance Source")

    now = datetime.now(timezone.utc)

    breaking_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-comma-breaking",
        url=f"https://example.com/comma-breaking-{uuid.uuid4()}",
        title="Breaking API Change",
        content="A breaking change that should appear with comma-separated filter.",
        primary_type="update",
        tags=["comma-sig-test"],
        status="processed",
        significance="breaking",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )

    major_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-comma-major",
        url=f"https://example.com/comma-major-{uuid.uuid4()}",
        title="Major SDK Release",
        content="A major release that should appear with comma-separated filter.",
        primary_type="update",
        tags=["comma-sig-test"],
        status="processed",
        significance="major",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )

    minor_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-comma-minor",
        url=f"https://example.com/comma-minor-{uuid.uuid4()}",
        title="Minor Patch Update",
        content="A minor update that should NOT appear with breaking,major filter.",
        primary_type="update",
        tags=["comma-sig-test"],
        status="processed",
        significance="minor",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )

    session.add_all([breaking_item, major_item, minor_item])
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=comma-sig-test&significance=breaking,major&days=7",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]

    item_ids = {item["id"] for item in items}
    assert str(breaking_item.id) in item_ids, "Breaking item should appear"
    assert str(major_item.id) in item_ids, "Major item should appear"
    assert str(minor_item.id) not in item_ids, "Minor item should NOT appear"


@pytest.mark.asyncio
async def test_feed_single_significance_still_works(
    client, api_key_header, session, source_factory
):
    """Phase 34: Single significance value (no comma) still works as before."""
    source = await source_factory(
        id="test:single-sig", name="Single Significance Source"
    )

    now = datetime.now(timezone.utc)

    breaking_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-single-breaking",
        url=f"https://example.com/single-breaking-{uuid.uuid4()}",
        title="Single Sig Breaking Change",
        content="Breaking item for single-value test.",
        primary_type="update",
        tags=["single-sig-test"],
        status="processed",
        significance="breaking",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )

    major_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-single-major",
        url=f"https://example.com/single-major-{uuid.uuid4()}",
        title="Single Sig Major Update",
        content="Major item that should NOT appear with significance=breaking.",
        primary_type="update",
        tags=["single-sig-test"],
        status="processed",
        significance="major",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=now,
    )

    session.add_all([breaking_item, major_item])
    await session.commit()

    response = await client.get(
        "/v1/feed?tag=single-sig-test&significance=breaking&days=7",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    items = data["items"]

    item_ids = {item["id"] for item in items}
    assert str(breaking_item.id) in item_ids, "Breaking item should appear"
    assert (
        str(major_item.id) not in item_ids
    ), "Major item should NOT appear with single significance filter"


@pytest.mark.asyncio
async def test_feed_invalid_comma_significance_rejected(client, api_key_header):
    """Phase 34: Invalid value in comma-separated significance returns 400."""
    response = await client.get(
        "/v1/feed?significance=breaking,invalid_value&days=7",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 400
    data = response.json()
    assert "invalid_significance" in str(data)
