"""README refresh worker: fetches and updates READMEs for processed GitHub items.

Three modes, one worker:
  1. Backfill (aggressive): Fetch README + stars for processed items with short content.
     Runs hourly, 1000 items/run until backlog cleared.
  2. Star patrol (cheap): Re-check star counts for all processed GitHub items.
     Detects repos that took off or died. 500/run, daily.
  3. README refresh (targeted): Re-fetch README for high-star items (>=50) where
     content may have changed. 200/run, weekly.

Star cutoffs are tiered by category:
  - MCP servers, Claude Code skills/hooks: 0 (always fetch — core product)
  - Agent tools/frameworks: 2
  - Everything else: 10

Runs on SlowWorkerSettings.
"""
import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import text as sa_text

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger
from src.services.feed_fetcher import fetch_github_file_contents, fetch_github_repo_info

logger = get_logger(__name__)

# Batch sizes per mode
BACKFILL_BATCH_SIZE = (
    500  # ~1500 API calls per batch (3 per item), 6 batches/hr = safe under 5000/hr
)
STAR_PATROL_BATCH_SIZE = 500
REFRESH_BATCH_SIZE = 200

# Minimum stars for README refresh (not backfill — backfill uses tiered cutoffs)
REFRESH_MIN_STARS = 50

# Polite delay between GitHub API calls (seconds)
API_DELAY = 0.1  # GitHub allows 5000 req/hr with token (~1.4/sec); 0.1s is safe

# Core ecosystem tags — always fetch README regardless of stars
CORE_TAGS = {
    "mcp",
    "mcp-server",
    "mcp-client",
    "model-context-protocol",
    "claude-code",
    "claude-skills",
    "claude-hooks",
    "claude-workflow",
    "claude-code-hooks",
    "claude-code-skills",
}

# Agent ecosystem tags — low star bar
AGENT_TAGS = {
    "agent",
    "agentic",
    "multi-agent",
    "agent-framework",
    "llm-agent",
    "ai-agent",
    "autonomous-agent",
    "coding-agent",
}


def _parse_github_owner_repo(url: str) -> tuple[str, str] | None:
    """Extract owner/repo from a GitHub URL. Returns None if not parseable."""
    parsed = urlparse(url)
    if "github.com" not in (parsed.hostname or ""):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        return None
    # Skip non-repo paths like /blog, /topics, etc.
    if parts[1] in ("topics", "explore", "settings", "marketplace"):
        return None
    return parts[0], parts[1]


