"""
Integration tests for relevance_gate.py and pipeline_helpers.py.

Requires database (Docker Compose). Uses function-scoped session fixture
from conftest.py for isolation.
"""
import uuid
import pytest
import pytest_asyncio

from src.models.models import IntelItem, ReferenceItem
from src.services.pipeline_helpers import (
    VALID_TRANSITIONS,
    build_embed_input,
    safe_transition,
)
from src.services.relevance_gate import compute_gate_score, query_reference_proximity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_unit_vector(dim_index: int, dims: int = 1024) -> list[float]:
    """
    Returns a 1024-dim unit vector with 1.0 at dim_index and 0.0 elsewhere.
    Cosine similarity between two identical unit vectors = 1.0.
    Cosine similarity between orthogonal unit vectors = 0.0.
    """
    vec = [0.0] * dims
    vec[dim_index] = 1.0
    return vec


async def _create_intel_item(
    session,
    source_factory,
    status: str = "raw",
    embedding: list[float] | None = None,
) -> IntelItem:
    """Create a minimal IntelItem with a pre-existing source."""
    source = await source_factory(id=f"test:{uuid.uuid4().hex[:8]}")
    item = IntelItem(
        source_id=source.id,
        external_id=str(uuid.uuid4()),
        url=f"https://example.com/{uuid.uuid4().hex}",
        title="Test Item",
        content="Test content for pipeline testing",
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
    url: str | None = None,
) -> ReferenceItem:
    """Create a ReferenceItem with optional embedding."""
    ref = ReferenceItem(
        url=url or f"https://ref.example.com/{uuid.uuid4().hex}",
        title="Reference Item",
        description="A reference item for gate calibration",
        is_positive=is_positive,
        embedding=embedding,
        label="positive" if is_positive else "negative",
    )
    session.add(ref)
    await session.commit()
    return ref


# ---------------------------------------------------------------------------
# pipeline_helpers: build_embed_input
# ---------------------------------------------------------------------------


def test_build_embed_input_basic():
    """build_embed_input concatenates title and content with double newline."""
    result = build_embed_input("Title", "Content")
    assert result == "Title\n\nContent"


def test_build_embed_input_truncation():
    """Content longer than 4000 chars is truncated to 4000."""
    long_content = "x" * 5000
    result = build_embed_input("T", long_content)
    parts = result.split("\n\n", 1)
    assert len(parts[1]) == 4000


def test_build_embed_input_exact_4000():
    """Content exactly 4000 chars is not truncated."""
    content = "y" * 4000
    result = build_embed_input("T", content)
    parts = result.split("\n\n", 1)
    assert len(parts[1]) == 4000


def test_build_embed_input_short():
    """Short content is not padded."""
    result = build_embed_input("Hello", "World")
    assert result == "Hello\n\nWorld"


# ---------------------------------------------------------------------------
# pipeline_helpers: VALID_TRANSITIONS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transition_raw_to_embedded_succeeds(session, source_factory):
    """Behavioral: raw → embedded is a valid pipeline transition."""
    item = await _create_intel_item(session, source_factory, status="raw")
    result = await safe_transition(session, str(item.id), "raw", "embedded")
    assert result is True


@pytest.mark.asyncio
async def test_transition_raw_to_processed_fails(session, source_factory):
    """Behavioral: raw → processed is not a valid direct transition (must go through embedded/queued)."""
    item = await _create_intel_item(session, source_factory, status="raw")
    # This succeeds at the safe_transition level (optimistic lock matches) but
    # callers should check VALID_TRANSITIONS first. We verify the dict enforces it.
    assert "processed" not in VALID_TRANSITIONS["raw"]


@pytest.mark.asyncio
async def test_transition_embedded_to_queued_succeeds(session, source_factory):
    """Behavioral: embedded → queued is a valid pipeline transition."""
    item = await _create_intel_item(session, source_factory, status="embedded")
    result = await safe_transition(session, str(item.id), "embedded", "queued")
    assert result is True


@pytest.mark.asyncio
async def test_transition_failed_to_raw_retry(session, source_factory):
    """Behavioral: failed → raw retry path works."""
    item = await _create_intel_item(session, source_factory, status="failed")
    result = await safe_transition(session, str(item.id), "failed", "raw")
    assert result is True


def test_terminal_states_have_no_transitions():
    """filtered and processed are terminal — no valid outgoing transitions."""
    assert VALID_TRANSITIONS["filtered"] == []
    assert VALID_TRANSITIONS["processed"] == []


