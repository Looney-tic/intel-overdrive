"""
Pipeline worker integration tests.

Tests for embed_items, gate_relevance, and classify_items workers covering:
- Status transitions (raw->embedded->queued|filtered->processing->processed|failed)
- Spend gate enforcement (SpendLimitExceeded blocks execution gracefully)
- Filtered items never reach LLM classification
- Full pipeline (raw->processed) integration test
- Stuck processing item recovery in classify_items

Mocking strategy:
- Patch src.core.init_db.async_session_factory with the test session factory
- Patch LLMClient.get_embeddings and LLMClient.classify with AsyncMock
- Patch SpendTracker.check_spend_gate to simulate spend limit scenarios
"""
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import src.core.init_db as _init_db
from sqlalchemy import select, text

from src.models.models import IntelItem, ReferenceItem, Source
from src.services.llm_client import LLMResponse
from src.workers.pipeline_workers import classify_items, embed_items, gate_relevance
from src.workers.settings import SlowWorkerSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


def _make_unit_vector(dim_index: int, dims: int = 1024) -> list[float]:
    """Return a 1024-dim unit vector with 1.0 at dim_index, 0.0 elsewhere."""
    vec = [0.0] * dims
    vec[dim_index] = 1.0
    return vec


async def _create_source(session, tier: str = "tier1") -> Source:
    source = Source(
        id=f"test:{uuid.uuid4().hex[:12]}",
        name="Test Source",
        type="rss",
        url="https://example.com/feed.xml",
        tier=tier,
        config={},
    )
    session.add(source)
    await session.commit()
    return source


async def _create_intel_item(
    session,
    source_id: str,
    status: str = "raw",
    embedding: list[float] | None = None,
    title: str = "Test Item",
    content: str = "Test content about MCP server skills",
    excerpt: str | None = None,
) -> IntelItem:
    item = IntelItem(
        source_id=source_id,
        external_id=str(uuid.uuid4()),
        url=f"https://example.com/{uuid.uuid4().hex}",
        title=title,
        content=content,
        excerpt=excerpt,
        primary_type="skill",
        status=status,
        embedding=embedding,
    )
    session.add(item)
    await session.commit()
    return item


async def _create_reference_item(
    session,
    is_positive: bool,
    embedding: list[float] | None = None,
) -> ReferenceItem:
    ref = ReferenceItem(
        url=f"https://ref.example.com/{uuid.uuid4().hex}",
        title="Reference Item",
        description="Reference for gate calibration",
        is_positive=is_positive,
        embedding=embedding,
        label="positive" if is_positive else "negative",
    )
    session.add(ref)
    await session.commit()
    return ref


async def _reload_item(session, item_id) -> IntelItem:
    """Reload an item from the DB, bypassing ORM identity map cache."""
    result = await session.execute(
        select(IntelItem)
        .where(IntelItem.id == item_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# SlowWorkerSettings registration tests (no DB required)
# ---------------------------------------------------------------------------


def test_slow_queue_has_pipeline_workers():
    """SlowWorkerSettings must register all three pipeline workers."""
    function_names = [f.__name__ for f in SlowWorkerSettings.functions]
    assert "embed_items" in function_names
    assert "gate_relevance" in function_names
    assert "classify_items" in function_names


def test_slow_queue_has_cron_jobs():
    """SlowWorkerSettings must have cron jobs for pipeline + quality workers."""
    assert len(SlowWorkerSettings.cron_jobs) >= 3


# ---------------------------------------------------------------------------
# embed_items tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embed_items_transitions_raw_to_embedded(session, redis_client):
    """embed_items must transition status raw->embedded and store the embedding."""
    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="raw")

    mock_embedding = _make_unit_vector(0)

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.get_embeddings = AsyncMock(return_value=[mock_embedding])
            MockLLMClient.return_value = mock_client

            await embed_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "embedded"
    assert reloaded.embedding is not None
    assert reloaded.embedding_model_version is not None


@pytest.mark.asyncio
async def test_embed_items_idempotent_already_embedded(session, redis_client):
    """embed_items must not change items that are already embedded."""
    source = await _create_source(session)
    item = await _create_intel_item(
        session, source.id, status="embedded", embedding=_make_unit_vector(1)
    )

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.get_embeddings = AsyncMock(return_value=[_make_unit_vector(2)])
            MockLLMClient.return_value = mock_client

            await embed_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    # Item was already 'embedded' — not in 'raw' pool — should stay unchanged
    assert reloaded.status == "embedded"


@pytest.mark.asyncio
async def test_embed_items_spend_limit_blocked(session, redis_client):
    """embed_items must leave items as raw when spend limit is exceeded."""
    from src.services.spend_tracker import SpendLimitExceeded

    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="raw")

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.get_embeddings = AsyncMock(
                side_effect=SpendLimitExceeded(current=10.0, limit=10.0)
            )
            MockLLMClient.return_value = mock_client

            await embed_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    # SpendLimitExceeded: item must remain 'raw' for next run
    assert reloaded.status == "raw"


