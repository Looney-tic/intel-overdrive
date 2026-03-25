"""
FOUND-04: Dedup service tests covering all 3 layers.

Layer 1: URL hash check
Layer 2: Content fingerprint check
Layer 3: Embedding cosine similarity (pgvector <=> operator)
"""
import uuid
import pytest
import pytest_asyncio
from sqlalchemy import text

from src.services.dedup_service import DedupService
from src.models.models import IntelItem, Source


async def _create_source(session):
    """Helper: insert a minimal Source row (required for IntelItem FK)."""
    src = Source(
        id="test-source",
        name="Test Source",
        type="test",
        url="https://example.com/feed",
        is_active=True,
    )
    session.add(src)
    await session.flush()
    return src


async def _create_intel_item(
    session,
    url: str,
    content: str = "Default content",
    title: str = "Test Item",
    embedding: list = None,
):
    """Helper: insert a minimal IntelItem row."""
    from src.services.dedup_service import DedupService as DS

    ds = DS(session)
    url_hash = ds._compute_url_hash(url)
    content_hash = ds._get_content_fingerprint(content)

    item = IntelItem(
        id=uuid.uuid4(),
        source_id="test-source",
        external_id=str(uuid.uuid4()),
        url=url,
        url_hash=url_hash,
        title=title,
        content=content,
        primary_type="tool",
        content_hash=content_hash,
        embedding=embedding,
    )
    session.add(item)
    await session.flush()
    return item


# ============================================================================
# Layer 1: URL hash check
# ============================================================================


@pytest.mark.asyncio
async def test_check_url_exists_returns_false_for_new_url(session):
    """FOUND-04 Layer 1: check_url_exists returns False when URL not in DB."""
    await _create_source(session)
    svc = DedupService(session)
    result = await svc.check_url_exists("https://never-seen-url.example.com/post")
    assert result is False


@pytest.mark.asyncio
async def test_check_url_exists_returns_true_after_insert(session):
    """FOUND-04 Layer 1: check_url_exists returns True for an existing URL."""
    await _create_source(session)
    url = "https://example.com/existing-post"
    await _create_intel_item(session, url=url)
    await session.commit()

    svc = DedupService(session)
    result = await svc.check_url_exists(url)
    assert result is True


# ============================================================================
# Layer 2: Content fingerprint check
# ============================================================================


@pytest.mark.asyncio
async def test_find_duplicate_by_content_returns_none_for_unique_content(session):
    """FOUND-04 Layer 2: find_duplicate_by_content returns None for unique content."""
    await _create_source(session)
    svc = DedupService(session)
    result = await svc.find_duplicate_by_content("Completely unique content 12345xyz")
    assert result is None


@pytest.mark.asyncio
async def test_find_duplicate_by_content_returns_item_for_matching_fingerprint(session):
    """FOUND-04 Layer 2: find_duplicate_by_content returns item when content hash matches."""
    await _create_source(session)
    content = "Claude 3.5 Haiku: new speed record for coding tasks"
    item = await _create_intel_item(
        session, url="https://example.com/haiku", content=content
    )
    await session.commit()

    svc = DedupService(session)
    # Same content, same fingerprint
    result = await svc.find_duplicate_by_content(content)
    assert result is not None
    assert result.id == item.id


@pytest.mark.asyncio
async def test_find_duplicate_by_content_normalizes_whitespace(session):
    """FOUND-04 Layer 2: Content fingerprint is whitespace-insensitive."""
    await _create_source(session)
    content = "Some   content   with   extra   spaces"
    item = await _create_intel_item(
        session, url="https://example.com/spaced", content=content
    )
    await session.commit()

    svc = DedupService(session)
    # Same words, different whitespace
    result = await svc.find_duplicate_by_content("Some content with extra spaces")
    assert result is not None


# ============================================================================
# Layer 3: Embedding cosine similarity
# ============================================================================


@pytest.mark.asyncio
async def test_find_similar_by_embedding_finds_near_identical_vector(session):
    """FOUND-04 Layer 3: find_similar_by_embedding returns item for near-identical vector."""
    await _create_source(session)

    # Store a known embedding
    base_embedding = [0.1] * 1024
    await _create_intel_item(
        session,
        url="https://example.com/embedded",
        content="Embedded content",
        embedding=base_embedding,
    )
    await session.commit()

    svc = DedupService(session)
    # Very similar vector (almost identical — cosine distance << 0.08)
    query_embedding = [0.1] * 1023 + [0.100001]
    result = await svc.find_similar_by_embedding(query_embedding, threshold=0.08)
    assert result is not None


