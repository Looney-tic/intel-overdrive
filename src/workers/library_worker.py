"""
Library workers: synthesis, graduation, and staleness detection for the knowledge library.

Three cron-driven ARQ slow-queue workers:
  1. graduate_candidates:      Score intel_items and promote qualified items to library_items
  2. detect_stale_entries:     Flag stale library entries and archive abandoned reviews
  3. synthesize_library_topics: Haiku-synthesized topic guides for top topics

All workers run on SlowWorkerSettings (max_jobs=5) and are gated by SpendTracker.
"""

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import voyageai

import src.core.init_db as _db
from sqlalchemy import select, text, update
from src.core.config import get_settings
from src.core.logger import get_logger
from src.models.models import LibraryItem
from src.services.llm_client import LLMClient
from src.services.spend_tracker import SpendTracker, SpendLimitExceeded

logger = get_logger(__name__)

# Graduation thresholds
GRADUATION_SCORE_THRESHOLD = 15.0  # Path 1: signal-based promotion threshold
GRADUATION_MIN_AGE_DAYS = 7  # Items must be at least 7 days old
CANDIDATE_PROMOTION_SCORE = 8.0  # Candidates need >= 8 score to become active
CANDIDATE_MIN_AGE_DAYS = 3  # Candidates must be held 3+ days before promotion
FAST_TRACK_RELEVANCE = 0.85  # Path 2: tier1 source fast-track relevance threshold

# Staleness thresholds
STALENESS_DAYS = 180  # 180 days without confirmation -> review_needed
ARCHIVE_DAYS = 30  # 30 days in review_needed with no action -> archived

# Synthesis settings
TOP_N_TOPICS = 50  # Synthesize top N topics by item count
TOP_ITEMS_PER_TOPIC = 10  # Fetch top 10 items per topic for synthesis
RESYNTHESIS_GROWTH_THRESHOLD = 0.20  # Re-synthesize if source_item_count grew > 20%

# Acronym mapping for smart title-case (slug word -> proper casing)
ACRONYM_MAP = {
    "ai": "AI",
    "mcp": "MCP",
    "llm": "LLM",
    "sdk": "SDK",
    "api": "API",
    "cli": "CLI",
    "rag": "RAG",
    "ui": "UI",
    "ci": "CI",
    "cd": "CD",
    "vs": "vs",
    "ux": "UX",
    "openai": "OpenAI",
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "crewai": "CrewAI",
    "autogen": "AutoGen",
    "vscode": "VS Code",
}

# Source tier multipliers for graduation scoring
SOURCE_TIER_MULTIPLIERS = {
    "tier1": 1.5,
    "tier2": 1.0,
    "tier3": 0.7,
}

# Type multipliers for graduation scoring
TYPE_MULTIPLIERS = {
    "practice": 1.4,
    "docs": 1.3,
    "tool": 1.1,
    "skill": 1.1,
    "update": 0.6,
}

SYNTHESIS_SYSTEM_PROMPT = """You produce JSON. No markdown fences, no explanation, no text outside the JSON object.

Your task: synthesize a knowledge library entry about "{topic}" from the items below.

Items:
{item_summaries}

Required JSON structure:
{{
  "tldr": "One sentence. Start with an action verb. What should a developer DO about this topic.",
  "body": "2-3 paragraphs, plain prose, no markdown headers, max 250 words. Lead with actions.",
  "key_points": ["3-5 short actionable bullets"],
  "gotchas": [{{"title": "Short name", "detail": "One sentence: component + failure mode + fix."}}]
}}

Rules:
- Respond with ONLY the JSON object. No ```json fences. No preamble. First character must be {{.
- Max 2 gotchas. Keep gotcha details to ONE sentence each.
- key_points: max 5 items, each under 20 words.
- Ground claims in the items. No hallucinations.
- body max 250 words. Be concise."""


def _slugify(text: str) -> str:
    """Convert a topic string to a URL-safe slug."""
    slug = text.lower()
    slug = re.sub(r"[/\\]", "-", slug)
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _smart_title(slug: str) -> str:
    """Convert a slug to a properly-cased title using ACRONYM_MAP.

    Splits slug by '-', maps known acronyms/brand names to their proper
    casing, and capitalizes the rest. Handles '/' as ' -- ' separator.
    """
    parts = slug.replace("/", " -- ").split("-")
    result = []
    for word in parts:
        mapped = ACRONYM_MAP.get(word.lower())
        if mapped is not None:
            result.append(mapped)
        else:
            result.append(word.capitalize())
    return " ".join(result)


