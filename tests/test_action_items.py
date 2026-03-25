"""Tests for GET /v1/action-items endpoint.

Action items returns top 3-5 breaking/major items from the last 7 days
that haven't been read, acted_on, or dismissed by this API key.
"""
import uuid
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.models.models import IntelItem, Source


@pytest_asyncio.fixture
async def action_items_source(session):
    """Create a test source for action items tests."""
    source = Source(
        id="test:action-items-source",
        name="Action Items Test Source",
        type="rss",
        url="https://example.com/action-items-feed.xml",
        is_active=True,
        poll_interval_seconds=3600,
        tier="tier1",
        config={},
    )
    session.add(source)
    await session.commit()
    return source


@pytest_asyncio.fixture
async def action_items_fixture(session, action_items_source):
    """
    Insert a mix of processed items:
    - 2 breaking items (recent, 7 days)
    - 2 major items (recent)
    - 1 minor item (should NOT appear in action items)
    - 1 old breaking item (>7 days old, should NOT appear)
    """
    now = datetime.now(timezone.utc)
    items = {}

    breaking1 = IntelItem(
        id=uuid.uuid4(),
        source_id=action_items_source.id,
        external_id="ext-breaking-1",
        url="https://example.com/breaking-1",
        title="Breaking Change 1",
        content="Critical breaking change requiring immediate attention",
        primary_type="update",
        tags=["mcp", "breaking"],
        significance="breaking",
        status="processed",
        relevance_score=0.95,
        quality_score=0.9,
        confidence_score=0.9,
        created_at=now - timedelta(hours=2),
    )
    session.add(breaking1)
    items["breaking1"] = breaking1

    breaking2 = IntelItem(
        id=uuid.uuid4(),
        source_id=action_items_source.id,
        external_id="ext-breaking-2",
        url="https://example.com/breaking-2",
        title="Breaking Change 2",
        content="Another critical breaking change",
        primary_type="update",
        tags=["claude", "api"],
        significance="breaking",
        status="processed",
        relevance_score=0.85,
        quality_score=0.85,
        confidence_score=0.9,
        created_at=now - timedelta(hours=12),
    )
    session.add(breaking2)
    items["breaking2"] = breaking2

    major1 = IntelItem(
        id=uuid.uuid4(),
        source_id=action_items_source.id,
        external_id="ext-major-1",
        url="https://example.com/major-1",
        title="Major Update 1",
        content="Important major update",
        primary_type="update",
        tags=["tool"],
        significance="major",
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.85,
        created_at=now - timedelta(days=2),
    )
    session.add(major1)
    items["major1"] = major1

    minor1 = IntelItem(
        id=uuid.uuid4(),
        source_id=action_items_source.id,
        external_id="ext-minor-1",
        url="https://example.com/minor-1",
        title="Minor Update",
        content="Small minor update",
        primary_type="update",
        tags=["docs"],
        significance="minor",
        status="processed",
        relevance_score=0.6,
        quality_score=0.7,
        confidence_score=0.8,
        created_at=now - timedelta(hours=6),
    )
    session.add(minor1)
    items["minor1"] = minor1

    old_breaking = IntelItem(
        id=uuid.uuid4(),
        source_id=action_items_source.id,
        external_id="ext-old-breaking",
        url="https://example.com/old-breaking",
        title="Old Breaking Change",
        content="Older breaking change outside 7-day window",
        primary_type="update",
        tags=["mcp"],
        significance="breaking",
        status="processed",
        relevance_score=0.9,
        quality_score=0.9,
        confidence_score=0.9,
        created_at=now - timedelta(days=10),
    )
    session.add(old_breaking)
    items["old_breaking"] = old_breaking

    await session.commit()
    return items


@pytest.mark.asyncio
async def test_action_items_returns_breaking_and_major(
    client, api_key_header, action_items_fixture
):
    """GET /v1/action-items returns breaking and major items, not minor or old ones."""
    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert "action_items" in data
    assert "total" in data
    assert "message" in data

    returned_ids = {item["id"] for item in data["action_items"]}
    fixture = action_items_fixture

    # Breaking and major items should be present
    assert str(fixture["breaking1"].id) in returned_ids
    assert str(fixture["breaking2"].id) in returned_ids
    assert str(fixture["major1"].id) in returned_ids

    # Minor items and old items should NOT appear
    assert str(fixture["minor1"].id) not in returned_ids
    assert str(fixture["old_breaking"].id) not in returned_ids


