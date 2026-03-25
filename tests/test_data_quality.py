"""
Data quality tests for plan 14-03:
  - Profile skill validation and warnings
  - TAG_GROUPS unification (context_pack imports from feed)
  - Score field documentation in guide
  - Classification prompt upgrade (source tier + 4000 char window)
  - Thread narrative improvements (summary-led + significance distribution)
"""

import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Task 1: Profile validation — unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestSkillTagExpansion:
    """SKILL_TAG_EXPANSION is accessible at module level."""

    def test_module_level_export(self):
        from src.api.v1.feed import SKILL_TAG_EXPANSION

        assert isinstance(SKILL_TAG_EXPANSION, dict)
        assert len(SKILL_TAG_EXPANSION) > 0

    def test_contains_expected_skills(self):
        from src.api.v1.feed import SKILL_TAG_EXPANSION

        expected = {
            "agentic-engineering",
            "plugin-development",
            "multi-agent-orchestration",
            "pipeline-design",
            "browser-automation",
            "api-development",
            "devops",
            "security",
            "testing",
            "documentation",
        }
        assert expected == set(SKILL_TAG_EXPANSION.keys())

    def test_each_skill_maps_to_set_of_tags(self):
        from src.api.v1.feed import SKILL_TAG_EXPANSION

        for skill, tags in SKILL_TAG_EXPANSION.items():
            assert isinstance(tags, set), f"{skill} should map to a set"
            assert len(tags) > 0, f"{skill} should have at least one tag"


class TestTagGroupsUnification:
    """context_pack.py must import TAG_GROUPS from feed.py (not define its own)."""

    def test_context_pack_imports_tag_groups_from_feed(self):
        import src.api.v1.context_pack as cp
        import src.api.v1.feed as feed

        # After unification, context_pack.TAG_GROUPS is the same object as feed.TAG_GROUPS
        assert cp.TAG_GROUPS is feed.TAG_GROUPS

    def test_unified_tag_groups_has_ten_groups(self):
        from src.api.v1.feed import TAG_GROUPS

        assert len(TAG_GROUPS) >= 10

    def test_unified_tag_groups_includes_mcp_and_claude_code(self):
        from src.api.v1.feed import TAG_GROUPS

        assert "mcp" in TAG_GROUPS
        assert "claude-code" in TAG_GROUPS


