"""Tests for the cluster_items worker (UX-10).

The worker assigns cluster_id to processed items with similar embeddings.
Tests use the real test DB (pgvector is enabled by conftest.py engine fixture).

Mocking strategy:
- Patch src.core.init_db.async_session_factory with a test session factory
  (same pattern as ingest tests: make_session_factory)
- ctx dict simulates ARQ context: {"redis": redis_client}
  (redis_client fixture from conftest.py — flushed after each test)
"""
import math
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

import src.core.init_db as _db
from src.models.models import IntelItem
from src.workers.cluster_worker import cluster_items


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def make_embedding(seed: float = 0.1, dim: int = 1024) -> list:
    """Generate a normalized unit embedding vector for testing."""
    raw = [seed * (i % 10 + 1) for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_assigns_cluster_id_to_similar_items(
    session, source_factory, redis_client, monkeypatch
):
    """UX-10: Two processed items with identical embeddings get the same cluster_id."""
    source = await source_factory(id="test:cluster-source-1", name="Cluster Source")
    monkeypatch.setenv("CLUSTER_DISTANCE_THRESHOLD", "0.15")

    embedding = make_embedding(0.5)

    item_a = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-a",
        url="https://example.com/cluster-a",
        title="Cluster Item A",
        content="Content A",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=embedding,
    )

    item_b = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-b",
        url="https://example.com/cluster-b",
        title="Cluster Item B",
        content="Content B",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=embedding,  # identical embedding — distance == 0
    )

    session.add(item_a)
    session.add(item_b)
    await session.commit()

    with patch_session_factory(session):
        await cluster_items(ctx={"redis": redis_client})

    # Reload both items after raw SQL UPDATE
    result_a = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item_a.id)},
    )
    result_b = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item_b.id)},
    )

    cid_a = result_a.scalar_one()
    cid_b = result_b.scalar_one()

    assert cid_a is not None, "Item A should have a cluster_id after clustering"
    assert cid_b is not None, "Item B should have a cluster_id after clustering"
    assert cid_a == cid_b, "Items with identical embeddings should share a cluster_id"


@pytest.mark.asyncio
async def test_cluster_skips_already_assigned(session, source_factory, redis_client):
    """UX-10: Items with an existing cluster_id are not processed again."""
    source = await source_factory(id="test:cluster-source-2", name="Cluster Source 2")

    existing_cluster = str(uuid.uuid4())
    embedding = make_embedding(0.3)

    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-pre-assigned",
        url="https://example.com/cluster-pre-assigned",
        title="Pre-assigned Cluster Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=embedding,
        cluster_id=existing_cluster,
    )
    session.add(item)
    await session.commit()

    with patch_session_factory(session):
        await cluster_items(ctx={"redis": redis_client})

    # The pre-assigned cluster_id should remain unchanged
    result = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item.id)},
    )
    cid = result.scalar_one()
    assert cid == existing_cluster, "Pre-assigned cluster_id should not be changed"


@pytest.mark.asyncio
async def test_cluster_skips_null_embedding(session, source_factory, redis_client):
    """UX-10: Items with NULL embedding are skipped without error."""
    source = await source_factory(id="test:cluster-source-3", name="Cluster Source 3")

    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-no-embed",
        url="https://example.com/cluster-no-embed",
        title="No Embedding Item",
        content="Content without embedding",
        primary_type="skill",
        tags=[],
        status="processed",
        relevance_score=0.7,
        quality_score=0.7,
        confidence_score=0.8,
        created_at=datetime.now(timezone.utc),
        embedding=None,
    )
    session.add(item)
    await session.commit()

    # Should complete without error
    with patch_session_factory(session):
        await cluster_items(ctx={"redis": redis_client})

    result = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item.id)},
    )
    cid = result.scalar_one_or_none()
    assert cid is None, "Item with NULL embedding should remain cluster_id=NULL"