@pytest.mark.asyncio
async def test_action_items_breaking_before_major(
    client, api_key_header, action_items_fixture
):
    """Breaking items come before major items in the response."""
    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    significances = [item["significance"] for item in data["action_items"]]
    breaking_indices = [i for i, s in enumerate(significances) if s == "breaking"]
    major_indices = [i for i, s in enumerate(significances) if s == "major"]

    if breaking_indices and major_indices:
        assert max(breaking_indices) < min(
            major_indices
        ), "All breaking items should appear before any major items"


@pytest.mark.asyncio
async def test_action_items_excludes_read_items(
    client, api_key_header, action_items_fixture, session
):
    """Items marked as 'read' by this API key are excluded from action items."""
    fixture = action_items_fixture
    api_key_id = api_key_header["api_key_id"]

    # Mark breaking1 as read
    await session.execute(
        text(
            """
            INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at)
            VALUES (gen_random_uuid(), CAST(:item_id AS uuid), :api_key_id, 'read', NOW(), NOW())
            """
        ),
        {"item_id": str(fixture["breaking1"].id), "api_key_id": api_key_id},
    )
    await session.commit()

    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data["action_items"]}
    assert str(fixture["breaking1"].id) not in returned_ids


@pytest.mark.asyncio
async def test_action_items_excludes_acted_on_items(
    client, api_key_header, action_items_fixture, session
):
    """Items marked as 'acted_on' by this API key are excluded from action items."""
    fixture = action_items_fixture
    api_key_id = api_key_header["api_key_id"]

    # Mark breaking2 as acted_on
    await session.execute(
        text(
            """
            INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at)
            VALUES (gen_random_uuid(), CAST(:item_id AS uuid), :api_key_id, 'acted_on', NOW(), NOW())
            """
        ),
        {"item_id": str(fixture["breaking2"].id), "api_key_id": api_key_id},
    )
    await session.commit()

    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data["action_items"]}
    assert str(fixture["breaking2"].id) not in returned_ids


@pytest.mark.asyncio
async def test_action_items_excludes_dismissed_items(
    client, api_key_header, action_items_fixture, session
):
    """Items marked as 'dismiss' by this API key are excluded from action items."""
    fixture = action_items_fixture
    api_key_id = api_key_header["api_key_id"]

    # Mark major1 as dismissed
    await session.execute(
        text(
            """
            INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at)
            VALUES (gen_random_uuid(), CAST(:item_id AS uuid), :api_key_id, 'dismiss', NOW(), NOW())
            """
        ),
        {"item_id": str(fixture["major1"].id), "api_key_id": api_key_id},
    )
    await session.commit()

    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    returned_ids = {item["id"] for item in data["action_items"]}
    assert str(fixture["major1"].id) not in returned_ids


@pytest.mark.asyncio
async def test_action_items_max_5(client, api_key_header, session, action_items_source):
    """Action items returns at most 5 items."""
    now = datetime.now(timezone.utc)
    # Insert 8 breaking items
    for i in range(8):
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=action_items_source.id,
            external_id=f"ext-max-test-{i}",
            url=f"https://example.com/max-test-{i}",
            title=f"Breaking Item {i}",
            content="Breaking content",
            primary_type="update",
            tags=["mcp"],
            significance="breaking",
            status="processed",
            relevance_score=0.9,
            quality_score=0.9,
            confidence_score=0.9,
            created_at=now - timedelta(hours=i),
        )
        session.add(item)
    await session.commit()

    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert len(data["action_items"]) <= 5


@pytest.mark.asyncio
async def test_action_items_message_when_caught_up(client, api_key_header, session):
    """Returns helpful message when there are no action items."""
    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert "caught up" in data["message"].lower()


@pytest.mark.asyncio
async def test_action_items_requires_auth(client):
    """Action items endpoint requires API key."""
    response = await client.get("/v1/action-items")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_action_items_with_profile_tag_filter(
    client, api_key_header, action_items_fixture, session
):
    """When user has a profile, only items matching their tech_stack/skills appear."""
    # Update user profile to only match "mcp" tagged items
    user_id = api_key_header["user_id"]
    await session.execute(
        text("UPDATE users SET profile = :profile WHERE id = :user_id"),
        {"profile": '{"tech_stack": ["mcp"], "skills": []}', "user_id": user_id},
    )
    await session.commit()

    response = await client.get("/v1/action-items", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    # Only items with "mcp" tag should appear
    fixture = action_items_fixture
    returned_ids = {item["id"] for item in data["action_items"]}

    # breaking1 has ["mcp", "breaking"] — should appear
    assert str(fixture["breaking1"].id) in returned_ids

    # breaking2 has ["claude", "api"] — should NOT appear with mcp-only profile
    assert str(fixture["breaking2"].id) not in returned_ids
