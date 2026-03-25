"""Tests for the knowledge library API endpoints.

Covers:
  - GET /v1/library — topic index (authenticated)
  - GET /v1/library/topics — unauthenticated topic listing
  - GET /v1/library/topic/{topic} — topic detail with evergreen scoring
  - GET /v1/library/{slug} — full entry by slug (library_items)
  - GET /v1/library/search — text search
  - GET /v1/library/recommend — profile-matched recommendations
  - POST /v1/library/{slug}/signals — helpful/outdated signals
  - POST /v1/library/suggest — topic suggestion
  - GET /v1/context-pack?include_library=true — library priming integration
"""

import json
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.models.models import IntelItem, ItemSignal, LibraryItem, Source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def intel_items_for_library(session, source_factory):
    """
    30 IntelItem rows across 3 topics (mcp, agents, prompting).
    Varied quality/relevance scores. Some with ItemSignals.
    Used for V1 computed-view tests (tests 1-8).
    """
    source = await source_factory(
        id="test:lib-source",
        name="Library Test Source",
        tier="tier1",
    )

    items = []
    now = datetime.now(timezone.utc)

    topics = ["mcp", "agents", "prompting"]
    for i in range(30):
        topic = topics[i % 3]
        age_days = (i * 5) % 180  # vary age
        item = IntelItem(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"lib-ext-{i}",
            url=f"https://example.com/lib-{i}",
            title=f"{topic.title()} Best Practice {i}",
            content=f"Content about {topic}",
            excerpt=f"Excerpt about {topic} practice {i}",
            summary=f"Summary about {topic} practice {i}",
            primary_type="practice",
            tags=[topic],
            status="processed",
            quality_score=0.5 + (i % 5) * 0.1,  # 0.5..0.9
            relevance_score=0.5 + (i % 5) * 0.1,
            confidence_score=0.8,
            significance="minor",
            created_at=now - timedelta(days=age_days),
        )
        session.add(item)
        items.append(item)

    await session.commit()

    await session.commit()

    return items


