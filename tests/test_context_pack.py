"""Tests for context-pack briefing compression.

Unit tests for _compress_to_bullets() logic and integration test for
GET /v1/context-pack?compress=true.
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

from src.api.v1.context_pack import _compress_to_bullets
from src.models.models import IntelItem, Source


# ---------------------------------------------------------------------------
# Unit tests for _compress_to_bullets
# ---------------------------------------------------------------------------


def _make_item(
    title: str, significance: str = "informational", summary: str = ""
) -> dict:
    """Helper to create a minimal item dict for unit tests."""
    return {
        "title": title,
        "significance": significance,
        "summary": summary or f"Summary for {title}",
        "excerpt": f"Excerpt for {title}",
        "url": f"https://example.com/{title.lower().replace(' ', '-')}",
        "primary_type": "update",
        "tags": ["test"],
        "relevance_score": 0.8,
    }


class TestCompressToBulletsGrouping:
    """Test grouping across multiple significance tiers."""

    def test_multi_tier_grouping(self):
        """10 items spanning 3 tiers produce 3-5 bullets with correct labels."""
        items = (
            [_make_item(f"Breaking {i}", "breaking") for i in range(2)]
            + [_make_item(f"Major {i}", "major") for i in range(3)]
            + [_make_item(f"Info {i}", "informational") for i in range(5)]
        )
        result = _compress_to_bullets(items, "ai-tools", 7)

        assert isinstance(result, str)
        assert "BREAKING" in result
        assert "Major" in result
        # Count bullet lines (lines starting with "- ")
        bullet_lines = [line for line in result.split("\n") if line.startswith("- ")]
        assert 3 <= len(bullet_lines) <= 5
        # Header contains item count
        assert "10 items" in result


class TestCompressToBulletsEmpty:
    """Test empty input."""

    def test_empty_list(self):
        """Empty input produces header with '0 items' and no bullets."""
        result = _compress_to_bullets([], None, 7)

        assert "0 items" in result
        bullet_lines = [line for line in result.split("\n") if line.startswith("- ")]
        assert len(bullet_lines) == 0


class TestCompressToBulletsSingleTier:
    """Test single-tier input."""

    def test_single_tier_minor(self):
        """3 items all 'minor' produce exactly 1 header + 1 bullet."""
        items = [_make_item(f"Minor item {i}", "minor") for i in range(3)]
        result = _compress_to_bullets(items, None, 14)

        lines = [line for line in result.split("\n") if line.strip()]
        # Should be 2: 1 header + 1 bullet line
        assert len(lines) == 2
        assert "3 minor" in result


class TestCompressToBulletsDetails:
    """Test specific formatting details."""

    def test_breaking_uses_summary(self):
        """Breaking bullet uses the top item's summary text."""
        items = [_make_item("Critical bug", "breaking", summary="A critical SDK bug")]
        result = _compress_to_bullets(items, None, 7)
        assert "A critical SDK bug" in result

    def test_topic_in_header(self):
        """Topic string appears in the header."""
        items = [_make_item("Item", "major")]
        result = _compress_to_bullets(items, "mcp-servers", 7)
        assert "on mcp-servers" in result

    def test_no_topic_in_header(self):
        """Without topic, no 'on X' in header."""
        items = [_make_item("Item", "major")]
        result = _compress_to_bullets(items, None, 7)
        assert " on " not in result

    def test_max_five_bullets(self):
        """Even with many tiers, bullets are capped at 5."""
        # Create items across all 4 tiers
        items = (
            [_make_item("B", "breaking")]
            + [_make_item("Ma", "major")]
            + [_make_item("Mi", "minor")]
            + [_make_item("I", "informational")]
        )
        result = _compress_to_bullets(items, None, 7)
        bullet_lines = [line for line in result.split("\n") if line.startswith("- ")]
        assert len(bullet_lines) <= 5


# ---------------------------------------------------------------------------
# Integration test: GET /v1/context-pack?compress=true
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def context_pack_items(session, source_factory):
    """Create items with varying significance for context-pack compression testing."""
    source = await source_factory(id="test:cp-source", name="CP Source")

    items_data = [
        {"title": "Breaking SDK Change", "significance": "breaking", "tags": ["sdk"]},
        {"title": "Major API Update", "significance": "major", "tags": ["api"]},
        {
            "title": "Major Framework Release",
            "significance": "major",
            "tags": ["framework"],
        },
        {"title": "Minor Doc Fix", "significance": "minor", "tags": ["docs"]},
        {"title": "Info Update 1", "significance": "informational", "tags": ["info"]},
        {"title": "Info Update 2", "significance": "informational", "tags": ["info"]},
    ]

    items = []
    for data in items_data:
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"ext-{uuid.uuid4()}",
            url=f"https://example.com/{uuid.uuid4()}",
            title=data["title"],
            content="Sufficient content for testing the briefing compression feature end to end with real data.",
            primary_type="update",
            tags=data["tags"],
            significance=data["significance"],
            status="processed",
            relevance_score=0.85,
            quality_score=0.80,
            confidence_score=0.9,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(item)
        items.append(item)

    await session.commit()
    return items


@pytest.mark.asyncio
async def test_context_pack_compress_json_integration(
    client, api_key_header, context_pack_items
):
    """GET /v1/context-pack?format=json&compress=true returns compressed_briefing
    and at most 3 items."""
    resp = await client.get(
        "/v1/context-pack",
        params={"format": "json", "compress": "true", "days": 30},
        headers=api_key_header["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()

    # Must have compressed_briefing key
    assert "compressed_briefing" in data
    assert isinstance(data["compressed_briefing"], str)
    assert len(data["compressed_briefing"]) > 0

    # Items capped at 3 for drill-in
    assert "items" in data
    assert len(data["items"]) <= 3

    # compressed_briefing contains bullet content
    briefing = data["compressed_briefing"]
    assert "items" in briefing  # header line contains "N items"
    assert "compressed view" in briefing


@pytest.mark.asyncio
async def test_context_pack_no_compress_unchanged(
    client, api_key_header, context_pack_items
):
    """GET /v1/context-pack?format=json (no compress) preserves existing behavior --
    no compressed_briefing key, all items returned."""
    resp = await client.get(
        "/v1/context-pack",
        params={"format": "json", "days": 30},
        headers=api_key_header["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()

    # No compressed_briefing when compress is omitted
    assert "compressed_briefing" not in data
    # All items returned (fixture has 6 items)
    assert len(data["items"]) >= 4