@pytest.mark.asyncio
async def test_cluster_singleton_stays_null(session, source_factory, redis_client):
    """UX-10: An item with no neighbors within threshold remains cluster_id=NULL."""
    source = await source_factory(id="test:cluster-source-4", name="Cluster Source 4")

    # Create two items with maximally different embeddings — one all-positive, one all-negative
    embedding_pos = [1.0 / math.sqrt(1024)] * 1024  # uniform positive unit vector
    embedding_neg = [-1.0 / math.sqrt(1024)] * 1024  # uniform negative unit vector

    item_pos = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-pos",
        url="https://example.com/cluster-pos",
        title="Positive Item",
        content="Content positive",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=embedding_pos,
    )

    item_neg = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-neg",
        url="https://example.com/cluster-neg",
        title="Negative Item",
        content="Content negative",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=embedding_neg,
    )

    session.add(item_pos)
    session.add(item_neg)
    await session.commit()

    with patch_session_factory(session):
        await cluster_items(ctx={"redis": redis_client})

    # Both have maximally dissimilar embeddings (cosine distance ~= 2.0 >> threshold 0.15)
    # Both should remain singletons (cluster_id = NULL)
    result_pos = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item_pos.id)},
    )
    result_neg = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item_neg.id)},
    )

    cid_pos = result_pos.scalar_one_or_none()
    cid_neg = result_neg.scalar_one_or_none()

    assert cid_pos is None, "Singleton item should remain cluster_id=NULL"
    assert cid_neg is None, "Singleton item should remain cluster_id=NULL"


@pytest.mark.asyncio
async def test_cluster_inherits_existing_cluster_id(
    session, source_factory, redis_client
):
    """UX-10: A new item similar to an already-clustered item inherits its cluster_id."""
    source = await source_factory(id="test:cluster-source-5", name="Cluster Source 5")

    existing_cluster = str(uuid.uuid4())
    embedding = make_embedding(0.7)

    # Item A already has a cluster_id
    item_a = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-inherit-a",
        url="https://example.com/cluster-inherit-a",
        title="Anchored Cluster Item",
        content="Content A",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.9,
        quality_score=0.9,
        confidence_score=0.95,
        created_at=datetime.now(timezone.utc),
        embedding=embedding,
        cluster_id=existing_cluster,
    )

    # Item B is new (no cluster_id), same embedding
    item_b = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-inherit-b",
        url="https://example.com/cluster-inherit-b",
        title="New Similar Item",
        content="Content B",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.85,
        quality_score=0.85,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=embedding,  # identical — distance == 0
    )

    session.add(item_a)
    session.add(item_b)
    await session.commit()

    with patch_session_factory(session):
        await cluster_items(ctx={"redis": redis_client})

    # Item B should inherit item A's existing cluster_id
    result_b = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item_b.id)},
    )
    cid_b = result_b.scalar_one()
    assert (
        cid_b == existing_cluster
    ), f"Item B should inherit cluster_id={existing_cluster}, got {cid_b}"


@pytest.mark.asyncio
async def test_cluster_lock_prevents_concurrent_run(
    session, source_factory, redis_client
):
    """Redis lock: if cluster:lock key already exists, cluster_items returns early."""
    source = await source_factory(id="test:cluster-source-6", name="Cluster Source 6")

    embedding = make_embedding(0.5)
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="ext-cluster-lock-test",
        url="https://example.com/cluster-lock-test",
        title="Lock Test Item",
        content="Content",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        quality_score=0.8,
        confidence_score=0.9,
        created_at=datetime.now(timezone.utc),
        embedding=embedding,
    )
    session.add(item)
    await session.commit()

    # Pre-acquire the lock — simulates a concurrent run holding the lock
    from src.workers.cluster_worker import CLUSTER_LOCK_KEY

    await redis_client.set(CLUSTER_LOCK_KEY, "1", nx=True, ex=120)

    with patch_session_factory(session):
        await cluster_items(ctx={"redis": redis_client})

    # Item should remain unassigned since the lock prevented processing
    result = await session.execute(
        text("SELECT cluster_id FROM intel_items WHERE id = CAST(:id AS uuid)"),
        {"id": str(item.id)},
    )
    cid = result.scalar_one_or_none()
    assert cid is None, "Item should remain unassigned when lock is held by another run"


# ---------------------------------------------------------------------------
# Context manager helper — patches _db.async_session_factory
# ---------------------------------------------------------------------------


class patch_session_factory:
    """Context manager that patches _db.async_session_factory with a test session."""

    def __init__(self, session):
        self._session = session
        self._orig = None

    def __enter__(self):
        self._orig = _db.async_session_factory

        @asynccontextmanager
        async def _factory():
            yield self._session

        _db.async_session_factory = _factory
        return self

    def __exit__(self, *args):
        _db.async_session_factory = self._orig
