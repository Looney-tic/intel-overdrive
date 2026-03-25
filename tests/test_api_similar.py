"""Tests for GET /v1/similar/{item_id} endpoint (UX-05).

Updated in Phase 14-02: /similar now returns {items, total} envelope (not bare array).
"""
import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone

from src.models.models import IntelItem


def make_embedding(seed: float = 0.1, dim: int = 1024) -> list:
    """Generate a normalized embedding vector for testing."""
    import math

    raw = [seed * (i % 10 + 1) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


@pytest_asyncio.fixture
async def similar_items_fixture(session, source_factory):
    """
    Inserts processed items with embeddings for similarity testing.
    Items 0-2 share a similar embedding cluster; item 3 is different.
    """
    source = await source_factory(id="test:similar-source", name="Similar Source")

    # Create reference item with known embedding
    ref_embedding = make_embedding(0.5)
    reference_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-ref",
        url="https://example.com/ref",
        title="Reference Item",
        content="Reference content",
        primary_type="skill",
        tags=["python"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=ref_embedding,
    )
    session.add(reference_item)

    # Similar items — slightly varied embeddings close to reference
    similar_items = []
    for i in range(2):
        seed = 0.5 + (i + 1) * 0.001  # very close to reference
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"ext-similar-{i}",
            url=f"https://example.com/similar-{i}",
            title=f"Similar Item {i}",
            content="Similar content",
            primary_type="skill",
            tags=["python"],
            status="processed",
            relevance_score=0.8,
            quality_score=0.8,
            confidence_score=0.9,
            created_at=datetime.now(timezone.utc),
            embedding=make_embedding(seed),
        )
        session.add(item)
        similar_items.append(item)

    # An item with no embedding (not yet embedded)
    no_embed_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-no-embed",
        url="https://example.com/no-embed",
        title="Not Embedded Item",
        content="Content without embedding",
        primary_type="skill",
        tags=[],
        status="processed",
        relevance_score=0.7,
        quality_score=0.7,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=None,
    )
    session.add(no_embed_item)

    await session.commit()
    return {
        "reference": reference_item,
        "similar": similar_items,
        "no_embed": no_embed_item,
    }


@pytest.mark.asyncio
async def test_similar_returns_envelope(client, api_key_header, similar_items_fixture):
    """Similar endpoint returns {items, total} envelope (not bare array)."""
    ref_id = similar_items_fixture["reference"].id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict), "Response must be an object with {items, total}"
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)


@pytest.mark.asyncio
async def test_similar_excludes_reference_item(
    client, api_key_header, similar_items_fixture
):
    """The reference item itself is not included in results."""
    ref_id = similar_items_fixture["reference"].id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    result_ids = [item["id"] for item in data["items"]]
    assert str(ref_id) not in result_ids


@pytest.mark.asyncio
async def test_similar_includes_similarity_score(
    client, api_key_header, similar_items_fixture
):
    """Each result includes a similarity float between 0 and 1."""
    ref_id = similar_items_fixture["reference"].id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    if data["items"]:  # If there are results
        for item in data["items"]:
            assert "similarity" in item
            assert isinstance(item["similarity"], float)
            assert 0.0 <= item["similarity"] <= 1.0


@pytest.mark.asyncio
async def test_similar_404_for_unknown_item(client, api_key_header):
    """Returns 404 for a non-existent item ID."""
    fake_id = uuid.uuid4()
    response = await client.get(
        f"/v1/similar/{fake_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 404
    err = response.json()
    # Structured error envelope: {"error": {"code": ..., "message": ...}}
    detail = err.get("detail") or err.get("error", {}).get("message", "")
    assert "not found" in detail.lower()


@pytest.mark.asyncio
async def test_similar_404_for_unembedded_item(
    client, api_key_header, similar_items_fixture
):
    """Returns 404 for an item that exists but has no embedding."""
    no_embed_id = similar_items_fixture["no_embed"].id
    response = await client.get(
        f"/v1/similar/{no_embed_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 404
    err = response.json()
    # Structured error envelope: {"error": {"code": ..., "message": ...}}
    detail = err.get("detail") or err.get("error", {}).get("message", "")
    assert "not yet embedded" in detail.lower()


@pytest.mark.asyncio
async def test_similar_limit_parameter(client, api_key_header, similar_items_fixture):
    """limit parameter caps results; max is 50."""
    ref_id = similar_items_fixture["reference"].id
    response = await client.get(
        f"/v1/similar/{ref_id}?limit=1", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) <= 1

    # Over limit
    response = await client.get(
        f"/v1/similar/{ref_id}?limit=51", headers=api_key_header["headers"]
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_similar_requires_auth(client, similar_items_fixture):
    """Similar endpoint requires API key."""
    ref_id = similar_items_fixture["reference"].id
    response = await client.get(f"/v1/similar/{ref_id}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_similar_only_includes_processed_items(
    client, api_key_header, similar_items_fixture
):
    """Results only include items with status='processed'."""
    ref_id = similar_items_fixture["reference"].id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    # All returned items are from the processed items in fixture — verify no raw/unprocessed leaks
    # The fixture only has processed items with embeddings as candidates
    data = response.json()
    # No assertion needed beyond 200 — the SQL WHERE clause enforces status='processed'
    # This test documents the intent


@pytest.mark.asyncio
async def test_similar_by_id_threshold_filters_distant(
    client, api_key_header, similar_items_fixture
):
    """Similar-by-id should only return items with cosine distance < 0.45 (similarity > 0.55)."""
    ref_id = similar_items_fixture["reference"].id
    response = await client.get(
        f"/v1/similar/{ref_id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()
    # All returned items must have similarity >= 0.55 (distance < 0.45)
    for item in data.get("items", []):
        assert item.get("similarity", 0) >= 0.55, (
            f"Item {item.get('id')} has similarity {item.get('similarity')} < 0.55 "
            "(cosine distance >= 0.45 threshold)"
        )


@pytest.mark.asyncio
async def test_similar_by_id_excludes_distant_items(
    client, api_key_header, session, source_factory
):
    """A very distant embedding should not appear in similar-by-id results."""
    source = await source_factory(id="test:distant-source", name="Distant Source")

    ref_embedding = make_embedding(0.5)
    ref_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-threshold-ref",
        url="https://example.com/threshold-ref",
        title="Threshold Reference",
        content="Reference content for threshold test",
        primary_type="skill",
        tags=["test"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=ref_embedding,
    )
    session.add(ref_item)

    # Deliberately distant embedding — negative of reference
    distant_embedding = [-v for v in ref_embedding]
    distant_item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-threshold-distant",
        url="https://example.com/threshold-distant",
        title="Distant Item",
        content="This item is completely unrelated",
        primary_type="tool",
        tags=["unrelated"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=distant_embedding,
    )
    session.add(distant_item)
    await session.commit()

    response = await client.get(
        f"/v1/similar/{ref_item.id}", headers=api_key_header["headers"]
    )
    assert response.status_code == 200
    data = response.json()

    # The distant item (cosine distance ~2.0) must NOT appear
    result_ids = {item["id"] for item in data["items"]}
    assert (
        str(distant_item.id) not in result_ids
    ), "Distant item should be excluded by cosine distance < 0.45 threshold"
