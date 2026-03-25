"""Tests for library_worker: graduate_candidates, detect_stale_entries, synthesize_library_topics.

Mocking strategy:
- Patch src.core.init_db.async_session_factory via make_session_factory helper
- Mock LLMClient.classify with AsyncMock
- Mock SpendTracker.check_spend_gate to simulate spend gate
- Mock ctx dict with redis client (from fixture)
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _init_db
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.models import APIKey, IntelItem, ItemSignal, LibraryItem, Source, User
from src.services.auth_service import AuthService
from src.services.llm_client import LLMResponse
from src.services.spend_tracker import SpendLimitExceeded
from src.workers.library_worker import (
    GRADUATION_MIN_AGE_DAYS,
    GRADUATION_SCORE_THRESHOLD,
    FAST_TRACK_RELEVANCE,
    STALENESS_DAYS,
    ARCHIVE_DAYS,
    detect_stale_entries,
    graduate_candidates,
    synthesize_library_topics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return an async_session_factory-compatible callable that yields `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


async def _create_api_key(session) -> int:
    """Create a real User + APIKey row and return the api_key.id (integer PK)."""
    auth = AuthService()
    _, key_hash = auth.generate_api_key()
    user = User(
        email=f"worker-test-{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
        profile={},
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
    return api_key.id


async def _create_source(session, tier: str = "tier1", type_: str = "rss") -> Source:
    src = Source(
        id=f"test:{uuid.uuid4().hex[:12]}",
        name=f"Test {tier} Source",
        type=type_,
        url="https://example.com/feed.xml",
        tier=tier,
        config={},
    )
    session.add(src)
    await session.commit()
    return src


async def _create_intel_item(
    session,
    source_id: str,
    title: str = "MCP Best Practice Item",
    primary_type: str = "practice",
    tags: list | None = None,
    status: str = "processed",
    relevance_score: float = 0.9,
    quality_score: float = 0.8,
    age_days: int = 10,
) -> IntelItem:
    """Create a processed IntelItem old enough for graduation consideration."""
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=str(uuid.uuid4()),
        url=f"https://example.com/{uuid.uuid4().hex}",
        title=title,
        content="Some detailed content about the practice",
        summary="A summary of the best practice",
        primary_type=primary_type,
        tags=tags or ["mcp"],
        status=status,
        relevance_score=relevance_score,
        quality_score=quality_score,
        confidence_score=0.8,
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    session.add(item)
    await session.commit()
    return item


async def _create_library_item(
    session,
    slug: str = "mcp-worker-test",
    status: str = "active",
    graduation_method: str = "synthesis",
    graduated_at_offset_days: int = 0,
    last_confirmed_offset_days: int = 0,
    updated_at_offset_days: int = 0,
    source_item_count: int = 5,
    source_item_ids: list | None = None,
    human_reviewed: bool = False,
    is_current: bool = True,
    version: int = 1,
) -> LibraryItem:
    now = datetime.now(timezone.utc)
    item = LibraryItem(
        id=uuid.uuid4(),
        slug=slug,
        title=slug.replace("-", " ").title(),
        body="Body text for " + slug,
        key_points=["Point 1", "Point 2"],
        gotchas=[],
        topic_path="mcp",
        tags=["mcp"],
        status=status,
        is_current=is_current,
        version=version,
        graduation_method=graduation_method,
        graduation_score=15.0,
        graduated_at=now - timedelta(days=graduated_at_offset_days),
        last_confirmed_at=(
            now - timedelta(days=last_confirmed_offset_days)
            if last_confirmed_offset_days
            else None
        ),
        source_item_count=source_item_count,
        source_item_ids=source_item_ids or [],
        confidence="medium",
        human_reviewed=human_reviewed,
        content_hash=f"hash-{slug}",
        agent_hint="Inject tldr + key_points into system prompt.",
    )
    # Override updated_at via raw SQL after commit
    session.add(item)
    await session.commit()

    if updated_at_offset_days:
        await session.execute(
            text("UPDATE library_items SET updated_at = :ts WHERE id = :id"),
            {
                "ts": now - timedelta(days=updated_at_offset_days),
                "id": str(item.id),
            },
        )
        await session.commit()

    return item


# ---------------------------------------------------------------------------
# graduate_candidates tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_creates_library_items(session, redis_client):
    """synthesize_library_topics creates LibraryItem with status='active', method='synthesis'."""
    source = await _create_source(session, tier="tier1")

    # Create 10 processed items with tag 'mcp' for synthesis
    for i in range(10):
        await _create_intel_item(
            session,
            source.id,
            title=f"MCP Guide {i}",
            primary_type="practice",
            tags=["mcp"],
            age_days=30,
        )

    fake_response = LLMResponse(
        primary_type="mcp",
        tags=["mcp"],
        confidence=0.9,
        raw_text=json.dumps(
            {
                "tldr": "Configure MCP servers with strict input validation.",
                "body": "When building MCP servers always validate inputs at the boundary.",
                "key_points": ["Validate inputs", "Use least-privilege auth"],
                "gotchas": [
                    {"title": "No validation", "detail": "Leads to injection."}
                ],
            }
        ),
        input_tokens=200,
        output_tokens=150,
        cost=0.001,
        summary="MCP guide summary",
    )

    # classify_batch returns dict keyed by custom_id (slug) -> LLMResponse
    fake_batch_results = {"mcp": fake_response}

    ctx = {"redis": redis_client}
    factory = make_session_factory(session)

    with patch.object(_init_db, "async_session_factory", factory):
        with patch(
            "src.workers.library_worker.LLMClient.classify_batch",
            new=AsyncMock(return_value=fake_batch_results),
        ):
            with patch(
                "src.workers.library_worker.SpendTracker.check_spend_gate",
                new=AsyncMock(return_value=None),
            ):
                await synthesize_library_topics(ctx)

    # Verify at least one LibraryItem was created with synthesis method
    result = await session.execute(
        select(LibraryItem)
        .where(
            LibraryItem.graduation_method == "synthesis",
            LibraryItem.is_current.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    items = result.scalars().all()
    assert len(items) >= 1
    item = items[0]
    assert item.status == "active"
    assert item.graduation_method == "synthesis"
    # Verify slug is topic-specific (not a generic primary_type like "tool" or "practice")
    assert item.slug == "mcp"


@pytest.mark.asyncio
async def test_synthesize_skips_insufficient_growth(session, redis_client):
    """Second synthesis run skips re-synthesis when item count growth <= 20% threshold.

    When an existing library entry's source_item_count matches the current item count,
    no growth is detected and the topic is skipped without calling the LLM.
    """
    source = await _create_source(session, tier="tier1")
    for i in range(10):
        await _create_intel_item(
            session,
            source.id,
            title=f"Agents Practice {i}",
            primary_type="practice",
            tags=["agents"],
            age_days=30,
        )

    fake_response = LLMResponse(
        primary_type="agents",
        tags=["agents"],
        confidence=0.9,
        raw_text=json.dumps(
            {
                "tldr": "Use explicit handoff protocols in agent systems.",
                "body": "Multi-agent systems need explicit coordination.",
                "key_points": ["Handoff protocols", "Shared state"],
                "gotchas": [],
            }
        ),
        input_tokens=200,
        output_tokens=100,
        cost=0.001,
        summary="Agents guide",
    )

    # classify_batch returns dict keyed by custom_id (slug) -> LLMResponse
    fake_batch_results = {"agents": fake_response}

    ctx = {"redis": redis_client}
    factory = make_session_factory(session)
    classify_batch_mock = AsyncMock(return_value=fake_batch_results)

    with patch.object(_init_db, "async_session_factory", factory):
        with patch(
            "src.workers.library_worker.LLMClient.classify_batch",
            new=classify_batch_mock,
        ):
            with patch(
                "src.workers.library_worker.SpendTracker.check_spend_gate",
                new=AsyncMock(return_value=None),
            ):
                # First run: creates version 1
                await synthesize_library_topics(ctx)

    # Verify version 1 was created
    result = await session.execute(
        select(LibraryItem)
        .where(LibraryItem.graduation_method == "synthesis")
        .execution_options(populate_existing=True)
    )
    items_v1 = result.scalars().all()
    assert len(items_v1) == 1
    assert items_v1[0].version == 1
    assert items_v1[0].is_current is True

    # Reset mock call count
    classify_batch_mock.reset_mock()

    # Second run WITHOUT adding more items: growth = 0% <= 20% threshold → skip
    with patch.object(_init_db, "async_session_factory", factory):
        with patch(
            "src.workers.library_worker.LLMClient.classify_batch",
            new=classify_batch_mock,
        ):
            with patch(
                "src.workers.library_worker.SpendTracker.check_spend_gate",
                new=AsyncMock(return_value=None),
            ):
                await synthesize_library_topics(ctx)

    # LLM should NOT have been called on the second run (growth below threshold)
    classify_batch_mock.assert_not_called()

    # Still only version 1 in the DB
    result2 = await session.execute(
        select(LibraryItem)
        .where(LibraryItem.graduation_method == "synthesis")
        .execution_options(populate_existing=True)
    )
    assert len(result2.scalars().all()) == 1


@pytest.mark.asyncio
async def test_synthesize_spend_gate(session, redis_client):
    """synthesize_library_topics skips gracefully when SpendLimitExceeded is raised."""
    source = await _create_source(session, tier="tier1")
    for i in range(5):
        await _create_intel_item(
            session,
            source.id,
            title=f"Gate Test {i}",
            primary_type="tool",
            tags=["tool"],
            age_days=30,
        )

    ctx = {"redis": redis_client}
    factory = make_session_factory(session)

    with patch.object(_init_db, "async_session_factory", factory):
        with patch(
            "src.workers.library_worker.SpendTracker.check_spend_gate",
            new=AsyncMock(side_effect=SpendLimitExceeded(current=10.0, limit=10.0)),
        ):
            # Should not raise, just stop gracefully
            await synthesize_library_topics(ctx)

    # No new library items should be created (spend gate blocked synthesis)
    result = await session.execute(
        select(LibraryItem).execution_options(populate_existing=True)
    )
    items = result.scalars().all()
    assert len(items) == 0


@pytest.mark.asyncio
async def test_synthesize_skips_curated(session, redis_client):
    """synthesize_library_topics skips topics already curated (human_reviewed=True)."""
    source = await _create_source(session, tier="tier1")
    for i in range(5):
        await _create_intel_item(
            session,
            source.id,
            title=f"Curated Item {i}",
            primary_type="mcp",
            tags=["mcp"],
            age_days=30,
        )

    # Create existing curated library item with synthesis method + human_reviewed=True
    existing = LibraryItem(
        id=uuid.uuid4(),
        slug="mcp",
        title="MCP",
        body="Curated body",
        key_points=["Curated point"],
        gotchas=[],
        topic_path="mcp",
        tags=["mcp"],
        status="active",
        is_current=True,
        graduation_method="synthesis",
        graduation_score=5.0,
        source_item_count=5,  # matches item count → no growth → skip
        source_item_ids=[],
        confidence="high",
        human_reviewed=True,
        content_hash="hash-mcp",
        agent_hint="hint",
    )
    session.add(existing)
    await session.commit()

    classify_batch_mock = AsyncMock(return_value={})
    ctx = {"redis": redis_client}
    factory = make_session_factory(session)

    with patch.object(_init_db, "async_session_factory", factory):
        with patch(
            "src.workers.library_worker.LLMClient.classify_batch",
            new=classify_batch_mock,
        ):
            with patch(
                "src.workers.library_worker.SpendTracker.check_spend_gate",
                new=AsyncMock(return_value=None),
            ):
                await synthesize_library_topics(ctx)

    # classify_batch should NOT be called because growth threshold not met (existing.source_item_count == item_count)
    classify_batch_mock.assert_not_called()


@pytest.mark.asyncio
async def test_graduate_signal_based(session):
    """Items with enough upvotes + age >= 7 days become library candidates.

    Uses relevance_score=0.5 (below FAST_TRACK_RELEVANCE=0.85) to ensure
    the item only qualifies via signal score, not fast-track, so graduation_method='signal'.
    """
    source = await _create_source(session, tier="tier1")
    item = await _create_intel_item(
        session,
        source.id,
        primary_type="practice",
        tags=["mcp"],
        relevance_score=0.5,  # below FAST_TRACK_RELEVANCE=0.85 → signal path only
        age_days=GRADUATION_MIN_AGE_DAYS + 1,
    )

    # Create a real api_key row to satisfy the FK constraint
    api_key_id = await _create_api_key(session)

    # Add enough upvotes to exceed GRADUATION_SCORE_THRESHOLD (15.0)
    # Score = upvotes*3 * tier_mult(1.5) * type_mult(1.4)
    # Need: upvotes*3 * 1.5 * 1.4 >= 15 -> upvotes >= 2.38 -> 3 upvotes gives 18.9
    # The unique constraint is (item_id, api_key_id), so we need separate api_keys
    # for multiple signals. Just use one key for one upvote and test the signal path.
    await session.execute(
        text(
            "INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at) "
            "VALUES (:id, :item_id, :key_id, 'upvote', NOW(), NOW())"
        ),
        {"id": str(uuid.uuid4()), "item_id": str(item.id), "key_id": api_key_id},
    )
    # Add more upvote rows from different "users" — create extra api_keys
    for _ in range(3):
        extra_key_id = await _create_api_key(session)
        await session.execute(
            text(
                "INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at) "
                "VALUES (:id, :item_id, :key_id, 'upvote', NOW(), NOW())"
            ),
            {"id": str(uuid.uuid4()), "item_id": str(item.id), "key_id": extra_key_id},
        )
    await session.commit()

    factory = make_session_factory(session)
    ctx: dict = {}

    with patch.object(_init_db, "async_session_factory", factory):
        await graduate_candidates(ctx)

    # Check that a LibraryItem candidate was created
    result = await session.execute(
        select(LibraryItem).execution_options(populate_existing=True)
    )
    lib_items = result.scalars().all()
    assert len(lib_items) >= 1
    candidate = lib_items[0]
    assert candidate.graduation_method == "signal"


@pytest.mark.asyncio
async def test_graduate_source_type_fasttrack(session):
    """tier1 practice item with relevance >= 0.85 is fast-tracked to candidate."""
    source = await _create_source(session, tier="tier1")
    item = await _create_intel_item(
        session,
        source.id,
        primary_type="practice",
        tags=["mcp"],
        relevance_score=FAST_TRACK_RELEVANCE,
        age_days=GRADUATION_MIN_AGE_DAYS + 1,
    )

    factory = make_session_factory(session)
    ctx: dict = {}

    with patch.object(_init_db, "async_session_factory", factory):
        await graduate_candidates(ctx)

    result = await session.execute(
        select(LibraryItem).execution_options(populate_existing=True)
    )
    lib_items = result.scalars().all()
    assert len(lib_items) >= 1
    assert lib_items[0].graduation_method == "source_type"


@pytest.mark.asyncio
async def test_graduate_prevents_duplicates(session):
    """Items already linked via source_item_ids are not re-graduated."""
    source = await _create_source(session, tier="tier1")
    item = await _create_intel_item(
        session,
        source.id,
        primary_type="practice",
        relevance_score=FAST_TRACK_RELEVANCE,
        age_days=GRADUATION_MIN_AGE_DAYS + 1,
    )

    # Pre-create a LibraryItem that already references this item
    existing = LibraryItem(
        id=uuid.uuid4(),
        slug="existing-mcp-item",
        title="Existing MCP Item",
        body="Body",
        key_points=[],
        gotchas=[],
        topic_path="mcp",
        tags=["mcp"],
        status="active",
        is_current=True,
        graduation_method="signal",
        graduation_score=15.0,
        source_item_ids=[str(item.id)],  # already linked
        source_item_count=1,
        source_count=1,
        confidence="low",
        content_hash="existing-hash",
        agent_hint="hint",
    )
    session.add(existing)
    await session.commit()

    factory = make_session_factory(session)
    ctx: dict = {}

    with patch.object(_init_db, "async_session_factory", factory):
        await graduate_candidates(ctx)

    # Should still only have the one pre-existing item
    result = await session.execute(
        select(LibraryItem).execution_options(populate_existing=True)
    )
    lib_items = result.scalars().all()
    assert len(lib_items) == 1
    assert lib_items[0].slug == "existing-mcp-item"


# ---------------------------------------------------------------------------
# detect_stale_entries tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_staleness_time_decay(session):
    """Active item without last_confirmed_at after 180+ days -> review_needed."""
    await _create_library_item(
        session,
        slug="stale-item-180",
        status="active",
        graduated_at_offset_days=STALENESS_DAYS + 1,
        last_confirmed_offset_days=0,  # None (no confirmation)
    )

    factory = make_session_factory(session)
    ctx: dict = {}

    with patch.object(_init_db, "async_session_factory", factory):
        await detect_stale_entries(ctx)

    result = await session.execute(
        select(LibraryItem)
        .where(LibraryItem.slug == "stale-item-180")
        .execution_options(populate_existing=True)
    )
    item = result.scalar_one()
    assert item.status == "review_needed"


@pytest.mark.asyncio
async def test_staleness_archive_after_30_days(session):
    """Item in review_needed for 35+ days with no action -> archived."""
    item = await _create_library_item(
        session,
        slug="review-needed-item",
        status="review_needed",
        graduated_at_offset_days=220,
        updated_at_offset_days=ARCHIVE_DAYS + 5,  # stuck for 35 days
    )

    factory = make_session_factory(session)
    ctx: dict = {}

    with patch.object(_init_db, "async_session_factory", factory):
        await detect_stale_entries(ctx)

    result = await session.execute(
        select(LibraryItem)
        .where(LibraryItem.slug == "review-needed-item")
        .execution_options(populate_existing=True)
    )
    archived_item = result.scalar_one()
    assert archived_item.status == "archived"


@pytest.mark.asyncio
async def test_staleness_signal_confirms(session):
    """LibraryItem with fresh upvote on source_item gets last_confirmed_at bumped, stays active."""
    source = await _create_source(session, tier="tier1")
    source_item = await _create_intel_item(
        session,
        source.id,
        title="Signal Confirm Test",
        age_days=GRADUATION_MIN_AGE_DAYS + 1,
    )

    # Create active library item that references this intel item
    # graduated_at is old enough to trigger staleness, but we'll have a fresh signal
    lib_item = await _create_library_item(
        session,
        slug="signal-confirmed-item",
        status="active",
        graduated_at_offset_days=STALENESS_DAYS + 1,
        source_item_ids=[str(source_item.id)],
    )

    # Add a fresh upvote signal on the source item (need valid FK for api_key_id)
    api_key_id = await _create_api_key(session)
    await session.execute(
        text(
            "INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at) "
            "VALUES (:id, :item_id, :key_id, 'upvote', NOW(), NOW())"
        ),
        {"id": str(uuid.uuid4()), "item_id": str(source_item.id), "key_id": api_key_id},
    )
    await session.commit()

    factory = make_session_factory(session)
    ctx: dict = {}

    with patch.object(_init_db, "async_session_factory", factory):
        await detect_stale_entries(ctx)

    result = await session.execute(
        select(LibraryItem)
        .where(LibraryItem.slug == "signal-confirmed-item")
        .execution_options(populate_existing=True)
    )
    confirmed_item = result.scalar_one()
    # Signal confirmation should have bumped last_confirmed_at → item remains active
    assert confirmed_item.last_confirmed_at is not None
    assert confirmed_item.status == "active"