# ---------------------------------------------------------------------------
# pipeline_helpers: safe_transition (requires DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_transition_success(session, source_factory):
    """Transition succeeds when item is in the expected state."""
    item = await _create_intel_item(session, source_factory, status="raw")

    result = await safe_transition(session, str(item.id), "raw", "embedded")

    assert result is True
    # Verify the DB was actually updated — use populate_existing=True to bypass
    # SQLAlchemy identity map (raw SQL update does not invalidate ORM cache)
    from sqlalchemy import select

    row = await session.execute(
        select(IntelItem)
        .where(IntelItem.id == item.id)
        .execution_options(populate_existing=True)
    )
    updated = row.scalar_one()
    assert updated.status == "embedded"


@pytest.mark.asyncio
async def test_safe_transition_wrong_state(session, source_factory):
    """Transition fails (returns False) when item is not in expected state."""
    item = await _create_intel_item(session, source_factory, status="raw")

    # Try to transition from "embedded" when item is actually "raw"
    result = await safe_transition(session, str(item.id), "embedded", "queued")

    assert result is False
    # Verify status was NOT changed
    from sqlalchemy import select

    row = await session.execute(select(IntelItem).where(IntelItem.id == item.id))
    unchanged = row.scalar_one()
    assert unchanged.status == "raw"


@pytest.mark.asyncio
async def test_safe_transition_filtered_terminal(session, source_factory):
    """
    Items in filtered state are terminal — VALID_TRANSITIONS["filtered"] is empty.
    safe_transition itself enforces expected_from state, not VALID_TRANSITIONS.
    Callers are responsible for checking VALID_TRANSITIONS before calling.
    """
    # Terminal state has no valid outgoing transitions
    assert VALID_TRANSITIONS["filtered"] == []
    # safe_transition enforces optimistic locking: expected_from must match
    # If we (wrongly) call safe_transition expecting "queued" but item is "filtered",
    # the WHERE status=:expected clause won't match → returns False
    item = await _create_intel_item(session, source_factory, status="filtered")
    result = await safe_transition(session, str(item.id), "queued", "processed")
    assert result is False  # item.status is "filtered", not "queued"


# ---------------------------------------------------------------------------
# relevance_gate: compute_gate_score (requires DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_gate_score_empty_refs(session):
    """With no reference items, gate returns (0.0, True) — pass by default."""
    item_embedding = _make_unit_vector(0)
    score, is_relevant = await compute_gate_score(session, item_embedding)

    assert score == 0.0
    assert is_relevant is True


@pytest.mark.asyncio
async def test_compute_gate_score_with_positive_refs(session):
    """Item similar to positive references should have score > 0 and be relevant."""
    # Insert 3 positive reference items with the same unit vector as our query
    embedding = _make_unit_vector(5)
    for _ in range(3):
        await _create_reference_item(session, is_positive=True, embedding=embedding)

    score, is_relevant = await compute_gate_score(session, embedding, threshold=0.3)

    # Identical vectors → cosine_sim ≈ 1.0, no negative refs → score ≈ 1.0
    assert score > 0.0
    assert is_relevant is True


@pytest.mark.asyncio
async def test_compute_gate_score_negative_penalty(session):
    """Negative reference items should reduce the gate score."""
    query_vec = _make_unit_vector(10)

    # One positive reference (identical to query)
    await _create_reference_item(
        session, is_positive=True, embedding=_make_unit_vector(10)
    )
    # One negative reference (also identical to query — worst case penalty)
    await _create_reference_item(
        session, is_positive=False, embedding=_make_unit_vector(10)
    )

    score_with_neg, _ = await compute_gate_score(session, query_vec, threshold=0.1)

    # Score = max_positive(1.0) - max_negative(1.0) * 0.5 = 0.5
    # Without negative: would be 1.0
    assert score_with_neg == pytest.approx(0.5, abs=0.05)


@pytest.mark.asyncio
async def test_compute_gate_score_only_negatives(session):
    """Only negative references: max_positive=0.0, score = 0.0 - neg*0.5 → clamped to 0.0."""
    query_vec = _make_unit_vector(20)
    await _create_reference_item(
        session, is_positive=False, embedding=_make_unit_vector(20)
    )

    score, is_relevant = await compute_gate_score(session, query_vec, threshold=0.5)

    # max_positive=0, max_negative≈1.0 → 0 - 0.5 = -0.5 → clamped to 0.0
    assert score == pytest.approx(0.0, abs=0.05)
    assert is_relevant is False


@pytest.mark.asyncio
async def test_query_reference_proximity_returns_tuples(session):
    """query_reference_proximity returns (bool, float) tuples."""
    embedding = _make_unit_vector(30)
    await _create_reference_item(session, is_positive=True, embedding=embedding)
    await _create_reference_item(
        session, is_positive=False, embedding=_make_unit_vector(31)
    )

    rows = await query_reference_proximity(session, embedding, limit=5)

    assert len(rows) == 2
    for is_pos, sim in rows:
        assert isinstance(is_pos, bool)
        assert isinstance(sim, float)
        assert 0.0 <= sim <= 1.01  # slight float rounding tolerance