@pytest.mark.asyncio
async def test_embed_items_no_items_returns_early(session, redis_client):
    """embed_items returns early without error when no raw items exist."""
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.get_embeddings = AsyncMock()
            MockLLMClient.return_value = mock_client

            # Should not raise
            await embed_items({"redis": redis_client})

    mock_client.get_embeddings.assert_not_called()


# ---------------------------------------------------------------------------
# gate_relevance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_relevance_routes_to_queued(session, redis_client):
    """gate_relevance routes item to 'queued' when it's similar to positive references."""
    source = await _create_source(session, tier="tier1")
    embedding = _make_unit_vector(5)

    # Create positive reference with same embedding
    await _create_reference_item(session, is_positive=True, embedding=embedding)

    item = await _create_intel_item(
        session, source.id, status="embedded", embedding=embedding
    )

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        await gate_relevance({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "queued"
    assert reloaded.relevance_score > 0.0


@pytest.mark.asyncio
async def test_gate_relevance_routes_to_filtered(session, redis_client):
    """gate_relevance routes item to 'filtered' when it's similar to negative references."""
    source = await _create_source(session, tier="tier1")

    # Query vector at dim 6
    query_embedding = _make_unit_vector(6)

    # Only negative reference with same embedding
    await _create_reference_item(session, is_positive=False, embedding=query_embedding)

    item = await _create_intel_item(
        session, source.id, status="embedded", embedding=query_embedding
    )

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        # Use a high threshold so score=0.0 fails the gate
        with patch("src.workers.pipeline_workers.compute_gate_score") as mock_gate:
            mock_gate.return_value = (0.0, False)
            await gate_relevance({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "filtered"


@pytest.mark.asyncio
async def test_gate_relevance_empty_ref_set_passes_item(session, redis_client):
    """gate_relevance passes items through when reference set is empty."""
    source = await _create_source(session)
    embedding = _make_unit_vector(7)
    item = await _create_intel_item(
        session, source.id, status="embedded", embedding=embedding
    )

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        await gate_relevance({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    # Empty reference set returns (0.0, True) — item passes gate
    assert reloaded.status == "queued"


@pytest.mark.asyncio
async def test_gate_relevance_sets_relevance_score(session, redis_client):
    """gate_relevance sets relevance_score on all processed items."""
    source = await _create_source(session)
    embedding = _make_unit_vector(8)
    item = await _create_intel_item(
        session, source.id, status="embedded", embedding=embedding
    )

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        await gate_relevance({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    # relevance_score must be a positive float (authority + freshness components > 0)
    assert isinstance(reloaded.relevance_score, float)
    assert reloaded.relevance_score > 0.0


# ---------------------------------------------------------------------------
# classify_items tests
# ---------------------------------------------------------------------------


def _make_llm_response(
    primary_type: str = "skill",
    tags: list | None = None,
    confidence: float = 0.95,
) -> LLMResponse:
    tags = tags or ["mcp", "hooks"]
    raw = json.dumps(
        {"primary_type": primary_type, "tags": tags, "confidence": confidence}
    )
    return LLMResponse(
        primary_type=primary_type,
        tags=tags,
        confidence=confidence,
        raw_text=raw,
        input_tokens=100,
        output_tokens=50,
        cost=0.0001,
    )


@pytest.mark.asyncio
async def test_classify_items_transitions_to_processed(session, redis_client):
    """classify_items transitions queued->processing->processed with correct fields."""
    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="queued")

    mock_response = _make_llm_response(
        primary_type="skill", tags=["mcp"], confidence=0.95
    )

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                return_value={str(item.id): mock_response}
            )
            MockLLMClient.return_value = mock_client

            await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "processed"
    assert reloaded.primary_type == "skill"
    assert reloaded.tags == ["mcp"]
    assert reloaded.confidence_score == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_classify_items_maps_invalid_primary_type_via_fallback(
    session, redis_client
):
    """classify_items maps unknown primary_type via TYPE_FALLBACK_MAP instead of failing items.

    "unknown" maps to "docs" (see TYPE_FALLBACK_MAP). Items with any LLM-returned type
    not in VALID_PRIMARY_TYPES are recovered rather than permanently failed.
    """
    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="queued")

    # "unknown" is not in VALID_PRIMARY_TYPES but is in TYPE_FALLBACK_MAP -> "docs"
    mock_response = _make_llm_response(primary_type="unknown")

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                return_value={str(item.id): mock_response}
            )
            MockLLMClient.return_value = mock_client

            await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    # Item should be processed (not failed) and mapped to the fallback type
    assert reloaded.status == "processed"
    assert reloaded.primary_type == "docs"


@pytest.mark.asyncio
async def test_classify_items_spend_gate_blocks_before_processing(
    session, redis_client
):
    """classify_items returns early without transitioning items when spend gate blocks."""
    from src.services.spend_tracker import SpendLimitExceeded

    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="queued")

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.SpendTracker") as MockSpendTracker:
            mock_tracker = AsyncMock()
            mock_tracker.check_spend_gate = AsyncMock(
                side_effect=SpendLimitExceeded(current=10.0, limit=10.0)
            )
            MockSpendTracker.return_value = mock_tracker

            await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    # Spend gate blocked BEFORE any transition — item must stay 'queued'
    assert reloaded.status == "queued"


@pytest.mark.asyncio
async def test_filtered_items_never_classified(session, redis_client):
    """Filtered items must never reach LLM classification."""
    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="filtered")

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock()
            MockLLMClient.return_value = mock_client

            await classify_items({"redis": redis_client})

    # LLMClient.classify_batch must never have been called — no queued items
    mock_client.classify_batch.assert_not_called()

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "filtered"


@pytest.mark.asyncio
async def test_classify_items_llm_error_transitions_to_failed(session, redis_client):
    """classify_items transitions to failed when LLM raises an unexpected error."""
    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="queued")

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                side_effect=RuntimeError("API unavailable")
            )
            MockLLMClient.return_value = mock_client

            await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    # Batch error leaves items in 'processing' — recovery step handles next run
    assert reloaded.status == "processing"