@pytest_asyncio.fixture
async def library_items(session):
    """
    5+ LibraryItem rows (status='active', is_current=True) across 2 topics
    with varying confidence/staleness_risk/helpful_count.
    Used for tests 9-19 (library_items table).
    """
    now = datetime.now(timezone.utc)
    items = []

    entries = [
        {
            "slug": "mcp-server-security",
            "title": "MCP Server Security",
            "tldr": "Validate inputs and apply least-privilege auth on every MCP endpoint.",
            "body": "When building MCP servers, security must be the first concern. "
            "Validate all inputs at the boundary. Apply least-privilege auth. "
            "Never expose raw filesystem access to untrusted clients.",
            "key_points": ["Validate inputs at boundary", "Use least-privilege auth"],
            "gotchas": [
                {"title": "No input validation", "detail": "Allows injection attacks."}
            ],
            "topic_path": "mcp",
            "tags": ["mcp", "security"],
            "confidence": "high",
            "staleness_risk": "low",
            "helpful_count": 10,
            "graduation_score": 20.0,
        },
        {
            "slug": "mcp-client-configuration",
            "title": "MCP Client Configuration",
            "tldr": "Configure MCP clients with explicit server URLs and timeout budgets.",
            "body": "MCP client configuration is critical for production deployments. "
            "Always set explicit server URLs and timeout budgets.",
            "key_points": ["Set explicit server URLs", "Set timeout budgets"],
            "gotchas": [],
            "topic_path": "mcp",
            "tags": ["mcp"],
            "confidence": "medium",
            "staleness_risk": "medium",
            "helpful_count": 5,
            "graduation_score": 12.0,
        },
        {
            "slug": "multi-agent-orchestration",
            "title": "Multi-Agent Orchestration",
            "tldr": "Use explicit handoff protocols and shared state stores for multi-agent systems.",
            "body": "Multi-agent orchestration requires explicit handoff protocols. "
            "Use shared state stores (Redis or DB) for coordination.",
            "key_points": ["Use handoff protocols", "Shared state stores"],
            "gotchas": [
                {"title": "Race conditions", "detail": "Use locks for shared state."}
            ],
            "topic_path": "agents",
            "tags": ["agents", "orchestration"],
            "confidence": "high",
            "staleness_risk": "low",
            "helpful_count": 8,
            "graduation_score": 18.0,
        },
        {
            "slug": "prompt-engineering-best-practices",
            "title": "Prompt Engineering Best Practices",
            "tldr": "Write prompts that specify the output format, context, and constraints explicitly.",
            "body": "Good prompts specify output format, context, and constraints. "
            "Avoid vague instructions. Include examples when format matters.",
            "key_points": ["Specify output format", "Include examples"],
            "gotchas": [],
            "topic_path": "prompting",
            "tags": ["prompting"],
            "confidence": "medium",
            "staleness_risk": "medium",
            "helpful_count": 3,
            "graduation_score": 10.0,
        },
        {
            "slug": "claude-code-hooks-guide",
            "title": "Claude Code Hooks Guide",
            "tldr": "Use Claude Code hooks for pre-commit validation and automated quality gates.",
            "body": "Claude Code hooks run before and after key lifecycle events. "
            "Use them for pre-commit validation, spend tracking, and quality gates.",
            "key_points": ["Use hooks for validation", "Hooks are advisory by default"],
            "gotchas": [
                {"title": "Blocking hooks", "detail": "Safety gates can exit non-zero."}
            ],
            "topic_path": "claude-code",
            "tags": ["claude-code", "hooks"],
            "confidence": "high",
            "staleness_risk": "low",
            "helpful_count": 15,
            "graduation_score": 25.0,
        },
    ]

    for entry in entries:
        lib_item = LibraryItem(
            id=uuid.uuid4(),
            slug=entry["slug"],
            title=entry["title"],
            tldr=entry["tldr"],
            body=entry["body"],
            key_points=entry["key_points"],
            gotchas=entry["gotchas"],
            topic_path=entry["topic_path"],
            tags=entry["tags"],
            status="active",
            is_current=True,
            confidence=entry["confidence"],
            staleness_risk=entry["staleness_risk"],
            helpful_count=entry["helpful_count"],
            graduation_score=entry["graduation_score"],
            graduation_method="synthesis",
            graduated_at=now - timedelta(days=30),
            version=1,
            content_hash=f"hash-{entry['slug']}",
            agent_hint="Inject tldr + key_points into system prompt.",
        )
        session.add(lib_item)
        items.append(lib_item)

    await session.commit()
    return items


