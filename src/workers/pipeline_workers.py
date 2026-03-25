"""
Pipeline workers for the Intel item processing pipeline.

Three cron-driven ARQ slow-queue workers:
  1. embed_items:       raw → embedded (Voyage AI batch embeddings)
  2. gate_relevance:    embedded → queued | filtered (pgvector cosine gate + scoring)
  3. classify_items:    queued → processing → processed | failed (Haiku classification)

All workers are registered in SlowWorkerSettings.
Each worker reads _init_db.async_session_factory at call time (late-binding) to
avoid capturing None at module load.
"""

import json

import src.core.init_db as _init_db
from sqlalchemy import text
from src.services.pipeline_helpers import build_embed_input, safe_transition
from src.services.relevance_gate import compute_gate_score
from src.services.scoring_service import compute_relevance_score
from src.services.llm_client import LLMClient, APICreditsExhausted
from src.services.spend_tracker import SpendTracker, SpendLimitExceeded
from src.services.quality_service import (
    compute_heuristic_quality,
    parse_github_url,
    fetch_github_signals,
    compute_quality_subscores,
    compute_aggregate_quality,
)
from src.core.config import get_settings
from src.core.logger import get_logger
from src.services.slack_delivery import deliver_slack_alert

logger = get_logger(__name__)


async def _notify_breaking_items(updates: list[dict], item_map: dict) -> None:
    """Fire-and-forget Slack notification for items classified as 'breaking'.

    Called AFTER commit -- notification is a side-effect, not part of the
    transaction. Exceptions are caught and logged, never propagated.

    Args:
        updates: List of param dicts from classification (with 'id', 'significance',
                 'primary_type', 'tags' keys).
        item_map: Dict mapping custom_id -> tuple of item fields. Fields at
                  index 5=url, 6=title for main batch path; index 1=source_id.
    """
    settings = get_settings()
    webhook_url = settings.SLACK_WEBHOOK_URL
    if not webhook_url:
        return

    for upd in updates:
        if upd.get("significance") != "breaking":
            continue

        item_id_str = upd["id"]
        if item_id_str not in item_map:
            continue

        item_tuple = item_map[item_id_str]
        # item_map layout varies between resume and main paths.
        # Resume path: (item_id, source_id, excerpt)  -- 3 elements
        # Main path: (item_id, source_id, excerpt, source_tier, source_name, url, title, content, summary, tags) -- 10 elements
        if len(item_tuple) >= 7:
            url = item_tuple[5] or ""
            title = item_tuple[6] or "Untitled"
        else:
            # Resume path -- minimal info, use item_id as fallback
            url = ""
            title = f"Item {item_id_str}"

        primary_type = upd.get("primary_type", "unknown")
        tags_raw = upd.get("tags", "[]")
        try:
            tags = (
                json.loads(tags_raw) if isinstance(tags_raw, str) else (tags_raw or [])
            )
        except (json.JSONDecodeError, TypeError):
            tags = []

        try:
            await deliver_slack_alert(
                webhook_url=webhook_url,
                item_title=title,
                item_url=url,
                item_type=primary_type,
                urgency="critical",
                tags=tags if isinstance(tags, list) else [],
            )
            logger.info(
                "breaking_item_notified",
                item_id=item_id_str,
                title=title[:80],
            )
        except Exception as exc:
            logger.warning(
                "breaking_item_notify_failed",
                item_id=item_id_str,
                error=str(exc),
            )


# Batch sizes — Voyage and Batch API handle large batches efficiently
EMBED_BATCH_SIZE = 2000
GATE_BATCH_SIZE = 2000
CLASSIFY_BATCH_SIZE = 500  # Keep small enough to complete within ARQ 30-min timeout

# Pre-embed keyword noise filter — drops obviously irrelevant items before
# spending money on Voyage embeddings. These items would be filtered by the
# relevance gate anyway, but filtering here saves embedding API costs.
import re