@pytest.mark.asyncio
async def test_classify_items_fence_stripping(session, redis_client):
    """classify_items processes correctly when classify_batch returns parsed result (fences stripped internally)."""
    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="queued")

    # classify_batch strips fences internally and returns the correctly parsed result
    parsed_response = LLMResponse(
        primary_type="tool",
        tags=["cli"],
        confidence=0.85,
        raw_text='{"primary_type": "tool", "tags": ["cli"], "confidence": 0.85}',
        input_tokens=80,
        output_tokens=40,
        cost=0.00005,
    )

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                return_value={str(item.id): parsed_response}
            )
            MockLLMClient.return_value = mock_client

            await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "processed"
    assert reloaded.primary_type == "tool"
    assert reloaded.tags == ["cli"]


# ---------------------------------------------------------------------------
# is_noise tests (pure function, no DB/async needed)
# ---------------------------------------------------------------------------


def test_is_noise_filters_badge_urls():
    """is_noise() returns True for shield.io and badge.fury.io badge URLs.

    Badge URLs are scraped from READMEs and never contain useful content.
    Non-badge GitHub URLs must pass through (return False).
    """
    from src.workers.pipeline_workers import is_noise

    assert (
        is_noise("some title", "some content", "https://img.shields.io/badge/foo")
        is True
    )
    assert is_noise("some title", "some content", "https://badge.fury.io/test") is True
    assert (
        is_noise("some title", "some content", "https://github.com/real-project")
        is False
    )


# ---------------------------------------------------------------------------
# classify_items significance normalization test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_items_normalizes_breaking_change_significance(
    session, redis_client
):
    """classify_items normalizes significance='breaking-change' to 'breaking'.

    The LLM may return the legacy value 'breaking-change'; the worker must
    canonicalize it to 'breaking' before storing it in intel_items.significance.
    """
    source = await _create_source(session)
    item = await _create_intel_item(session, source.id, status="queued")

    # Build response with significance="breaking-change" (legacy LLM output)
    mock_response = _make_llm_response(primary_type="update")
    mock_response.significance = "breaking-change"

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                return_value={str(item.id): mock_response}
            )
            MockLLMClient.return_value = mock_client

            await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "processed"
    assert reloaded.significance == "breaking", (
        f"Expected 'breaking', got '{reloaded.significance}' — "
        "normalization of 'breaking-change' -> 'breaking' did not apply"
    )