def _compute_content_hash(content: str) -> str:
    """SHA-256 hex digest of content (first 16 chars)."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _get_star_cutoff(tags: list[str], url: str) -> int:
    """Determine minimum stars based on item tags and URL."""
    tag_set = set(t.lower() for t in tags) if tags else set()
    url_lower = url.lower()

    # Core product — always fetch
    if tag_set & CORE_TAGS or "mcp" in url_lower:
        return 0

    # Agent ecosystem — low bar
    if tag_set & AGENT_TAGS:
        return 2

    # Everything else
    return 10


async def _fetch_readme(owner: str, repo: str, github_token: str) -> str | None:
    """Fetch README.md content, trying multiple filenames. Returns text or None."""
    for filename in ("README.md", "readme.md", "Readme.md"):
        data = await fetch_github_file_contents(owner, repo, filename, github_token)
        if data is not None:
            raw = base64.b64decode(data.get("content", "")).decode(
                "utf-8", errors="replace"
            )
            return raw[:8000]  # Cap at 8000 chars
    return None


# ---------------------------------------------------------------------------
# Mode 1: Backfill — fetch README for items with short content
# ---------------------------------------------------------------------------


BACKFILL_CONCURRENCY = 5  # Concurrent GitHub API requests
# Budget: 5000 req/hr shared across all workers. Reserve 2000 for other
# GitHub tasks (deep polling, star patrol, etc). That leaves ~3000/hr
# for backfill = ~500 items/batch (3 calls each) × 6 batches/hr = 3000 max.


async def backfill_readmes(ctx: dict) -> None:
    """Cron: fetch README + stars for processed GitHub items with short content.

    Targets items where content < 200 chars (just a GitHub description).
    Uses tiered star cutoffs. Processes items concurrently (10 at a time).
    """
    if _db.async_session_factory is None:
        logger.error("backfill_readmes_called_before_db_init")
        return

    settings = get_settings()
    github_token = settings.GITHUB_TOKEN
    if not github_token:
        logger.warning("backfill_readmes_no_github_token")
        return

    # Fetch the batch using its own short-lived session (SELECT only)
    async with _db.async_session_factory() as select_session:
        result = await select_session.execute(
            sa_text(
                """
                SELECT id, url, tags, content_hash
                FROM intel_items
                WHERE status = 'processed'
                  AND url LIKE '%%github.com%%'
                  AND LENGTH(COALESCE(content, '')) < 200
                ORDER BY relevance_score DESC
                LIMIT :batch_size
            """
            ),
            {"batch_size": BACKFILL_BATCH_SIZE},
        )
        rows = result.fetchall()

    if not rows:
        logger.info("backfill_readmes_nothing_to_do")
        return

    fetched = 0
    skipped_stars = 0
    skipped_no_readme = 0
    errors = 0
    sem = asyncio.Semaphore(BACKFILL_CONCURRENCY)

    # Abort mechanism: shared event + consecutive-error counter with lock.
    # When 3+ consecutive errors occur, all coroutines stop immediately.
    abort_event = asyncio.Event()
    consecutive_errors_lock = asyncio.Lock()
    consecutive_errors = 0

    async def process_one(row):
        nonlocal fetched, skipped_stars, skipped_no_readme, errors, consecutive_errors
        async with sem:
            # Check abort before doing any work
            if abort_event.is_set():
                return

            parsed = _parse_github_owner_repo(row.url)
            if not parsed:
                return

            owner, repo = parsed
            tags = (
                row.tags if isinstance(row.tags, list) else json.loads(row.tags or "[]")
            )

            try:
                repo_info = await fetch_github_repo_info(owner, repo, github_token)
                stars = repo_info.get("stargazers_count", 0)
                cutoff = _get_star_cutoff(tags, row.url)

                # Each coroutine uses its own session — AsyncSession is NOT
                # safe for concurrent use across coroutines.
                async with _db.async_session_factory() as item_session:
                    if stars < cutoff:
                        await item_session.execute(
                            sa_text(
                                "UPDATE intel_items SET updated_at = NOW() WHERE id = :id"
                            ),
                            {"id": str(row.id)},
                        )
                        await item_session.commit()
                        skipped_stars += 1
                        return

                    readme_text = await _fetch_readme(owner, repo, github_token)
                    if readme_text is None:
                        await item_session.execute(
                            sa_text(
                                "UPDATE intel_items SET updated_at = NOW() WHERE id = :id"
                            ),
                            {"id": str(row.id)},
                        )
                        await item_session.commit()
                        skipped_no_readme += 1
                        return

                    description = repo_info.get("description", "")
                    full_content = (
                        f"{description}\n\n{readme_text}"
                        if description
                        else readme_text
                    )
                    new_hash = _compute_content_hash(readme_text)

                    await item_session.execute(
                        sa_text(
                            """
                            UPDATE intel_items
                            SET content = :content,
                                content_hash = :hash,
                                status = 'embedded',
                                embedding = NULL,
                                updated_at = NOW()
                            WHERE id = :id
                        """
                        ),
                        {
                            "content": full_content,
                            "hash": new_hash,
                            "id": str(row.id),
                        },
                    )
                    await item_session.commit()
                    fetched += 1

                # Reset consecutive error counter on success
                async with consecutive_errors_lock:
                    consecutive_errors = 0

            except Exception as exc:
                errors += 1
                logger.warning(
                    "backfill_readme_error", url=row.url, error=str(exc)[:100]
                )
                # Track consecutive errors for cascade abort
                async with consecutive_errors_lock:
                    consecutive_errors += 1
                    if consecutive_errors >= 3 and not abort_event.is_set():
                        logger.warning(
                            "backfill_readmes_cascade_abort",
                            consecutive_errors=consecutive_errors,
                            reason="3+ consecutive errors — likely rate limited",
                        )
                        abort_event.set()

    await asyncio.gather(*[process_one(row) for row in rows])
    aborted = abort_event.is_set()
    logger.info(
        "backfill_readmes_complete",
        total=len(rows),
        fetched=fetched,
        skipped_low_stars=skipped_stars,
        skipped_no_readme=skipped_no_readme,
        errors=errors,
        aborted=aborted,
    )


# ---------------------------------------------------------------------------
# Mode 2: Star patrol — re-check star counts, detect rising/falling repos
# ---------------------------------------------------------------------------


async def star_patrol(ctx: dict) -> None:
    """Daily cron: re-check star counts for processed GitHub items.

    Updates quality_score_details with fresh star count. Detects repos that
    took off (may need README fetch) or died (may need downranking).
    """
    if _db.async_session_factory is None:
        logger.error("star_patrol_called_before_db_init")
        return

    settings = get_settings()
    github_token = settings.GITHUB_TOKEN
    if not github_token:
        logger.warning("star_patrol_no_github_token")
        return

    async with _db.async_session_factory() as session:
        # Check items with oldest star data first
        result = await session.execute(
            sa_text(
                """
                SELECT id, url, quality_score_details
                FROM intel_items
                WHERE status = 'processed'
                  AND url LIKE '%%github.com%%'
                ORDER BY
                    COALESCE(
                        (quality_score_details->>'last_tracked_at')::timestamptz,
                        '1970-01-01'::timestamptz
                    ) ASC
                LIMIT :batch_size
            """
            ),
            {"batch_size": STAR_PATROL_BATCH_SIZE},
        )
        rows = result.fetchall()

        if not rows:
            logger.info("star_patrol_nothing_to_do")
            return

        updated = 0
        errors = 0

        for row in rows:
            parsed = _parse_github_owner_repo(row.url)
            if not parsed:
                continue

            owner, repo = parsed

            try:
                repo_info = await fetch_github_repo_info(owner, repo, github_token)
                stars = repo_info.get("stargazers_count", 0)

                # Update quality_score_details with fresh star count
                details = row.quality_score_details or {}
                old_stars = details.get("stars", 0)
                details["stars"] = stars
                details["last_tracked_at"] = datetime.now(timezone.utc).isoformat()

                # Detect significant star changes
                if old_stars and stars >= old_stars * 3 and stars >= 50:
                    logger.info(
                        "star_patrol_repo_rising",
                        url=row.url,
                        old_stars=old_stars,
                        new_stars=stars,
                    )

                await session.execute(
                    sa_text(
                        """
                        UPDATE intel_items
                        SET quality_score_details = :details,
                            updated_at = NOW()
                        WHERE id = :id
                    """
                    ),
                    {
                        "details": json.dumps(details),
                        "id": str(row.id),
                    },
                )
                updated += 1

            except Exception as exc:
                errors += 1
                logger.debug("star_patrol_error", url=row.url, error=str(exc)[:80])

            await asyncio.sleep(API_DELAY)

        await session.commit()
        logger.info(
            "star_patrol_complete",
            total=len(rows),
            updated=updated,
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Mode 3: README refresh — re-fetch for high-value repos
# ---------------------------------------------------------------------------


async def refresh_readmes(ctx: dict) -> None:
    """Weekly cron: re-fetch READMEs for high-star processed items.

    Only targets repos with >= REFRESH_MIN_STARS that already have README content.
    Detects changes via content hash comparison.
    """
    if _db.async_session_factory is None:
        logger.error("refresh_readmes_called_before_db_init")
        return

    settings = get_settings()
    github_token = settings.GITHUB_TOKEN
    if not github_token:
        logger.warning("refresh_readmes_no_github_token")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            sa_text(
                """
                SELECT id, url, content_hash, tags
                FROM intel_items
                WHERE status = 'processed'
                  AND url LIKE '%%github.com%%'
                  AND LENGTH(COALESCE(content, '')) >= 200
                  AND COALESCE((quality_score_details->>'stars')::int, 0) >= :min_stars
                ORDER BY updated_at ASC
                LIMIT :batch_size
            """
            ),
            {"min_stars": REFRESH_MIN_STARS, "batch_size": REFRESH_BATCH_SIZE},
        )
        rows = result.fetchall()

        if not rows:
            logger.info("refresh_readmes_nothing_to_do")
            return

        refreshed = 0
        unchanged = 0
        errors = 0

        for row in rows:
            parsed = _parse_github_owner_repo(row.url)
            if not parsed:
                continue

            owner, repo = parsed

            try:
                readme_text = await _fetch_readme(owner, repo, github_token)
                if readme_text is None:
                    await asyncio.sleep(API_DELAY)
                    continue

                new_hash = _compute_content_hash(readme_text)
                if new_hash == (row.content_hash or ""):
                    # Touch updated_at so it goes to back of queue
                    await session.execute(
                        sa_text(
                            "UPDATE intel_items SET updated_at = NOW() WHERE id = :id"
                        ),
                        {"id": str(row.id)},
                    )
                    unchanged += 1
                    await asyncio.sleep(API_DELAY)
                    continue

                # README changed — update and re-process
                repo_info = await fetch_github_repo_info(owner, repo, github_token)
                description = repo_info.get("description", "")
                full_content = (
                    f"{description}\n\n{readme_text}" if description else readme_text
                )

                await session.execute(
                    sa_text(
                        """
                        UPDATE intel_items
                        SET content = :content,
                            content_hash = :hash,
                            status = 'embedded',
                            embedding = NULL,
                            updated_at = NOW()
                        WHERE id = :id
                    """
                    ),
                    {
                        "content": full_content,
                        "hash": new_hash,
                        "id": str(row.id),
                    },
                )
                refreshed += 1
                logger.info("readme_changed", url=row.url)

            except Exception as exc:
                errors += 1
                logger.warning(
                    "refresh_readme_error", url=row.url, error=str(exc)[:100]
                )

            await asyncio.sleep(API_DELAY)

        await session.commit()
        logger.info(
            "refresh_readmes_complete",
            total=len(rows),
            refreshed=refreshed,
            unchanged=unchanged,
            errors=errors,
        )