_NOISE_PATTERNS = re.compile(
    r"(?i)"
    r"hiring|job opening|job posting|we're looking for|join our team|"
    r"apply now|careers at|open position|"
    r"sponsored content|sponsored post|advertisement|"
    r"raises \$|series [a-c] |funding round|"
    r"press release|media contact|for immediate release|"
    r"unsubscribe|view in browser|email preferences"
)

# Badge/shield URL patterns — scraped from READMEs, never useful content
_BADGE_URL_PATTERNS = re.compile(
    r"(?i)" r"^https?://img\.shields\.io/|" r"^https?://badge"
)


def is_noise(title: str, content: str, url: str = "") -> bool:
    """Return True if the item is obviously irrelevant based on keywords or URL."""
    if url and _BADGE_URL_PATTERNS.search(url):
        return True
    text = f"{title} {content[:500]}"
    return bool(_NOISE_PATTERNS.search(text))


# Classification taxonomy
VALID_PRIMARY_TYPES = {"skill", "tool", "update", "practice", "docs"}

# Fallback mapping for invalid LLM classification types → closest valid type.
# Instead of marking items as "failed", map to the best fit.
TYPE_FALLBACK_MAP = {
    "unknown": "docs",
    "other": "docs",
    "resource": "docs",
    "article": "docs",
    "blog": "docs",
    "post": "docs",
    "discussion": "docs",
    "tutorial": "docs",
    "guide": "docs",
    "reference": "docs",
    "news": "update",
    "release": "update",
    "announcement": "update",
    "changelog": "update",
    "library": "tool",
    "framework": "tool",
    "sdk": "tool",
    "package": "tool",
    "plugin": "tool",
    "extension": "tool",
    "pattern": "practice",
    "methodology": "practice",
    "technique": "practice",
    "workflow": "practice",
    "tip": "practice",
}

CLASSIFICATION_SYSTEM_PROMPT = """You are an AI coding ecosystem intelligence classifier for developers and AI agents who need to stay current on Claude Code, MCP, and agentic engineering.

Classify the given item and write an actionable summary that helps someone decide whether to integrate this into their workflow.

PRIMARY TYPES (choose exactly one):
- skill: A reusable Claude Code skill, hook, command, workflow, or prompt pattern
- tool: An MCP server, CLI tool, VS Code extension, or developer utility
- update: A model release, API change, deprecation, breaking change, or platform update
- practice: A methodology, technique, best practice, or architectural pattern — NOT questions, complaints, or status updates
- docs: Documentation, tutorial, guide, README, or reference material

TYPE GUIDANCE:
- A "practice" must describe a methodology, technique, or pattern that a developer can deliberately apply. Questions, complaints, anecdotal observations, and status updates are NOT practices — classify those as "docs" (if informational) or "update" (if about a platform change).

SIGNIFICANCE (choose one):
- breaking: Breaking change, security issue, or critical update requiring immediate action. Use sparingly — only for changes that demand immediate response.
- major: Introduces a genuinely NEW capability or significantly improves an existing workflow in a non-obvious way. A generic MCP wrapper around an existing API (Jira, Slack, GitHub, Contentful) is "minor" — it adds convenience but not a new capability. "major" should apply to roughly 10-15% of items, not the majority.
- minor: Incremental improvement, a wrapper around an existing service, small utility, or niche use case. Default for most MCP servers and tools.
- informational: Discussion, opinion, comparison, general awareness, or status update. Also use this for content that is clearly satirical, humorous, or a complaint rather than informational — regardless of topic.

SIGNIFICANCE GUIDANCE:
- Most items (70-80%) should be "minor" or "informational".
- "major" is reserved for novel capabilities: a new paradigm, a tool that meaningfully changes how you build agents, or a practice that is demonstrably better than the status quo.
- If content is clearly satirical, humorous, or a user complaint, always classify as "informational".

SOURCE TIER:
- Tier 1 (official): Anthropic, OpenAI, major framework releases. Higher baseline significance.
- Tier 2 (established): Well-known community tools, established blogs. Normal significance.
- Tier 3 (community): Random repos, individual blogs, social posts. Lower significance unless content is genuinely novel.
When source is Tier 1, consider upgrading significance by one level (e.g., minor → major) if the content introduces a new capability.

SUMMARY: Write 1-2 sentences that a developer can act on. Lead with what the item DOES (not what it IS), then state the concrete benefit or risk. Bad: 'A new MCP server for browser automation.' Good: 'Lets Claude Code control a browser via Playwright MCP — automate testing, scraping, and form-filling directly from chat. Supports headed/headless mode and screenshot capture.' The summary will be injected into agent system prompts, so every word must earn its tokens.

TAGS: Freeform lowercase strings, 1-5 tags. Prefer specific terms (e.g., "browser-automation" not just "tool").

IMPORTANT: 'breaking' means ONLY a backwards-incompatible API or SDK change requiring code modifications by consumers. Service outages, temporary incidents, security advisories, bug reports, news articles, and research papers are NEVER 'breaking' -- use 'major' or 'informational' for those. When in doubt, use 'informational'.

Return ONLY valid JSON with no markdown fences:
{"primary_type": "tool", "tags": ["mcp", "browser-automation"], "confidence": 0.9, "significance": "minor", "summary": "MCP server that wraps the Jira API, letting Claude Code create and query tickets without leaving the terminal."}"""