@pytest.mark.asyncio
async def test_find_similar_by_embedding_returns_none_for_orthogonal_vector(session):
    """FOUND-04 Layer 3: find_similar_by_embedding returns None for very different vector."""
    await _create_source(session)

    base_embedding = [1.0] + [0.0] * 1023
    await _create_intel_item(
        session,
        url="https://example.com/orthogonal",
        content="Orthogonal content",
        embedding=base_embedding,
    )
    await session.commit()

    svc = DedupService(session)
    # Orthogonal vector — cosine distance = 1.0
    orthogonal_embedding = [0.0] * 1023 + [1.0]
    result = await svc.find_similar_by_embedding(orthogonal_embedding, threshold=0.08)
    assert result is None


# ============================================================================
# Unified is_duplicate() 3-layer check
# ============================================================================


@pytest.mark.asyncio
async def test_is_duplicate_returns_url_hash_for_existing_url(session):
    """FOUND-04: is_duplicate returns (True, 'url_hash') for existing URL."""
    await _create_source(session)
    url = "https://example.com/is-dup-url"
    await _create_intel_item(session, url=url)
    await session.commit()

    svc = DedupService(session)
    is_dup, reason = await svc.is_duplicate(url=url, content="Different content")
    assert is_dup is True
    assert reason == "url_hash"


@pytest.mark.asyncio
async def test_is_duplicate_returns_content_fingerprint_for_same_content(session):
    """FOUND-04: is_duplicate returns (True, 'content_fingerprint') for same content, different URL."""
    await _create_source(session)
    content = "Exact same content as before"
    await _create_intel_item(
        session, url="https://example.com/original", content=content
    )
    await session.commit()

    svc = DedupService(session)
    is_dup, reason = await svc.is_duplicate(
        url="https://different-url.example.com/new",
        content=content,
    )
    assert is_dup is True
    assert reason == "content_fingerprint"


@pytest.mark.asyncio
async def test_is_duplicate_returns_false_for_genuinely_new_content(session):
    """FOUND-04: is_duplicate returns (False, None) for genuinely new content."""
    await _create_source(session)
    svc = DedupService(session)
    is_dup, reason = await svc.is_duplicate(
        url="https://totally-new.example.com/article",
        content="Brand new content never seen before xyz987",
    )
    assert is_dup is False
    assert reason is None


@pytest.mark.asyncio
async def test_is_duplicate_returns_embedding_similarity_for_near_identical(session):
    """FOUND-04: is_duplicate returns (True, 'embedding_similarity') for near-identical embedding."""
    await _create_source(session)

    base_embedding = [0.5] * 1024
    await _create_intel_item(
        session,
        url="https://example.com/emb-dup",
        content="Unique content A1B2C3",
        embedding=base_embedding,
    )
    await session.commit()

    svc = DedupService(session)
    similar_embedding = [0.5] * 1023 + [0.500001]
    is_dup, reason = await svc.is_duplicate(
        url="https://different-url.example.com/emb-new",
        content="Unique content D4E5F6",  # different content — won't trigger layer 2
        embedding=similar_embedding,
    )
    assert is_dup is True
    assert reason == "embedding_similarity"


@pytest.mark.asyncio
async def test_find_similar_by_embedding_excludes_old_items(session):
    """FOUND-04 Layer 3: Items older than the days window are excluded from similarity search."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as sa_text

    await _create_source(session)
    base_embedding = [0.5] * 1024
    item = await _create_intel_item(
        session,
        url="https://example.com/old-item",
        content="Old content xyz",
        embedding=base_embedding,
    )
    await session.commit()

    # Backdate the item to 30 days ago (beyond default 7-day window)
    await session.execute(
        sa_text("UPDATE intel_items SET created_at = :old_date WHERE id = :item_id"),
        {
            "old_date": datetime.now(timezone.utc) - timedelta(days=30),
            "item_id": str(item.id),
        },
    )
    await session.commit()

    svc = DedupService(session)
    # Query with identical embedding — should NOT match because item is too old
    result = await svc.find_similar_by_embedding(base_embedding, threshold=0.08, days=7)
    assert result is None