def _compute_graduation_score(
    upvotes: int,
    bookmarks: int,
    dismissals: int,
    source_tier: str,
    primary_type: str,
) -> float:
    """Compute graduation score using the multi-factor formula."""
    tier_mult = SOURCE_TIER_MULTIPLIERS.get(source_tier, 1.0)
    type_mult = TYPE_MULTIPLIERS.get(primary_type, 1.0)
    raw = upvotes * 3.0 + bookmarks * 2.0 - dismissals * 2.0
    return raw * tier_mult * type_mult


def _compute_confidence(source_item_count: int, source_count: int) -> str:
    """Assign confidence label from source diversity."""
    if source_item_count >= 10 and source_count >= 3:
        return "high"
    if source_item_count >= 5 or source_count >= 2:
        return "medium"
    return "low"


def _compute_content_hash(body: str, key_points: list) -> str:
    """SHA-256 of body + serialized key_points for ETag/304 support."""
    content = body + json.dumps(key_points, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


async def graduate_candidates(ctx: dict) -> None:
    """Score processed intel_items and promote qualified items to library_items.

    Path 1 (signal-based): graduation_score >= 15 AND age >= 7 days -> candidate
    Path 2 (source-type fast-track): tier1 + practice/docs + relevance >= 0.85 -> candidate
    Candidate promotion: candidates with score >= 8 AND age >= 3 days -> active

    Skips items already linked via source_item_ids (prevents double-promotion).
    """
    if _db.async_session_factory is None:
        logger.error("graduate_candidates_called_before_db_init")
        return

    now = datetime.now(timezone.utc)
    min_age_cutoff = now - timedelta(days=GRADUATION_MIN_AGE_DAYS)
    candidate_cutoff = now - timedelta(days=CANDIDATE_MIN_AGE_DAYS)

    async with _db.async_session_factory() as session:
        # Fetch all source_item_ids already in the library to avoid double-promotion
        existing_rows = await session.execute(
            text("SELECT source_item_ids FROM library_items WHERE is_current = TRUE")
        )
        already_linked: set[str] = set()
        for (ids,) in existing_rows:
            if ids:
                for item_id in ids:
                    already_linked.add(str(item_id))

        # Query processed intel_items with signal aggregates
        rows = await session.execute(
            text(
                """
                SELECT
                    i.id,
                    i.title,
                    i.summary,
                    i.primary_type,
                    i.relevance_score,
                    i.quality_score,
                    CAST(i.tags AS text) AS tags,
                    i.created_at,
                    s.tier,
                    COALESCE(SUM(CASE WHEN sig.action = 'upvote' THEN 1 ELSE 0 END), 0) AS upvotes,
                    COALESCE(SUM(CASE WHEN sig.action = 'bookmark' THEN 1 ELSE 0 END), 0) AS bookmarks,
                    COALESCE(SUM(CASE WHEN sig.action = 'dismiss' THEN 1 ELSE 0 END), 0) AS dismissals
                FROM intel_items i
                JOIN sources s ON s.id = i.source_id
                LEFT JOIN item_signals sig ON sig.item_id = i.id
                WHERE i.status = 'processed'
                AND i.created_at <= :min_age_cutoff
                GROUP BY i.id, i.title, i.summary, i.primary_type,
                         i.relevance_score, i.quality_score, CAST(i.tags AS text), i.created_at, s.tier
            """
            ),
            {"min_age_cutoff": min_age_cutoff},
        )

        new_candidates = 0
        for row in rows:
            item_id = str(row.id)
            if item_id in already_linked:
                continue

            score = _compute_graduation_score(
                upvotes=int(row.upvotes),
                bookmarks=int(row.bookmarks),
                dismissals=int(row.dismissals),
                source_tier=row.tier or "tier3",
                primary_type=row.primary_type or "docs",
            )

            # Path 2: tier1 + practice/docs + high relevance -> fast-track candidate
            is_fast_track = (
                row.tier == "tier1"
                and row.primary_type in ("practice", "docs")
                and (row.relevance_score or 0.0) >= FAST_TRACK_RELEVANCE
            )

            # Path 3: quality+relevance auto-graduation (no engagement signals needed)
            # High-quality items that have aged 14+ days and have strong relevance/quality
            # graduate automatically — solves empty library when user engagement is sparse.
            item_age_days = (
                (datetime.now(timezone.utc) - row.created_at).days
                if row.created_at
                else 0
            )
            is_quality_grad = (
                (row.relevance_score or 0.0) >= 0.70
                and (row.quality_score or 0.0) >= 0.60
                and item_age_days >= 14
                and row.primary_type in ("practice", "docs", "tool")
            )

            qualifies = (
                score >= GRADUATION_SCORE_THRESHOLD or is_fast_track or is_quality_grad
            )
            if not qualifies:
                continue

            # Derive topic from first tag or primary_type
            # row.tags is cast to text in SQL to avoid JSON equality operator issue in GROUP BY
            raw_tags = row.tags or "[]"
            tags: list = (
                json.loads(raw_tags) if isinstance(raw_tags, str) else (raw_tags or [])
            )
            topic = tags[0] if tags else row.primary_type or "general"
            slug = _slugify(f"{topic}-{row.title[:40]}")

            # Avoid slug collision by appending item_id prefix
            existing_slug = await session.execute(
                select(LibraryItem.id).where(LibraryItem.slug == slug)
            )
            if existing_slug.scalar():
                slug = f"{slug}-{item_id[:8]}"

            body = row.summary or row.title
            library_item = LibraryItem(
                id=uuid.uuid4(),
                slug=slug,
                title=row.title,
                body=body,
                key_points=[],
                gotchas=[],
                topic_path=topic,
                tags=tags,
                status="candidate",
                graduation_score=score,
                graduation_method="source_type" if is_fast_track else "signal",
                graduated_at=now,
                source_item_ids=[item_id],
                source_item_count=1,
                source_count=1,
                confidence=_compute_confidence(1, 1),
                content_hash=_compute_content_hash(body, []),
                agent_hint="Inject tldr + key_points into system prompt. Use gotchas as pre-task checklist.",
            )
            session.add(library_item)
            new_candidates += 1

        await session.commit()
        logger.info("graduate_candidates_new", count=new_candidates)

        # Generate embeddings for all newly-created candidate library items.
        # Runs after commit so items are persisted even if embedding fails.
        if new_candidates > 0:
            try:
                settings = get_settings()
                voyage_client = voyageai.AsyncClient()
                unembedded_result = await session.execute(
                    select(LibraryItem).where(
                        LibraryItem.embedding.is_(None),
                        LibraryItem.graduation_method.in_(["signal", "source_type"]),
                        LibraryItem.is_current.is_(True),
                    )
                )
                unembedded_items = unembedded_result.scalars().all()
                if unembedded_items:
                    embed_texts = [
                        f"{item.title}\n\n{item.body or ''}"
                        for item in unembedded_items
                    ]
                    embed_result = await voyage_client.embed(
                        embed_texts, model=settings.EMBEDDING_MODEL
                    )
                    for item, embedding in zip(
                        unembedded_items, embed_result.embeddings
                    ):
                        item.embedding = embedding
                    await session.commit()
                    logger.info(
                        "graduate_candidates_embedded", count=len(unembedded_items)
                    )
            except Exception as embed_exc:
                logger.warning(
                    "graduate_candidates_embed_failed",
                    error=str(embed_exc),
                    error_type=type(embed_exc).__name__,
                )

    # Second pass: promote qualified candidates to active
    async with _db.async_session_factory() as session:
        promoted = 0
        candidates = await session.execute(
            select(LibraryItem).where(
                LibraryItem.status == "candidate",
                LibraryItem.is_current.is_(True),
                LibraryItem.graduation_score >= CANDIDATE_PROMOTION_SCORE,
                LibraryItem.graduated_at <= candidate_cutoff,
            )
        )
        for (lib_item,) in candidates:
            lib_item.status = "active"
            promoted += 1

        await session.commit()
        logger.info("graduate_candidates_promoted", count=promoted)


async def detect_stale_entries(ctx: dict) -> None:
    """Flag stale library entries and archive those with no curator action.

    Time decay: active items without last_confirmed_at update in 180+ days -> review_needed
    Archive: entries in review_needed for 30+ days with no curator action -> archived
    Signal bump: read item_signals for source_item_ids to refresh last_confirmed_at
    """
    if _db.async_session_factory is None:
        logger.error("detect_stale_entries_called_before_db_init")
        return

    now = datetime.now(timezone.utc)
    staleness_cutoff = now - timedelta(days=STALENESS_DAYS)
    archive_cutoff = now - timedelta(days=ARCHIVE_DAYS)

    async with _db.async_session_factory() as session:
        # Bump last_confirmed_at for entries that have fresh signals on their source items
        # (upvote/bookmark on any source_item_id counts as confirmation)
        signal_rows = await session.execute(
            text(
                """
                SELECT DISTINCT li.id
                FROM library_items li
                JOIN item_signals sig
                    ON sig.item_id::text = ANY(
                        SELECT jsonb_array_elements_text(li.source_item_ids::jsonb)
                    )
                WHERE li.status = 'active'
                AND li.is_current = TRUE
                AND sig.action IN ('upvote', 'bookmark')
                AND sig.created_at > COALESCE(li.last_confirmed_at, li.graduated_at, li.created_at)
            """
            )
        )
        signal_ids = [row.id for row in signal_rows]
        if signal_ids:
            await session.execute(
                update(LibraryItem)
                .where(LibraryItem.id.in_(signal_ids))
                .values(last_confirmed_at=now)
            )
            logger.info("detect_stale_entries_confirmed", count=len(signal_ids))

        # Flag active entries that haven't been confirmed in 180+ days
        flagged = await session.execute(
            update(LibraryItem)
            .where(
                LibraryItem.status == "active",
                LibraryItem.is_current.is_(True),
                (LibraryItem.last_confirmed_at <= staleness_cutoff)
                | (
                    LibraryItem.last_confirmed_at.is_(None)
                    & (LibraryItem.graduated_at <= staleness_cutoff)
                ),
            )
            .values(status="review_needed")
            .returning(LibraryItem.id)
        )
        flagged_ids = flagged.fetchall()

        # Archive entries stuck in review_needed for 30+ days with no action
        archived = await session.execute(
            update(LibraryItem)
            .where(
                LibraryItem.status == "review_needed",
                LibraryItem.is_current.is_(True),
                LibraryItem.updated_at <= archive_cutoff,
            )
            .values(status="archived")
            .returning(LibraryItem.id)
        )
        archived_ids = archived.fetchall()

        await session.commit()
        logger.info(
            "detect_stale_entries_complete",
            flagged=len(flagged_ids),
            archived=len(archived_ids),
        )


async def synthesize_library_topics(ctx: dict) -> None:
    """Generate or refresh LLM-synthesized topic guides using the Batch API (50% cheaper).

    Phase 1: Collect all topics needing synthesis and their source items.
    Phase 2: Submit all prompts as a single Anthropic Batch API request.
    Phase 3: Process batch results and insert/update library_items.

    Note: Topics are discovered dynamically from processed intel_items — both
    primary_type values and tag frequency. RAG/embedding/vector-database topics
    are covered automatically when items with those tags reach 'processed' status.
    No hardcoded topic list is needed (confirmed phase 20-04).
    """
    if _db.async_session_factory is None:
        logger.error("synthesize_library_topics_called_before_db_init")
        return

    redis_client = ctx.get("redis")
    if redis_client is None:
        logger.error("synthesize_library_topics_no_redis")
        return

    spend_tracker = SpendTracker(redis_client)
    llm_client = LLMClient(spend_tracker)

    now = datetime.now(timezone.utc)

    # ── Phase 1: Collect topics needing synthesis ──────────────────────
    #
    # Topic discovery uses tag frequency analysis only.  Generic primary_type
    # values (tool, update, docs, practice, skill) are excluded because they
    # produce unhelpful top-level category entries instead of specific topic
    # guides.  Tag-based discovery already covers these types via more
    # specific tag values (e.g. "mcp", "claude-code", "ai-agents").

    # Generic primary_type names that should NOT become standalone topics
    _GENERIC_TYPE_NAMES = {"tool", "update", "docs", "practice", "skill"}

    async with _db.async_session_factory() as session:
        # Top topics by tag frequency (with source diversity for confidence)
        tag_rows = await session.execute(
            text(
                """
                SELECT
                    tag_value,
                    COUNT(*) AS tag_count,
                    COUNT(DISTINCT i.source_id) AS source_count
                FROM intel_items i,
                     jsonb_array_elements_text(i.tags::jsonb) AS tag_value
                WHERE i.status = 'processed'
                GROUP BY tag_value
                ORDER BY tag_count DESC
                LIMIT :limit
            """
            ),
            {"limit": TOP_N_TOPICS * 2},  # fetch extra to allow filtering
        )

        # Deduplicate and filter out generic primary_type names
        seen_topics: set[str] = set()
        topics: list[tuple[str, int, int]] = []
        for row in tag_rows:
            topic = row.tag_value
            if topic in seen_topics:
                continue
            if topic in _GENERIC_TYPE_NAMES:
                continue
            seen_topics.add(topic)
            topics.append((topic, row.tag_count, row.source_count or 1))
            if len(topics) >= TOP_N_TOPICS:
                break

    # Also include any manually seeded pending entries (e.g. cursor, copilot, aider)
    # These won't appear in tag frequency but need synthesis.
    async with _db.async_session_factory() as session:
        pending_rows = await session.execute(
            select(LibraryItem).where(
                LibraryItem.status == "pending",
                LibraryItem.is_current.is_(True),
            )
        )
        for entry in pending_rows.scalars().all():
            topic_name = entry.slug.replace("-", " ")
            if (
                entry.slug not in {t[0] for t in topics}
                and topic_name not in seen_topics
            ):
                # Use the slug as search term, estimate item count from tags
                topics.append((entry.slug, 0, 1))
                seen_topics.add(entry.slug)

    # Filter to topics that need (re)synthesis
    # Collect batch items and metadata in parallel
    batch_items: list[dict] = []  # for classify_batch
    topic_meta: dict[str, dict] = {}  # keyed by custom_id (slug)
    skipped = 0

    async with _db.async_session_factory() as session:
        for topic, item_count, source_count in topics:
            slug = _slugify(topic)

            existing_row = await session.execute(
                select(LibraryItem)
                .where(
                    LibraryItem.slug == slug,
                    LibraryItem.is_current.is_(True),
                    LibraryItem.graduation_method == "synthesis",
                )
                .limit(1)
            )
            existing: Optional[LibraryItem] = existing_row.scalar_one_or_none()

            if existing:
                growth = (item_count - existing.source_item_count) / max(
                    existing.source_item_count, 1
                )
                if growth <= RESYNTHESIS_GROWTH_THRESHOLD:
                    skipped += 1
                    continue

            # Fetch top items for this topic
            items_q = await session.execute(
                text(
                    """
                    SELECT i.id, i.title, i.summary, i.url, i.relevance_score, i.quality_score
                    FROM intel_items i
                    WHERE i.status = 'processed'
                    AND (i.primary_type = :topic
                         OR :topic = ANY(SELECT jsonb_array_elements_text(i.tags::jsonb)))
                    ORDER BY (i.relevance_score * 0.5 + i.quality_score * 0.5) DESC
                    LIMIT :limit
                """
                ),
                {"topic": topic, "limit": TOP_ITEMS_PER_TOPIC},
            )
            item_rows = items_q.fetchall()

            if not item_rows:
                logger.info("synthesize_library_topics_no_items", topic=topic)
                continue

            item_summaries = "\n".join(
                f"[{i+1}] {r.title}: {r.summary or '(no summary)'}"
                for i, r in enumerate(item_rows)
            )

            prompt = SYNTHESIS_SYSTEM_PROMPT.format(
                topic=topic,
                item_summaries=item_summaries,
            )

            batch_items.append(
                {
                    "custom_id": slug,
                    "content": f"Synthesize a knowledge library entry for topic: {topic}",
                }
            )
            topic_meta[slug] = {
                "topic": topic,
                "slug": slug,
                "item_count": item_count,
                "source_count": source_count,
                "source_item_ids": [str(r.id) for r in item_rows],
                "existing": existing,
                "system_prompt": prompt,
            }

    if not batch_items:
        logger.info("synthesize_library_topics_nothing_to_do", skipped=skipped)
        return

    # ── Phase 2: Submit batch ─────────────────────────────────────────

    try:
        await spend_tracker.check_spend_gate()
    except SpendLimitExceeded as e:
        logger.warning(
            "synthesize_library_topics_spend_gate",
            current=e.current,
            limit=e.limit,
        )
        return

    # classify_batch uses a single system_prompt for all items.
    # Since each topic has its own prompt (with different item_summaries),
    # we need to embed the full prompt in the content field instead.
    batch_requests = []
    for item in batch_items:
        meta = topic_meta[item["custom_id"]]
        batch_requests.append(
            {
                "custom_id": item["custom_id"],
                "content": meta["system_prompt"],  # full prompt as user message
            }
        )

    # Use a generic system prompt; the topic-specific prompt is in the content
    generic_system = (
        "You produce JSON. No markdown fences, no explanation. "
        "First character of your response must be {. Last character must be }."
    )

    logger.info(
        "synthesize_library_topics_batch_submit",
        count=len(batch_requests),
    )

    results = await llm_client.classify_batch(
        items=batch_requests,
        system_prompt=generic_system,
        redis_client=redis_client,
        poll_interval=15,
        max_wait=1800,  # 30 min for batch
    )

    if not results:
        logger.error("synthesize_library_topics_batch_empty")
        return

    # ── Phase 3: Process results and insert library items ─────────────

    synthesized = 0

    async with _db.async_session_factory() as session:
        for slug, llm_response in results.items():
            meta = topic_meta.get(slug)
            if not meta:
                logger.warning("synthesize_library_topics_unknown_slug", slug=slug)
                continue

            # Parse JSON — strip code fences if LLM still adds them
            raw_text = llm_response.raw_text.strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*\n?", "", raw_text)
                raw_text = re.sub(r"\n?```\s*$", "", raw_text)

            try:
                synthesized_data = json.loads(raw_text)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.error(
                    "synthesize_library_topics_parse_error",
                    topic=meta["topic"],
                    error=str(exc),
                    raw=raw_text[:200],
                )
                continue

            tldr = synthesized_data.get("tldr", "")
            body = synthesized_data.get("body", "")
            key_points = synthesized_data.get("key_points", [])
            gotchas = synthesized_data.get("gotchas", [])

            if not body:
                logger.warning(
                    "synthesize_library_topics_empty_body", topic=meta["topic"]
                )
                continue

            existing = meta["existing"]
            confidence = _compute_confidence(meta["item_count"], meta["source_count"])
            content_hash = _compute_content_hash(body, key_points)

            if existing:
                new_version = existing.version + 1
                await session.execute(
                    update(LibraryItem)
                    .where(LibraryItem.id == existing.id)
                    .values(is_current=False)
                )
            else:
                new_version = 1

            new_item = LibraryItem(
                id=uuid.uuid4(),
                slug=meta["slug"],
                title=_smart_title(meta["topic"]),
                tldr=tldr,
                body=body,
                key_points=key_points,
                gotchas=gotchas,
                topic_path=meta["topic"],
                tags=[meta["topic"]],
                status="active",
                graduation_score=float(meta["item_count"]),
                graduation_method="synthesis",
                graduated_at=now,
                last_confirmed_at=now,
                source_item_ids=meta["source_item_ids"],
                source_item_count=meta["item_count"],
                source_count=meta["source_count"],
                confidence=confidence,
                content_hash=content_hash,
                version=new_version,
                is_current=True,
                agent_hint=(
                    "Inject tldr + key_points into system prompt. "
                    "Use gotchas as pre-task checklist."
                ),
            )
            session.add(new_item)
            await session.commit()

            # Generate embedding for the new library item (P0-3).
            # Must happen AFTER commit so the item exists even if embedding fails.
            # Library item remains usable via ILIKE search without embedding.
            try:
                settings = get_settings()
                embed_text = f"{new_item.tldr}\n\n{new_item.body}"
                voyage_client = voyageai.AsyncClient()
                embed_result = await voyage_client.embed(
                    [embed_text], model=settings.EMBEDDING_MODEL
                )
                new_item.embedding = embed_result.embeddings[0]
                await session.commit()
                logger.info(
                    "library_item_embedded",
                    slug=meta["slug"],
                    topic=meta["topic"],
                )
            except Exception as embed_exc:
                logger.warning(
                    "library_item_embed_failed",
                    slug=meta["slug"],
                    topic=meta["topic"],
                    error=str(embed_exc),
                    error_type=type(embed_exc).__name__,
                    embed_text_len=len(embed_text),
                    has_voyage_key=bool(settings.VOYAGE_API_KEY),
                )

            synthesized += 1
            logger.info(
                "synthesize_library_topics_done",
                topic=meta["topic"],
                version=new_version,
                item_count=meta["item_count"],
                cost=llm_response.cost,
            )

    logger.info(
        "synthesize_library_topics_complete",
        synthesized=synthesized,
        skipped=skipped,
        batch_submitted=len(batch_requests),
        batch_returned=len(results),
    )