async def embed_items(ctx: dict) -> None:
    """Slow queue cron: embed raw IntelItems in batches.

    Status transition: raw → embedded.
    Uses SELECT FOR UPDATE SKIP LOCKED to prevent double-processing under concurrent
    cron invocations. Transaction is held open across the API call (batch is small,
    Voyage API is fast <2s).
    """
    if _init_db.async_session_factory is None:
        logger.error("embed_items_called_before_db_init")
        return

    settings = get_settings()
    redis_client = ctx["redis"]
    spend_tracker = SpendTracker(redis_client)
    llm_client = LLMClient(spend_tracker)

    async with _init_db.async_session_factory() as session:
        # SELECT FOR UPDATE SKIP LOCKED: prevents concurrent workers from processing
        # the same batch
        result = await session.execute(
            text(
                """
                SELECT id, title, content, url
                FROM intel_items
                WHERE status = 'raw'
                ORDER BY created_at ASC
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
            """
            ),
            {"batch_size": EMBED_BATCH_SIZE},
        )
        rows = result.fetchall()

        if not rows:
            return

        # Pre-embed noise filter: drop obviously irrelevant items (free, no API cost)
        noise_ids = []
        clean_rows = []
        for row in rows:
            if is_noise(row[1], row[2], row[3] or ""):
                noise_ids.append(row[0])
            else:
                clean_rows.append(row)

        if noise_ids:
            await session.execute(
                text("UPDATE intel_items SET status = 'filtered' WHERE id = ANY(:ids)"),
                {"ids": noise_ids},
            )
            await session.commit()
            logger.info("pre_embed_noise_filtered", count=len(noise_ids))

        if not clean_rows:
            return

        rows = clean_rows

        # P1-24: Empty content guard — skip embedding for items with no content
        embeddable_rows = []
        skipped_empty = 0
        for row in rows:
            title = row[1] or ""
            content = row[2] or ""
            if not title.strip() and not content.strip():
                # Filter out items with no content — they cannot be embedded or classified
                await session.execute(
                    text(
                        """
                        UPDATE intel_items
                        SET status = 'filtered',
                            updated_at = NOW()
                        WHERE id = CAST(:id AS uuid) AND status = 'raw'
                    """
                    ),
                    {"id": str(row[0])},
                )
                skipped_empty += 1
                logger.warning(
                    "embed_skip_empty_content",
                    item_id=str(row[0]),
                )
            else:
                embeddable_rows.append(row)

        if skipped_empty > 0:
            await session.commit()

        if not embeddable_rows:
            logger.info(
                "embed_batch_complete",
                total=len(rows) + len(noise_ids),
                embedded=0,
                skipped_empty=skipped_empty,
                failed=0,
            )
            return

        item_ids = [row[0] for row in embeddable_rows]
        texts = [build_embed_input(row[1], row[2]) for row in embeddable_rows]

        failed_count = 0
        try:
            embeddings = await llm_client.get_embeddings(texts)
        except SpendLimitExceeded:
            logger.warning(
                "embed_items_spend_gate_blocked",
                message="Spend limit exceeded; items remain raw for next run",
            )
            return
        except APICreditsExhausted as exc:
            logger.error(
                "EMBED_ITEMS_CREDITS_EXHAUSTED",
                provider=exc.provider,
                detail=exc.detail,
                message="API credits exhausted — embedding halted. Top up credits to resume.",
            )
            return
        except Exception as exc:
            failed_count = len(item_ids)
            logger.error(
                "embed_items_batch_failed",
                error=str(exc),
                count=failed_count,
            )
            embeddings = None

        embedded_count = 0
        if embeddings is not None:
            for item_id, embedding in zip(item_ids, embeddings):
                await session.execute(
                    text(
                        """
                        UPDATE intel_items
                        SET embedding = CAST(:emb AS vector),
                            embedding_model_version = :model,
                            status = 'embedded',
                            updated_at = NOW()
                        WHERE id = CAST(:id AS uuid) AND status = 'raw'
                    """
                    ),
                    {
                        "emb": str(embedding),
                        "model": settings.EMBEDDING_MODEL,
                        "id": str(item_id),
                    },
                )
                embedded_count += 1

            await session.commit()

    # P2-39: Embed batch summary and failure alerting
    total = len(embeddable_rows) + skipped_empty + len(noise_ids)
    log_level = "warning" if failed_count > 0 else "info"
    getattr(logger, log_level)(
        "embed_batch_complete",
        total=total,
        embedded=embedded_count,
        skipped_empty=skipped_empty,
        failed=failed_count,
    )

    # Alert on high failure rate (>10 failed items in a batch)
    if failed_count > 10:
        try:
            from src.services.slack_delivery import deliver_slack_alert

            slack_settings = get_settings()
            if slack_settings.SLACK_WEBHOOK_URL:
                await deliver_slack_alert(
                    webhook_url=slack_settings.SLACK_WEBHOOK_URL,
                    item_title=f"Embed worker: {failed_count} items failed embedding in last batch",
                    item_url="",
                    item_type="ops",
                    urgency="important",
                    tags=["ops", "embed-failure", "pipeline"],
                )
        except Exception as alert_exc:
            logger.error("embed_failure_alert_failed", error=str(alert_exc))