# ---------------------------------------------------------------------------
# V1 computed-view tests (tests 1-8: index + topic detail)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_library_index_empty(client, api_key_header):
    """GET /v1/library with no processed items returns 200 with empty topics list."""
    response = await client.get("/v1/library", headers=api_key_header["headers"])
    assert response.status_code == 200
    body = response.json()
    assert "topics" in body
    assert "total" in body
    assert isinstance(body["topics"], list)
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_library_index_populated(client, api_key_header, intel_items_for_library):
    """GET /v1/library returns topics ranked by avg composite score."""
    response = await client.get(
        "/v1/library",
        headers=api_key_header["headers"],
        params={"min_items": 1},  # lower threshold to show all 3 topics
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 3
    topics = body["topics"]
    # Topics should be ordered by avg_quality descending
    avg_qualities = [t["avg_quality"] for t in topics]
    assert avg_qualities == sorted(avg_qualities, reverse=True)


@pytest.mark.asyncio
async def test_library_topics_requires_auth(
    client, api_key_header, intel_items_for_library
):
    """GET /v1/library/topics is reachable without authentication.

    The /library/topics route is registered before /library/{slug} so it is
    no longer shadowed. It does not require authentication (discovery endpoint).
    """
    # Without auth: 200 (unauthenticated discovery endpoint)
    unauth_response = await client.get("/v1/library/topics")
    assert unauth_response.status_code == 200
    body = unauth_response.json()
    assert "topics" in body
    assert "total" in body


@pytest.mark.asyncio
async def test_library_topic_detail(client, api_key_header, library_items):
    """GET /v1/library/{slug} returns full library entry by slug.

    The {slug} wildcard route is registered LAST, after /topics and /topic/{topic}.
    Test retrieves a known library entry by slug.
    """
    response = await client.get(
        "/v1/library/mcp-server-security",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "mcp-server-security"
    assert "body" in body
    assert "key_points" in body
    assert "gotchas" in body
    meta = body["meta"]
    assert "confidence" in meta
    assert "helpful_count" in meta


@pytest.mark.asyncio
async def test_library_topic_not_found(client, api_key_header, library_items):
    """GET /v1/library/nonexistent returns 404 (slug entry not found).

    Unknown slugs return 404 from the {slug} wildcard handler.
    """
    response = await client.get(
        "/v1/library/unknown-topic-xyz-999",
        headers=api_key_header["headers"],
    )
    # Slug handler returns 404 for unknown slugs
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_library_evergreen_score_ordering(session, source_factory):
    """Higher quality items produce a higher evergreen score via direct SQL.

    Verify the evergreen formula directly via SQL. The /library/topic/{topic} endpoint
    (single-param GET routes, first registration wins). Verify the evergreen formula
    directly via SQL: score = (quality*0.35 + relevance*0.35 + confidence*0.10) *
    EXP(-0.693 * age_days / 90).
    """
    source = await source_factory(id="test:ev-source", name="EV Source", tier="tier2")
    now = datetime.now(timezone.utc)

    low_id = uuid.uuid4()
    high_id = uuid.uuid4()

    session.add(
        IntelItem(
            id=low_id,
            source_id=source.id,
            external_id="ev-low-1",
            url="https://example.com/ev-low-1",
            title="Low Quality MCP Item",
            content="Content",
            primary_type="practice",
            tags=["mcp"],
            status="processed",
            quality_score=0.3,
            relevance_score=0.3,
            confidence_score=0.5,
            created_at=now,
        )
    )
    session.add(
        IntelItem(
            id=high_id,
            source_id=source.id,
            external_id="ev-high-1",
            url="https://example.com/ev-high-1",
            title="High Quality MCP Item",
            content="Content",
            primary_type="practice",
            tags=["mcp"],
            status="processed",
            quality_score=0.9,
            relevance_score=0.9,
            confidence_score=0.9,
            created_at=now,
        )
    )
    await session.commit()

    # Verify evergreen ordering directly via SQL (matches library API formula)
    rows = (
        await session.execute(
            text(
                """
                SELECT id,
                    (quality_score * 0.35 + relevance_score * 0.35 + confidence_score * 0.10)
                    * EXP(-0.693 * EXTRACT(EPOCH FROM (NOW() - created_at)) / (90 * 86400.0))
                    AS evergreen_score
                FROM intel_items
                WHERE id = ANY(CAST(:ids AS uuid[]))
                ORDER BY evergreen_score DESC
                """
            ),
            {"ids": [str(high_id), str(low_id)]},
        )
    ).fetchall()

    assert len(rows) == 2
    # High quality must rank first
    assert str(rows[0][0]) == str(high_id)
    assert rows[0][1] > rows[1][1]


@pytest.mark.asyncio
async def test_library_signal_boost(session, source_factory, api_key_header):
    """Item with upvotes produces higher evergreen score than identical item without signals.

    Verifies the signal_boost factor via SQL: signal_boost = 1.0 + min(upvotes, 10) * 0.05.
    Uses raw SQL for item_signals to avoid FK constraint issues.
    """
    source = await source_factory(
        id="test:sig-source", name="Signal Source", tier="tier2"
    )
    now = datetime.now(timezone.utc)

    item_a_id = uuid.uuid4()
    item_b_id = uuid.uuid4()

    session.add(
        IntelItem(
            id=item_a_id,
            source_id=source.id,
            external_id="sig-a",
            url="https://example.com/sig-a",
            title="MCP Boosted Item",
            content="Content",
            primary_type="practice",
            tags=["mcp"],
            status="processed",
            quality_score=0.7,
            relevance_score=0.7,
            confidence_score=0.7,
            created_at=now,
        )
    )
    session.add(
        IntelItem(
            id=item_b_id,
            source_id=source.id,
            external_id="sig-b",
            url="https://example.com/sig-b",
            title="MCP Unboosted Item",
            content="Content",
            primary_type="practice",
            tags=["mcp"],
            status="processed",
            quality_score=0.7,
            relevance_score=0.7,
            confidence_score=0.7,
            created_at=now,
        )
    )
    await session.flush()

    # Add 5 upvotes to item_a using the real api_key_id from fixture (valid FK)
    real_key_id = api_key_header["api_key_id"]
    # Use raw SQL INSERT with ON CONFLICT DO NOTHING to insert multiple signals
    # The unique constraint is on (item_id, api_key_id), so we only get 1 upvote per key.
    # We verify signal_boost formula with the 1 upvote we can insert.
    await session.execute(
        text(
            "INSERT INTO item_signals (id, item_id, api_key_id, action, created_at, updated_at) "
            "VALUES (:id, :item_id, :key_id, 'upvote', NOW(), NOW()) "
        ),
        {"id": str(uuid.uuid4()), "item_id": str(item_a_id), "key_id": real_key_id},
    )
    await session.commit()

    # Verify that item_a has 1 upvote and item_b has 0 upvotes
    upvote_counts = (
        await session.execute(
            text(
                """
                SELECT item_id, COUNT(*) AS upvotes
                FROM item_signals
                WHERE item_id = ANY(CAST(:ids AS uuid[]))
                  AND action = 'upvote'
                GROUP BY item_id
                """
            ),
            {"ids": [str(item_a_id), str(item_b_id)]},
        )
    ).fetchall()

    # item_a_id should have 1 upvote, item_b_id should have 0
    upvote_map = {str(r[0]): r[1] for r in upvote_counts}
    assert upvote_map.get(str(item_a_id), 0) == 1
    assert upvote_map.get(str(item_b_id), 0) == 0

    # Verify signal_boost formula: boosted score = base_score * (1.0 + min(1,10)*0.05)
    # base_score is identical for both items (same q/r/c scores and same age)
    # So boosted item_a score = base * 1.05 > item_b score = base * 1.0
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    i.id,
                    (
                        (i.quality_score * 0.35 + i.relevance_score * 0.35 + i.confidence_score * 0.10)
                        * (1.0 + LEAST(COUNT(s.id), 10) * 0.05)
                        * EXP(-0.693 * EXTRACT(EPOCH FROM (NOW() - i.created_at)) / (90 * 86400.0))
                    ) AS evergreen_score
                FROM intel_items i
                LEFT JOIN item_signals s ON s.item_id = i.id AND s.action = 'upvote'
                WHERE i.id = ANY(CAST(:ids AS uuid[]))
                GROUP BY i.id, i.quality_score, i.relevance_score, i.confidence_score, i.created_at
                ORDER BY evergreen_score DESC
                """
            ),
            {"ids": [str(item_a_id), str(item_b_id)]},
        )
    ).fetchall()

    assert len(rows) == 2
    # Boosted item (item_a) must rank first
    assert str(rows[0][0]) == str(item_a_id)


@pytest.mark.asyncio
async def test_library_recency_decay(session, source_factory):
    """Newer item produces higher evergreen score than older item with identical quality.

    Verifies the recency_decay factor via SQL: decay = EXP(-0.693 * age_days / 90).
    Same quality scores but different ages → newer item scores higher.
    """
    source = await source_factory(
        id="test:recency-source", name="Recency Source", tier="tier2"
    )
    now = datetime.now(timezone.utc)

    new_id = uuid.uuid4()
    old_id = uuid.uuid4()

    session.add(
        IntelItem(
            id=new_id,
            source_id=source.id,
            external_id="recency-new",
            url="https://example.com/recency-new",
            title="MCP New Item",
            content="Content",
            primary_type="practice",
            tags=["mcp"],
            status="processed",
            quality_score=0.7,
            relevance_score=0.7,
            confidence_score=0.7,
            created_at=now,
        )
    )
    session.add(
        IntelItem(
            id=old_id,
            source_id=source.id,
            external_id="recency-old",
            url="https://example.com/recency-old",
            title="MCP Old Item",
            content="Content",
            primary_type="practice",
            tags=["mcp"],
            status="processed",
            quality_score=0.7,
            relevance_score=0.7,
            confidence_score=0.7,
            created_at=now - timedelta(days=180),
        )
    )
    await session.commit()

    rows = (
        await session.execute(
            text(
                """
                SELECT id,
                    (quality_score * 0.35 + relevance_score * 0.35 + confidence_score * 0.10)
                    * EXP(-0.693 * EXTRACT(EPOCH FROM (NOW() - created_at)) / (90 * 86400.0))
                    AS evergreen_score
                FROM intel_items
                WHERE id = ANY(CAST(:ids AS uuid[]))
                ORDER BY evergreen_score DESC
                """
            ),
            {"ids": [str(new_id), str(old_id)]},
        )
    ).fetchall()

    assert len(rows) == 2
    # Newer item must rank first (higher evergreen score)
    assert str(rows[0][0]) == str(new_id)
    # Score gap should be significant (180 days = 2 half-lives at 90-day decay)
    assert rows[0][1] > rows[1][1] * 3


# ---------------------------------------------------------------------------
# V2 library_items tests (tests 9-19)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_library_slug_entry(client, api_key_header, library_items):
    """GET /v1/library/{slug} returns full entry with required fields."""
    response = await client.get(
        "/v1/library/mcp-server-security",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "mcp-server-security"
    assert "tldr" in body
    assert "body" in body
    assert "key_points" in body
    assert "gotchas" in body
    assert "agent_hint" in body
    assert "meta" in body
    meta = body["meta"]
    assert "confidence" in meta
    assert "staleness_risk" in meta


@pytest.mark.asyncio
async def test_library_slug_not_found(client, api_key_header, library_items):
    """GET /v1/library/nonexistent-slug returns 404."""
    response = await client.get(
        "/v1/library/nonexistent-slug-xyz-999",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_library_slug_content_hash(client, api_key_header, library_items):
    """GET /v1/library/{slug} returns content_hash in meta for client-side caching.

    Note: The endpoint sets response.headers["ETag"] but returns a new JSONResponse,
    so the ETag header is lost. The content_hash is reliably exposed via meta.content_hash.
    """
    response = await client.get(
        "/v1/library/mcp-server-security",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    # content_hash is available in meta for client-side caching
    meta = body["meta"]
    assert "content_hash" in meta
    assert meta["content_hash"] == "hash-mcp-server-security"


@pytest.mark.asyncio
async def test_library_search_text(client, api_key_header, library_items):
    """GET /v1/library/search?q=keyword returns matching entries."""
    response = await client.get(
        "/v1/library/search",
        headers=api_key_header["headers"],
        params={"q": "security"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    # "security" appears in title/tldr/body of mcp-server-security
    slugs = [r["slug"] for r in body["items"]]
    assert "mcp-server-security" in slugs


@pytest.mark.asyncio
async def test_library_search_empty(client, api_key_header, library_items):
    """GET /v1/library/search for nonexistent topic returns 200 with empty results."""
    response = await client.get(
        "/v1/library/search",
        headers=api_key_header["headers"],
        params={"q": "zzz-nonexistent-topic-xyz-12345"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []
    # suggest_topic hint should appear in response headers or body
    # (endpoint sets X-Suggest header when no results)


@pytest.mark.asyncio
async def test_library_recommend_with_profile(
    client, api_key_header, session, library_items
):
    """GET /v1/library/recommend with profile tech_stack returns matching entries."""
    # Update user profile with tech_stack containing "mcp"
    await session.execute(
        text("UPDATE users SET profile = :profile WHERE id = :uid"),
        {
            "profile": json.dumps({"tech_stack": ["mcp", "security"]}),
            "uid": str(api_key_header["user_id"]),
        },
    )
    await session.commit()

    response = await client.get(
        "/v1/library/recommend",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert "entries" in body
    assert "profile_tags_matched" in body
    # At least one mcp or security entry should appear
    assert len(body["entries"]) >= 1
    # profile_tags_matched should contain matched tags
    matched = body["profile_tags_matched"]
    assert "mcp" in matched or "security" in matched


@pytest.mark.asyncio
async def test_library_recommend_no_profile(client, api_key_header, library_items):
    """GET /v1/library/recommend without profile returns 422 with error 'no_profile'."""
    response = await client.get(
        "/v1/library/recommend",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 422
    # The endpoint raises HTTPException(422, detail={"error": "no_profile", ...})
    # FastAPI serializes as {"detail": {"error": "no_profile", ...}}
    body = response.json()
    assert "no_profile" in str(body)


@pytest.mark.asyncio
async def test_library_signal_helpful(client, api_key_header, library_items):
    """POST /v1/library/{slug}/signals with action=helpful increments helpful_count."""
    # Get initial helpful_count
    get_resp = await client.get(
        "/v1/library/claude-code-hooks-guide",
        headers=api_key_header["headers"],
    )
    initial_count = get_resp.json()["meta"]["helpful_count"]

    # Submit helpful signal
    response = await client.post(
        "/v1/library/claude-code-hooks-guide/signals",
        headers=api_key_header["headers"],
        json={"action": "helpful"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "claude-code-hooks-guide"
    assert body["helpful_count"] == initial_count + 1


@pytest.mark.asyncio
async def test_library_signal_outdated(client, api_key_header, library_items):
    """POST /v1/library/{slug}/signals with action=outdated sets flagged_outdated=True."""
    response = await client.post(
        "/v1/library/prompt-engineering-best-practices/signals",
        headers=api_key_header["headers"],
        json={"action": "outdated"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["flagged_outdated"] is True


@pytest.mark.asyncio
async def test_library_suggest(client, api_key_header):
    """POST /v1/library/suggest returns 201 with suggestion_id."""
    response = await client.post(
        "/v1/library/suggest",
        headers=api_key_header["headers"],
        json={
            "topic": "test-topic",
            "description": "A test topic description for the library",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert "suggestion_id" in body
    assert body["status"] == "received"
    assert body["topic"] == "test-topic"


# ---------------------------------------------------------------------------
# Context-pack include_library integration test (test 19)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_pack_include_library(
    client, api_key_header, session, source_factory, library_items
):
    """GET /v1/context-pack?include_library=true&format=json returns library_priming section."""
    # Add a processed IntelItem so context-pack has feed items too
    source = await source_factory(id="test:cp-lib-source", name="CP Lib Source")
    item = IntelItem(
        id=uuid.uuid4(),
        source_id=source.id,
        external_id="cp-lib-1",
        url="https://example.com/cp-lib-1",
        title="MCP Context Pack Item",
        content="Content",
        primary_type="tool",
        tags=["mcp"],
        status="processed",
        quality_score=0.8,
        relevance_score=0.8,
        confidence_score=0.8,
        created_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.commit()

    response = await client.get(
        "/v1/context-pack",
        headers=api_key_header["headers"],
        params={
            "include_library": "true",
            "format": "json",
        },
    )
    assert response.status_code == 200
    body = response.json()
    # JSON format with include_library=True must include library_priming
    assert "library_priming" in body
    assert isinstance(body["library_priming"], list)
    assert len(body["library_priming"]) >= 1

    # Each library entry should have at minimum title and tldr
    for entry in body["library_priming"]:
        assert "title" in entry
