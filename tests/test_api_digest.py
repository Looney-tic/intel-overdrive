"""Tests for GET /v1/digest endpoint (UX-02)."""
import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone, timedelta
from src.models.models import IntelItem


@pytest_asyncio.fixture
async def digest_items_fixture(session, source_factory):
    """Inserts processed items across multiple primary_types for digest testing."""
    source = await source_factory(id="test:digest-source", name="Digest Source")

    items_data = [
        {"title": "Skill Alpha", "primary_type": "skill", "relevance_score": 0.9},
        {"title": "Skill Beta", "primary_type": "skill", "relevance_score": 0.7},
        {"title": "Tool One", "primary_type": "tool", "relevance_score": 0.85},
        {"title": "Tool Two", "primary_type": "tool", "relevance_score": 0.6},
        {"title": "Update One", "primary_type": "update", "relevance_score": 0.8},
    ]

    items = []
    for data in items_data:
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"ext-{uuid.uuid4()}",
            url=f"https://example.com/{uuid.uuid4()}",
            title=data["title"],
            content="Sample content",
            primary_type=data["primary_type"],
            tags=[],
            status="processed",
            relevance_score=data["relevance_score"],
            quality_score=0.8,
            confidence_score=0.9,
            created_at=datetime.now(timezone.utc),
        )
        session.add(item)
        items.append(item)

    # Old item outside the default 7-day window — should not appear
    old_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-old-digest",
        url="https://example.com/old-digest",
        title="Old Skill",
        content="Old content",
        primary_type="skill",
        tags=[],
        status="processed",
        relevance_score=0.9,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    session.add(old_item)

    await session.commit()
    return items


@pytest.mark.asyncio
async def test_digest_returns_grouped_by_primary_type(
    client, api_key_header, digest_items_fixture
):
    """Digest groups items by primary_type with correct structure."""
    response = await client.get("/v1/digest?days=7", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    assert "days" in data
    assert data["days"] == 7
    assert "groups" in data
    assert "total" in data

    # Expect 3 groups: skill, tool, update
    group_types = {g["primary_type"] for g in data["groups"]}
    assert "skill" in group_types
    assert "tool" in group_types
    assert "update" in group_types


@pytest.mark.asyncio
async def test_digest_items_sorted_by_relevance_within_group(
    client, api_key_header, digest_items_fixture
):
    """Items within each group are sorted by relevance_score DESC."""
    response = await client.get("/v1/digest?days=7", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    skill_group = next(g for g in data["groups"] if g["primary_type"] == "skill")
    scores = [item["relevance_score"] for item in skill_group["items"]]
    assert scores == sorted(
        scores, reverse=True
    ), "Items should be sorted by relevance DESC"


@pytest.mark.asyncio
async def test_digest_respects_days_window(
    client, api_key_header, digest_items_fixture
):
    """Items older than the days window are excluded."""
    # days=1 should show recent items but not the 30-day-old one
    response = await client.get("/v1/digest?days=1", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    all_titles = [item["title"] for g in data["groups"] for item in g["items"]]
    assert "Old Skill" not in all_titles


@pytest.mark.asyncio
async def test_digest_per_group_limit(client, api_key_header, digest_items_fixture):
    """per_group=1 limits to one item per group."""
    response = await client.get(
        "/v1/digest?days=7&per_group=1", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    for group in data["groups"]:
        assert len(group["items"]) <= 1, f"Group {group['primary_type']} has > 1 item"


@pytest.mark.asyncio
async def test_digest_empty_db(client, api_key_header):
    """Empty DB returns groups=[] and total=0."""
    response = await client.get("/v1/digest?days=7", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    assert data["groups"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_digest_requires_auth(client):
    """Digest endpoint requires API key."""
    response = await client.get("/v1/digest?days=7")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_digest_per_group_validation(
    client, api_key_header, digest_items_fixture
):
    """per_group max is 50; per_group min is 1."""
    # Over max — should return 422
    response = await client.get(
        "/v1/digest?per_group=51", headers=api_key_header["headers"]
    )
    assert response.status_code == 422

    # Under min — should return 422
    response = await client.get(
        "/v1/digest?per_group=0", headers=api_key_header["headers"]
    )
    assert response.status_code == 422
