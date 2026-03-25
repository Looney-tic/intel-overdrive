"""
Tests for scripts/seed_reference_set.py.

Covers:
- Data quality checks (no DB required): count, split, required fields, uniqueness, label consistency, categories
- Integration: idempotency logic with mocked embeddings (requires DB via session fixture)
"""
import sys
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.models.models import ReferenceItem

# Ensure scripts/ is importable from project root (seed script adds sys.path itself,
# but tests run from the project root so direct import works if PYTHONPATH is set via
# conftest. Use importlib as a fallback to be safe.)
try:
    from scripts.seed_reference_set import REFERENCE_ITEMS, EMBED_BATCH_SIZE, main
except ImportError:
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "seed_reference_set",
        str(pathlib.Path(__file__).parent.parent / "scripts" / "seed_reference_set.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    # Insert project root into sys.path so the seed script's own imports work
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    spec.loader.exec_module(mod)
    REFERENCE_ITEMS = mod.REFERENCE_ITEMS
    EMBED_BATCH_SIZE = mod.EMBED_BATCH_SIZE
    main = mod.main


# ---------------------------------------------------------------------------
# Data Quality Tests (no DB required)
# ---------------------------------------------------------------------------


def test_reference_items_count():
    """REFERENCE_ITEMS list must have 50-200 items."""
    assert (
        50 <= len(REFERENCE_ITEMS) <= 200
    ), f"Expected 50-200 items, got {len(REFERENCE_ITEMS)}"


def test_reference_items_positive_negative_split():
    """At least 50 positive and 20 negative items."""
    positive = [i for i in REFERENCE_ITEMS if i["is_positive"]]
    negative = [i for i in REFERENCE_ITEMS if not i["is_positive"]]
    assert len(positive) >= 50, f"Expected >=50 positive items, got {len(positive)}"
    assert len(negative) >= 20, f"Expected >=20 negative items, got {len(negative)}"


def test_reference_items_required_fields():
    """Every item dict must have url, title, description, is_positive, label."""
    required = {"url", "title", "description", "is_positive", "label"}
    for idx, item in enumerate(REFERENCE_ITEMS):
        missing = required - set(item.keys())
        assert (
            not missing
        ), f"Item[{idx}] ({item.get('url', '?')!r}) missing fields: {missing}"


def test_reference_items_unique_urls():
    """All URLs in REFERENCE_ITEMS must be unique."""
    urls = [item["url"] for item in REFERENCE_ITEMS]
    seen = set()
    duplicates = [url for url in urls if url in seen or seen.add(url)]
    assert not duplicates, f"Duplicate URLs found: {duplicates}"


def test_reference_items_label_consistency():
    """label=='positive' iff is_positive==True; label=='negative' iff is_positive==False."""
    for idx, item in enumerate(REFERENCE_ITEMS):
        if item["is_positive"]:
            assert (
                item["label"] == "positive"
            ), f"Item[{idx}] ({item['url']!r}): is_positive=True but label={item['label']!r}"
        else:
            assert (
                item["label"] == "negative"
            ), f"Item[{idx}] ({item['url']!r}): is_positive=False but label={item['label']!r}"


def test_positive_items_cover_categories():
    """Positive items cover Anthropic, MCP, Claude Code, and skill/hook/workflow content."""
    positive_urls = [i["url"].lower() for i in REFERENCE_ITEMS if i["is_positive"]]
    positive_titles = [i["title"].lower() for i in REFERENCE_ITEMS if i["is_positive"]]
    positive_descs = [
        (i["description"] or "").lower() for i in REFERENCE_ITEMS if i["is_positive"]
    ]

    combined = positive_urls + positive_titles + positive_descs

    assert any(
        "anthropic" in s for s in combined
    ), "No Anthropic content in positive items"
    assert any(
        "mcp" in s or "modelcontextprotocol" in s for s in combined
    ), "No MCP content in positive items"
    assert any(
        "claude-code" in s or "claude_code" in s or "claude code" in s for s in combined
    ), "No Claude Code content in positive items"
    assert any(
        "skill" in s or "hook" in s or "workflow" in s for s in combined
    ), "No skill/hook/workflow content in positive items"


def test_negative_items_cover_categories():
    """Negative items cover frontend frameworks, non-Claude LLMs, DevOps, and crypto."""
    negative_urls = [i["url"].lower() for i in REFERENCE_ITEMS if not i["is_positive"]]
    negative_titles = [
        i["title"].lower() for i in REFERENCE_ITEMS if not i["is_positive"]
    ]

    combined = negative_urls + negative_titles

    assert any(
        "react" in s or "vue" in s or "angular" in s for s in combined
    ), "No frontend framework content in negative items"
    assert any(
        "openai" in s or "gemini" in s or "llama" in s for s in combined
    ), "No non-Claude LLM content in negative items"
    assert any(
        "docker" in s or "kubernetes" in s for s in combined
    ), "No DevOps content in negative items"
    assert any(
        "ethereum" in s or "solana" in s or "bitcoin" in s for s in combined
    ), "No crypto/Web3 content in negative items"


# ---------------------------------------------------------------------------
# Integration Test: idempotency (requires DB via session fixture)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_idempotent(engine, session):
    """
    Inserting one reference item manually and then running seed_reference_set.main()
    should skip the pre-existing URL and insert the remaining items.

    Uses the test engine (tables already created by engine fixture) as the backend for
    main()'s async_session_factory. get_embeddings is patched to return fake 1024-dim
    vectors without hitting the API.
    """
    # Create a session factory backed by the test engine (all tables already exist)
    test_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Pre-seed one item with the first URL from REFERENCE_ITEMS
    first_item_data = REFERENCE_ITEMS[0]
    pre_existing = ReferenceItem(
        url=first_item_data["url"],
        title=first_item_data["title"],
        description=first_item_data.get("description"),
        embedding=[0.5] * 1024,
        embedding_model_version="voyage-3.5-lite",
        label=first_item_data["label"],
        is_positive=first_item_data["is_positive"],
    )
    session.add(pre_existing)
    await session.commit()

    with (
        patch(
            "scripts.seed_reference_set.init_db",
            new=AsyncMock(return_value=None),
        ),
        # Wire main() to use the test engine's session factory
        # seed_reference_set uses `import src.core.init_db as _db` pattern
        patch.object(
            __import__("scripts.seed_reference_set", fromlist=["_db"])._db,
            "async_session_factory",
            new=test_session_factory,
        ),
        patch(
            "scripts.seed_reference_set.aioredis.from_url",
        ) as mock_redis_from_url,
        patch(
            "src.services.llm_client.LLMClient.get_embeddings",
            new_callable=AsyncMock,
        ) as mock_embed,
        patch(
            "src.services.spend_tracker.SpendTracker.check_spend_gate",
            new_callable=AsyncMock,
        ),
        patch(
            "src.services.spend_tracker.SpendTracker.track_spend",
            new_callable=AsyncMock,
        ),
    ):
        from unittest.mock import MagicMock

        mock_redis_instance = MagicMock()
        mock_redis_instance.aclose = AsyncMock()
        mock_redis_from_url.return_value = mock_redis_instance

        # get_embeddings returns one 1024-dim vector per text in the batch
        async def _return_vectors(texts):
            return [[0.1] * 1024 for _ in texts]

        mock_embed.side_effect = _return_vectors

        await main()

    # After running main(), total rows should equal len(REFERENCE_ITEMS)
    result = await session.execute(
        select(ReferenceItem).execution_options(populate_existing=True)
    )
    all_rows = result.scalars().all()
    assert len(all_rows) == len(
        REFERENCE_ITEMS
    ), f"Expected {len(REFERENCE_ITEMS)} rows after seed, got {len(all_rows)}"

    # The pre-existing item should still be there (not duplicated)
    existing_urls = {row.url for row in all_rows}
    assert (
        first_item_data["url"] in existing_urls
    ), "Pre-existing item URL was not preserved"

    # All items should have an embedding
    null_embeddings = [row for row in all_rows if row.embedding is None]
    assert (
        not null_embeddings
    ), f"{len(null_embeddings)} items have NULL embeddings after seed"