# ---------------------------------------------------------------------------
# Task 1: Profile API tests (with DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_valid_skills_no_warnings(client, api_key_header):
    """Valid skills → response has no warnings key."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python", "fastapi"],
            "skills": ["agentic-engineering", "browser-automation"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["message"] == "Profile updated successfully"
    assert "warnings" not in data
    assert "valid_skills" in data
    assert "agentic-engineering" in data["valid_skills"]


@pytest.mark.asyncio
async def test_profile_unrecognized_skill_returns_warnings(client, api_key_header):
    """Unrecognized skill → warnings array with guidance."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python"],
            "skills": ["nonexistent-skill"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert len(data["warnings"]) == 1
    warning = data["warnings"][0]
    assert "nonexistent-skill" in warning
    assert "will not affect feed ranking" in warning
    # The warning mentions valid alternatives
    assert "agentic-engineering" in warning


@pytest.mark.asyncio
async def test_profile_mix_valid_invalid_skills(client, api_key_header):
    """Mix of valid + invalid skills: valid stored, warnings for invalid only."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python"],
            "skills": ["agentic-engineering", "bad-skill-1", "bad-skill-2"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    # Two warnings for the two invalid skills
    assert "warnings" in data
    assert len(data["warnings"]) == 2
    # Profile stores all three (backward compat)
    assert "agentic-engineering" in data["profile"]["skills"]
    assert "bad-skill-1" in data["profile"]["skills"]


@pytest.mark.asyncio
async def test_profile_unrecognized_skill_still_stored(client, api_key_header):
    """Unrecognized skills must be stored (backward compat) despite warning."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": [],
            "skills": ["future-skill-not-yet-known"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "future-skill-not-yet-known" in data["profile"]["skills"]


@pytest.mark.asyncio
async def test_profile_tools_providers_role_stored(client, api_key_header):
    """Profile accepts tools, providers, and role fields."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python"],
            "skills": ["agentic-engineering"],
            "tools": ["claude-code", "cursor"],
            "providers": ["anthropic", "google"],
            "role": "builder",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["profile"]["tools"] == ["claude-code", "cursor"]
    assert data["profile"]["providers"] == ["anthropic", "google"]
    assert data["profile"]["role"] == "builder"
    assert "warnings" not in data
    assert "valid_tools" in data
    assert "claude-code" in data["valid_tools"]
    assert "valid_providers" in data
    assert "anthropic" in data["valid_providers"]


@pytest.mark.asyncio
async def test_profile_unrecognized_tool_warns(client, api_key_header):
    """Unrecognized tool → warning with valid alternatives."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python"],
            "skills": [],
            "tools": ["unknown-ide"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert any("unknown-ide" in w for w in data["warnings"])
    assert any("will not affect feed ranking" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_profile_unrecognized_provider_warns(client, api_key_header):
    """Unrecognized provider → warning with valid alternatives."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python"],
            "skills": [],
            "providers": ["unknown-llm"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "warnings" in data
    assert any("unknown-llm" in w for w in data["warnings"])


@pytest.mark.asyncio
async def test_profile_optional_fields_default_empty(client, api_key_header):
    """tools, providers, role are optional — omitting them doesn't error."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python"],
            "skills": ["testing"],
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    # tools/providers/role should not be in profile if not provided
    assert "tools" not in data["profile"]
    assert "providers" not in data["profile"]
    assert "role" not in data["profile"]


@pytest.mark.asyncio
async def test_profile_invalid_role_rejected(client, api_key_header):
    """Invalid role value → 422."""
    headers = api_key_header["headers"]
    resp = await client.post(
        "/v1/profile",
        json={
            "tech_stack": ["python"],
            "skills": [],
            "role": "invalid-role",
        },
        headers=headers,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 1: Guide endpoint — score field documentation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guide_documents_all_three_score_fields(client):
    """Guide response_fields must include all three score fields with distinct semantics."""
    resp = await client.get("/v1/guide")
    assert resp.status_code == 200
    data = resp.json()
    response_fields = data["response_fields"]

    assert "relevance_score" in response_fields
    assert "quality_score" in response_fields
    assert "confidence_score" in response_fields

    # Each field must have a distinct, non-empty description
    descriptions = {
        response_fields["relevance_score"],
        response_fields["quality_score"],
        response_fields["confidence_score"],
    }
    # All three descriptions must be unique
    assert len(descriptions) == 3
    # Each must mention 0.0-1.0 range
    for field in ["relevance_score", "quality_score", "confidence_score"]:
        desc = response_fields[field]
        assert "0.0-1.0" in desc, f"{field} description should mention 0.0-1.0 range"


@pytest.mark.asyncio
async def test_guide_relevance_score_mentions_semantic(client):
    """relevance_score doc should mention semantic relevance."""
    resp = await client.get("/v1/guide")
    data = resp.json()
    desc = data["response_fields"]["relevance_score"]
    assert "semantic" in desc.lower() or "pgvector" in desc.lower()


@pytest.mark.asyncio
async def test_guide_confidence_score_mentions_classifier(client):
    """confidence_score doc should mention classification confidence."""
    resp = await client.get("/v1/guide")
    data = resp.json()
    desc = data["response_fields"]["confidence_score"]
    assert "classif" in desc.lower()


@pytest.mark.asyncio
async def test_guide_quick_start_mentions_auth(client):
    """quick_start must mention auth/API key requirement."""
    resp = await client.get("/v1/guide")
    data = resp.json()
    quick_start = data["quick_start"]
    combined = " ".join(quick_start)
    assert "API" in combined or "api" in combined.lower()
    assert "key" in combined.lower()
    # Auth step should come before feed step
    auth_idx = next(
        (
            i
            for i, s in enumerate(quick_start)
            if "key" in s.lower() and "api" in s.lower()
        ),
        -1,
    )
    feed_idx = next(
        (i for i, s in enumerate(quick_start) if "/v1/feed" in s),
        999,
    )
    assert auth_idx < feed_idx, "Auth step should appear before feed step"


# ---------------------------------------------------------------------------
# Task 2: Classification prompt — unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestClassificationPrompt:
    """Classification system prompt has actionable SUMMARY guidance."""

    def test_system_prompt_has_does_not_is_guidance(self):
        from src.workers.pipeline_workers import CLASSIFICATION_SYSTEM_PROMPT

        # Must guide the model to lead with what item DOES, not what it IS
        assert (
            "DOES" in CLASSIFICATION_SYSTEM_PROMPT
            or "does" in CLASSIFICATION_SYSTEM_PROMPT
        )

    def test_system_prompt_has_source_tier_guidance(self):
        from src.workers.pipeline_workers import CLASSIFICATION_SYSTEM_PROMPT

        assert (
            "SOURCE TIER" in CLASSIFICATION_SYSTEM_PROMPT
            or "Tier 1" in CLASSIFICATION_SYSTEM_PROMPT
        )

    def test_system_prompt_has_tier1_description(self):
        from src.workers.pipeline_workers import CLASSIFICATION_SYSTEM_PROMPT

        assert "Tier 1" in CLASSIFICATION_SYSTEM_PROMPT

    def test_system_prompt_has_tier3_description(self):
        from src.workers.pipeline_workers import CLASSIFICATION_SYSTEM_PROMPT

        assert "Tier 3" in CLASSIFICATION_SYSTEM_PROMPT


class TestClassificationContentWindow:
    """Classification content window is 4000 chars (not 2000)."""

    def test_content_window_is_4000_chars(self):
        import inspect
        from src.workers import pipeline_workers

        source = inspect.getsource(pipeline_workers)
        # Should have 4000 in content slicing, not 2000
        assert "content[:4000]" in source, "Content window should be 4000 chars"
        # Old 2000 limit should be gone from classification input
        assert "content[:2000]" not in source, "Old 2000-char limit should be removed"

    def test_content_includes_source_tier(self):
        import inspect
        from src.workers import pipeline_workers

        source = inspect.getsource(pipeline_workers)
        assert "source_tier" in source, "source_tier should appear in pipeline_workers"
        assert "source_name" in source


# ---------------------------------------------------------------------------
# Task 2: Thread narratives — unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestBuildNarrative:
    """_build_narrative produces summary-led output with significance distribution."""

    def _make_items(self, sigs=None, summaries=None):
        """Helper to build item dicts for _build_narrative."""
        sigs = sigs or ["major", "minor", "informational"]
        summaries = summaries or [
            "Lets Claude Code control a browser via Playwright MCP.",
            "Minor update to API client library.",
            "General awareness post.",
        ]
        items = []
        for i, (sig, summary) in enumerate(zip(sigs, summaries)):
            items.append(
                {
                    "id": str(uuid.uuid4()),
                    "title": f"Title {i}",
                    "url": f"https://example.com/{i}",
                    "summary": summary,
                    "tags": ["mcp", "browser-automation"] if i == 0 else ["api"],
                    "significance": sig,
                }
            )
        return items

    def _make_thread(self, items):
        now = datetime.now(timezone.utc)
        return {
            "item_count": len(items),
            "first_seen": now,
            "last_seen": now,
            "dominant_significance": "major",
        }

    def test_narrative_leads_with_item_summary(self):
        from src.api.v1.threads import _build_narrative

        items = self._make_items()
        thread = self._make_thread(items)
        narrative = _build_narrative(items, thread)

        # Should lead with first item's summary (the most significant item)
        first_summary = items[0]["summary"]
        # The summary (or truncated version) should appear near the start
        assert (
            first_summary[:50] in narrative
        ), f"Narrative should lead with item summary. Got:\n{narrative}"

    def test_narrative_includes_significance_distribution(self):
        from src.api.v1.threads import _build_narrative

        items = self._make_items(
            sigs=["breaking", "major", "minor"],
            summaries=["Breaking change summary.", "Major feature.", "Minor fix."],
        )
        thread = self._make_thread(items)
        narrative = _build_narrative(items, thread)

        # Should include breakdown of significance counts
        assert "breaking" in narrative.lower() or "1 breaking" in narrative.lower()
        assert "major" in narrative.lower()
        assert "minor" in narrative.lower()

    def test_narrative_includes_item_count(self):
        from src.api.v1.threads import _build_narrative

        items = self._make_items()
        thread = self._make_thread(items)
        narrative = _build_narrative(items, thread)

        n = str(thread["item_count"])
        assert n in narrative

    def test_narrative_not_just_tag_list(self):
        from src.api.v1.threads import _build_narrative

        items = self._make_items()
        thread = self._make_thread(items)
        narrative = _build_narrative(items, thread)

        # Old format was "Thread of N items about tag1, tag2..."
        # New format should NOT start with "Thread of"
        assert not narrative.strip().startswith(
            "Thread of"
        ), f"Narrative should not start with 'Thread of'. Got:\n{narrative}"

    def test_narrative_with_empty_items(self):
        from src.api.v1.threads import _build_narrative

        thread = {
            "item_count": 0,
            "first_seen": datetime.now(timezone.utc),
            "last_seen": datetime.now(timezone.utc),
            "dominant_significance": "informational",
        }
        # Should not raise
        narrative = _build_narrative([], thread)
        assert isinstance(narrative, str)

    def test_narrative_truncates_long_summary(self):
        from src.api.v1.threads import _build_narrative

        long_summary = "X" * 300
        items = self._make_items(summaries=[long_summary, "Short.", "Short."])
        thread = self._make_thread(items)
        narrative = _build_narrative(items, thread)

        # Summary should be truncated (not full 300 chars in the lead)
        assert (
            len(narrative) < 600
        ), "Narrative should be concise, not include full 300-char summary verbatim"