async def gate_relevance(ctx: dict) -> None:
    """Slow queue cron: route embedded items through the relevance gate.

    Status transition: embedded → queued (relevant) or filtered (noise).
    Computes and stores relevance_score via the 4-component scoring formula.
    """
    if _init_db.async_session_factory is None:
        logger.error("gate_relevance_called_before_db_init")
        return

    queued_count = 0
    filtered_count = 0
    source_cache: dict = {}  # source_id -> (tier, config) — avoid repeated queries

    async with _init_db.async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT id, embedding, source_id, created_at
                FROM intel_items
                WHERE status = 'embedded'
                  AND embedding IS NOT NULL
                ORDER BY created_at ASC
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
            """
            ),
            {"batch_size": GATE_BATCH_SIZE},
        )
        rows = result.fetchall()

        if not rows:
            return

        for row in rows:
            item_id = row[0]
            item_embedding = row[1]
            source_id = row[2]
            created_at = row[3]

            # Fetch source tier (cached to avoid N+1 queries)
            if source_id not in source_cache:
                src_result = await session.execute(
                    text("SELECT tier, config FROM sources WHERE id = :id"),
                    {"id": source_id},
                )
                src_row = src_result.fetchone()
                if src_row:
                    source_cache[source_id] = (src_row[0], src_row[1] or {})
                else:
                    source_cache[source_id] = ("tier3", {})

            source_tier, source_config = source_cache[source_id]

            # Per-source threshold override (stored in source.config['relevance_threshold'])
            # None means fall through to global RELEVANCE_THRESHOLD from Settings
            per_source_threshold = source_config.get("relevance_threshold")

            # Compute gate score (cosine similarity against reference set)
            gate_score, is_relevant = await compute_gate_score(
                session, item_embedding, threshold=per_source_threshold
            )

            # Compute composite relevance score
            relevance_score = compute_relevance_score(
                content_match=gate_score,
                source_tier=source_tier,
                metadata=source_config,
                published_at=created_at,
            )

            new_status = "queued" if is_relevant else "filtered"

            await session.execute(
                text(
                    """
                    UPDATE intel_items
                    SET status = :status,
                        relevance_score = :relevance_score,
                        updated_at = NOW()
                    WHERE id = CAST(:id AS uuid) AND status = 'embedded'
                """
                ),
                {
                    "status": new_status,
                    "relevance_score": relevance_score,
                    "id": str(item_id),
                },
            )

            if is_relevant:
                queued_count += 1
            else:
                filtered_count += 1

        await session.commit()

    logger.info(
        "gate_relevance_complete",
        queued=queued_count,
        filtered=filtered_count,
    )


async def classify_items(ctx: dict) -> None:
    """Slow queue cron: classify queued IntelItems via Haiku Batch API (50% cheaper).

    Uses the Anthropic Message Batches API to submit all queued items as a single
    batch, then polls for completion and applies results.

    Status transitions:
      queued → processing (before batch submission)
      processing → processed (successful classification)
      processing → failed (batch error or invalid primary_type)

    Includes a recovery step for items stuck in 'processing' >10 minutes (worker
    crash/restart scenario).
    """
    if _init_db.async_session_factory is None:
        logger.error("classify_items_called_before_db_init")
        return

    redis_client = ctx["redis"]
    spend_tracker = SpendTracker(redis_client)
    llm_client = LLMClient(spend_tracker)

    async with _init_db.async_session_factory() as session:
        # Recovery: items stuck in 'processing' for >10 minutes (worker crash).
        # IMPORTANT: Skip recovery if a batch poll is actively running — resetting
        # items while classify_batch is polling would cause duplicate submissions.
        active_batch_id = await redis_client.get("batch:active:classify")
        if active_batch_id is not None:
            # An active batch poll is in progress — resume it instead of creating a new batch
            active_batch_id_str = (
                active_batch_id.decode()
                if isinstance(active_batch_id, bytes)
                else active_batch_id
            )
            logger.info(
                "classify_items_resuming_active_batch", batch_id=active_batch_id_str
            )
            try:
                batch_results = await llm_client.poll_existing_batch(
                    active_batch_id_str, redis_client=redis_client
                )
            except Exception as exc:
                logger.error("classify_items_resume_batch_error", error=str(exc))
                return
            # Apply results from resumed batch (items are already in 'processing')
            # Fall through to the apply-results section below
            # We need to build item_map from 'processing' items in DB
            proc_result = await session.execute(
                text(
                    """
                    SELECT id, source_id, excerpt
                    FROM intel_items
                    WHERE status = 'processing'
                """
                ),
            )
            proc_rows = proc_result.fetchall()
            item_map = {str(row[0]): (row[0], row[1], row[2]) for row in proc_rows}
            # Jump to applying results
            classified_count = 0
            failed_count = 0
            total_cost = 0.0
            updates = []
            for custom_id, llm_result in batch_results.items():
                if custom_id not in item_map:
                    continue
                item_id, source_id, item_excerpt = item_map[custom_id]
                primary_type = llm_result.primary_type
                tags = llm_result.tags
                confidence = llm_result.confidence
                if primary_type not in VALID_PRIMARY_TYPES:
                    mapped = TYPE_FALLBACK_MAP.get(
                        primary_type.lower() if primary_type else "", "docs"
                    )
                    logger.info(
                        "classify_type_fallback",
                        original=primary_type,
                        mapped=mapped,
                        item_id=str(item_id),
                    )
                    primary_type = mapped
                summary = llm_result.summary
                if not summary or summary.strip() == "":
                    summary = (
                        "[Summary unavailable — classification returned empty summary]"
                    )
                significance = llm_result.significance
                # Issue #26: normalize legacy 'breaking-change' to canonical 'breaking'
                if significance == "breaking-change":
                    significance = "breaking"
                updates.append(
                    {
                        "primary_type": primary_type,
                        "tags": json.dumps(tags) if isinstance(tags, list) else tags,
                        "confidence_score": confidence,
                        "summary": summary,
                        "significance": significance,
                        "id": str(item_id),
                    }
                )
                classified_count += 1
                total_cost += llm_result.cost
            if updates:
                for upd in updates:
                    await session.execute(
                        text(
                            """
                            UPDATE intel_items
                            SET primary_type = :primary_type,
                                tags = CAST(:tags AS json),
                                confidence_score = :confidence_score,
                                summary = :summary,
                                significance = :significance,
                                status = 'processed',
                                updated_at = NOW()
                            WHERE id = CAST(:id AS uuid) AND status = 'processing'
                        """
                        ),
                        upd,
                    )
                await session.commit()
                # Breaking change instant notification (after commit, fire-and-forget)
                await _notify_breaking_items(updates, item_map)
            # Note: spend is already tracked inside poll_existing_batch() —
            # do NOT call spend_tracker.track_spend() again here.
            logger.info(
                "classify_items_complete",
                classified=classified_count,
                failed=failed_count,
                total_cost_usd=round(total_cost, 6),
            )
            return

        stuck_result = await session.execute(
            text(
                """
                UPDATE intel_items
                SET status = 'queued',
                    updated_at = NOW()
                WHERE status = 'processing'
                  AND updated_at < NOW() - INTERVAL '10 minutes'
                RETURNING id
            """
            ),
        )
        stuck_ids = stuck_result.fetchall()
        if stuck_ids:
            await session.commit()
            logger.info("classify_items_recovered_stuck", count=len(stuck_ids))
            # Return immediately — let the NEXT cron run pick up the reset items.
            # Without this, the same run re-fetches all reset items, submits them
            # as a huge batch, exceeds the 30-min ARQ timeout, and the cycle repeats.
            return

        # Pre-flight spend check
        try:
            await spend_tracker.check_spend_gate()
        except SpendLimitExceeded:
            logger.warning(
                "classify_items_spend_gate_blocked",
                message="Spend limit reached; skipping classification run",
            )
            return

        # Fetch queued items (with FOR UPDATE SKIP LOCKED)
        # Also JOIN sources to get source tier and name for classification context.
        result = await session.execute(
            text(
                """
                SELECT i.id, i.title, i.content, i.excerpt, i.source_id,
                       s.tier AS source_tier, s.name AS source_name_db,
                       i.url, i.summary, i.tags
                FROM intel_items i
                LEFT JOIN sources s ON s.id = i.source_id
                WHERE i.status = 'queued'
                ORDER BY i.created_at ASC
                LIMIT :batch_size
                FOR UPDATE OF i SKIP LOCKED
            """
            ),
            {"batch_size": CLASSIFY_BATCH_SIZE},
        )
        rows = result.fetchall()

        if not rows:
            return

        # Transition all to 'processing' before batch submission
        item_map = (
            {}
        )  # custom_id → (item_id, source_id, excerpt, source_tier, source_name, url, title, content, summary, tags)
        batch_items = []

        for row in rows:
            item_id = row[0]
            title = row[1]
            content = row[2]
            excerpt = row[3]
            source_id = row[4]
            source_tier = row[5] or "tier2"
            source_name = row[6] or "unknown"
            url = row[7] or ""
            summary = row[8] or ""
            tags_raw = row[9]

            transitioned = await safe_transition(
                session, str(item_id), "queued", "processing"
            )
            if not transitioned:
                continue

            custom_id = str(item_id)
            item_map[custom_id] = (
                item_id,
                source_id,
                excerpt,
                source_tier,
                source_name,
                url,
                title,
                content,
                summary,
                tags_raw,
            )
            batch_items.append(
                {
                    "custom_id": custom_id,
                    "content": (
                        f"Title: {title}\n"
                        f"Source: {source_name} (Tier: {source_tier})\n"
                        f"Excerpt: {excerpt or ''}\n"
                        f"Content: {content[:4000]}"
                    ),
                }
            )

        if not batch_items:
            return

        # Commit all 'processing' transitions before batch submission
        await session.commit()

        # Submit batch to Anthropic Batch API (50% cheaper than real-time)
        # Pass redis_client so classify_batch can store batch.id before polling.
        try:
            batch_results = await llm_client.classify_batch(
                batch_items, CLASSIFICATION_SYSTEM_PROMPT, redis_client=redis_client
            )
        except SpendLimitExceeded:
            logger.warning(
                "classify_items_spend_limit_pre_batch",
                message="Spend limit hit before batch submission",
            )
            # Transition items back to queued to prevent cycling:
            # Without this, items stay in 'processing', get reset by the recovery
            # step on the next run, resubmitted, and hit the spend limit again
            # in an infinite loop.
            for custom_id, (item_id, source_id, excerpt, *_) in item_map.items():
                await safe_transition(session, str(item_id), "processing", "queued")
            await session.commit()
            return
        except APICreditsExhausted as exc:
            logger.error(
                "CLASSIFY_ITEMS_CREDITS_EXHAUSTED",
                provider=exc.provider,
                detail=exc.detail,
                message="API credits exhausted — classification halted. Top up credits to resume.",
            )
            # Revert items from 'processing' back to 'queued' so they can be
            # retried once credits are restored (prevents stuck cycling).
            for custom_id, (item_id, source_id, excerpt, *_) in item_map.items():
                await safe_transition(session, str(item_id), "processing", "queued")
            await session.commit()
            # Surface credit exhaustion to /v1/sla and /v1/status (24h TTL)
            if redis_client:
                await redis_client.set("credits:exhausted", "1", ex=86400)
            return
        except Exception as exc:
            logger.error("classify_items_batch_error", error=str(exc))
            # Leave items in 'processing' — recovery step handles next run
            return

        # Apply batch results in a SINGLE transaction (not per-item commits).
        # Accumulate all updates first, then commit once to avoid half-committed batches.
        classified_count = 0
        failed_count = 0
        total_cost = 0.0
        success_updates = []  # list of param dicts for bulk UPDATE
        failed_item_ids = []  # item IDs to transition to 'failed'
        item_log_data = []  # (item_id, source_id, cost) for post-commit logging

        for custom_id, llm_result in batch_results.items():
            item_id, source_id, item_excerpt, *_ = item_map[custom_id]

            primary_type = llm_result.primary_type
            tags = llm_result.tags
            confidence = llm_result.confidence

            # Validate primary_type against taxonomy; map invalid types via TYPE_FALLBACK_MAP
            # (same logic as the resume path) instead of failing items permanently
            if primary_type not in VALID_PRIMARY_TYPES:
                mapped = TYPE_FALLBACK_MAP.get(
                    primary_type.lower() if primary_type else "", "docs"
                )
                logger.info(
                    "classify_type_fallback",
                    original=primary_type,
                    mapped=mapped,
                    item_id=str(item_id),
                )
                primary_type = mapped

            # Ensure summary is never null on processed items.
            # Use a clear marker instead of raw excerpt to avoid "Click here to read more..."
            # appearing as summaries, which looks unprocessed to users.
            summary = llm_result.summary
            if not summary or summary.strip() == "":
                summary = (
                    "[Summary unavailable — classification returned empty summary]"
                )
            significance = llm_result.significance
            # Issue #26: normalize legacy 'breaking-change' to canonical 'breaking'
            if significance == "breaking-change":
                significance = "breaking"

            success_updates.append(
                {
                    "primary_type": primary_type,
                    "tags": json.dumps(tags) if isinstance(tags, list) else tags,
                    "confidence_score": confidence,
                    "summary": summary,
                    "significance": significance,
                    "id": str(item_id),
                }
            )
            item_log_data.append((str(item_id), str(source_id), llm_result.cost))
            classified_count += 1
            total_cost += llm_result.cost

        # Mark items that weren't in batch results as failed
        for custom_id, (item_id, source_id, _excerpt, *_rest) in item_map.items():
            if custom_id not in batch_results:
                failed_item_ids.append(str(item_id))
                failed_count += 1

        # Execute all UPDATEs then commit once
        for upd in success_updates:
            await session.execute(
                text(
                    """
                    UPDATE intel_items
                    SET primary_type = :primary_type,
                        tags = CAST(:tags AS json),
                        confidence_score = :confidence_score,
                        summary = :summary,
                        significance = :significance,
                        status = 'processed',
                        updated_at = NOW()
                    WHERE id = CAST(:id AS uuid) AND status = 'processing'
                """
                ),
                upd,
            )
        # --- Inline quality scoring (P0: items must not be visible with quality=0.0) ---
        # Score every successfully classified item BEFORE commit so they never
        # appear with quality_score=0.0 in feed/search results.
        settings = get_settings()
        rate_limited = False

        for upd in success_updates:
            item_id_str = upd["id"]
            custom_id = item_id_str  # custom_id == str(item_id)
            if custom_id not in item_map:
                continue

            (
                _,
                _,
                _,
                source_tier,
                _,
                url,
                title,
                content,
                summary_raw,
                tags_raw,
            ) = item_map[custom_id]

            # Prefer the classified summary/tags over the raw item values
            classified_summary = upd.get("summary", summary_raw)
            classified_tags = upd.get("tags", tags_raw)

            is_github = url and "github.com" in url

            if is_github and not rate_limited:
                parsed = parse_github_url(url)
                if parsed:
                    signals = await fetch_github_signals(
                        parsed[0], parsed[1], settings.GITHUB_TOKEN
                    )
                    if signals and signals.get("rate_limited"):
                        rate_limited = True
                        # Fall through to heuristic below
                    elif signals:
                        subscores = compute_quality_subscores(signals, content)
                        quality_score = compute_aggregate_quality(subscores)
                        details = json.dumps(subscores)
                        await session.execute(
                            text(
                                "UPDATE intel_items SET quality_score = :qs, quality_score_details = CAST(:details AS json) WHERE id = CAST(:id AS uuid)"
                            ),
                            {
                                "qs": quality_score,
                                "details": details,
                                "id": item_id_str,
                            },
                        )
                        continue

            # Heuristic path: non-GitHub items, rate-limited GitHub, or parse failures
            quality_score, quality_details = compute_heuristic_quality(
                source_tier, content, classified_summary, classified_tags, title
            )
            await session.execute(
                text(
                    "UPDATE intel_items SET quality_score = :qs, quality_score_details = CAST(:details AS json) WHERE id = CAST(:id AS uuid)"
                ),
                {
                    "qs": quality_score,
                    "details": json.dumps(quality_details),
                    "id": item_id_str,
                },
            )

        for failed_id in failed_item_ids:
            await safe_transition(session, failed_id, "processing", "failed")

        if success_updates or failed_item_ids:
            await session.commit()

        # Breaking change instant notification (after commit, fire-and-forget)
        if success_updates:
            await _notify_breaking_items(success_updates, item_map)

        # Per-item success log after commit (OPS-04)
        spend_remaining = await spend_tracker.get_remaining_spend()
        for log_item_id, log_source_id, log_cost in item_log_data:
            logger.info(
                "ITEM_CLASSIFIED",
                item_id=log_item_id,
                source=log_source_id,
                cost=log_cost,
                spend_remaining=round(spend_remaining, 4),
            )

    logger.info(
        "classify_items_complete",
        classified=classified_count,
        failed=failed_count,
        total_cost_usd=round(total_cost, 6),
    )