# ---------------------------------------------------------------------------
# Full pipeline integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_machine_full_pipeline(session, redis_client):
    """Full pipeline: raw → embedded → queued → processed (mock LLM/gate)."""
    source = await _create_source(session, tier="tier1")
    item = await _create_intel_item(session, source.id, status="raw")

    mock_embedding = _make_unit_vector(100)
    mock_classify_response = _make_llm_response(
        primary_type="practice", tags=["workflow", "claude-code"], confidence=0.9
    )

    factory = make_session_factory(session)

    # Step 1: embed_items (raw -> embedded)
    with patch.object(_init_db, "async_session_factory", factory):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.get_embeddings = AsyncMock(return_value=[mock_embedding])
            MockLLMClient.return_value = mock_client
            await embed_items({"redis": redis_client})

    after_embed = await _reload_item(session, item.id)
    assert after_embed.status == "embedded"

    # Step 2: gate_relevance (embedded -> queued, empty ref set passes by default)
    with patch.object(_init_db, "async_session_factory", factory):
        await gate_relevance({"redis": redis_client})

    after_gate = await _reload_item(session, item.id)
    assert after_gate.status == "queued"
    assert after_gate.relevance_score > 0.0

    # Step 3: classify_items (queued -> processed)
    after_gate_item = await _reload_item(session, item.id)
    with patch.object(_init_db, "async_session_factory", factory):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                return_value={str(after_gate_item.id): mock_classify_response}
            )
            MockLLMClient.return_value = mock_client
            await classify_items({"redis": redis_client})

    final = await _reload_item(session, item.id)
    assert final.status == "processed"
    assert final.primary_type == "practice"
    assert "workflow" in final.tags
    assert final.confidence_score == pytest.approx(0.9)
    assert final.relevance_score > 0.0


# ---------------------------------------------------------------------------
# Inline quality scoring tests (P0: items must not have quality_score=0.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_items_inline_quality_scoring(session, redis_client):
    """classify_items must set a non-zero quality_score inline before commit.

    This is the key test for the P0 fix: items must never be visible with
    quality_score=0.0 after transitioning to status='processed'.
    """
    source = await _create_source(session, tier="tier1")
    item = await _create_intel_item(
        session,
        source.id,
        status="queued",
        title="MCP Server for Browser Automation",
        content="A powerful MCP server that lets Claude Code control a browser via Playwright.",
    )

    mock_response = _make_llm_response(
        primary_type="tool", tags=["mcp", "browser-automation"], confidence=0.92
    )
    mock_response.summary = "Lets Claude Code control a browser via Playwright MCP."
    mock_response.significance = "minor"

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                return_value={str(item.id): mock_response}
            )
            MockLLMClient.return_value = mock_client

            # Mock fetch_github_signals to not be called (non-GitHub URL)
            with patch(
                "src.workers.pipeline_workers.fetch_github_signals"
            ) as mock_fetch:
                await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "processed"
    # P0 check: quality_score must be non-zero after inline scoring
    assert reloaded.quality_score > 0.0, (
        f"Expected non-zero quality_score, got {reloaded.quality_score} — "
        "inline scoring did not run before commit"
    )
    assert reloaded.quality_score_details is not None
    assert reloaded.quality_score_details.get("method") == "heuristic"
    # Non-GitHub URL — fetch_github_signals should NOT have been called
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_classify_items_github_rate_limit_fallback(session, redis_client):
    """classify_items falls back to heuristic scoring when GitHub API is rate-limited.

    When fetch_github_signals returns rate_limited=True, the item must still
    receive a heuristic quality score (not 0.0 or 0.1).
    """
    source = await _create_source(session, tier="tier2")
    item = await _create_intel_item(
        session,
        source.id,
        status="queued",
        title="Cool GitHub Project",
        content="A useful tool for developers hosted on GitHub.",
    )
    # Set the URL to a GitHub URL so the GitHub path is triggered
    await session.execute(
        text("UPDATE intel_items SET url = :url WHERE id = CAST(:id AS uuid)"),
        {"url": "https://github.com/owner/cool-project", "id": str(item.id)},
    )
    await session.commit()

    mock_response = _make_llm_response(
        primary_type="tool", tags=["developer-tools"], confidence=0.88
    )
    mock_response.summary = "A useful tool for developers."
    mock_response.significance = "minor"

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.workers.pipeline_workers.LLMClient") as MockLLMClient:
            mock_client = AsyncMock()
            mock_client.classify_batch = AsyncMock(
                return_value={str(item.id): mock_response}
            )
            MockLLMClient.return_value = mock_client

            # Mock fetch_github_signals to return rate_limited
            with patch(
                "src.workers.pipeline_workers.fetch_github_signals",
                new_callable=AsyncMock,
                return_value={"rate_limited": True},
            ):
                await classify_items({"redis": redis_client})

    reloaded = await _reload_item(session, item.id)
    assert reloaded.status == "processed"
    # Rate-limited GitHub item must still get a heuristic score, not 0.0 or 0.1
    assert reloaded.quality_score > 0.0, (
        f"Expected non-zero quality_score, got {reloaded.quality_score} — "
        "rate-limit fallback to heuristic did not work"
    )
    assert (
        reloaded.quality_score != 0.1
    ), "quality_score should not be the old 0.1 failure sentinel"
    assert reloaded.quality_score_details is not None
    assert reloaded.quality_score_details.get("method") == "heuristic"
