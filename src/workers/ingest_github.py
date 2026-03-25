"""GitHub Search API ingestion worker: cron dispatcher + per-source job."""

from datetime import datetime
import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger
from src.workers.dms_worker import update_ingestion_heartbeat
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.feed_fetcher import fetch_github_search
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)

logger = get_logger(__name__)

DEFAULT_GITHUB_QUERIES = [
    "topic:claude-code",
    "topic:mcp topic:claude",
    "topic:claude-code-hooks",
    "topic:claude-workflow",
]

RELEVANT_TOPICS = {
    "claude-code",
    "mcp",
    "claude",
    "ai-agent",
    "llm",
    "ai-coding",
    "claude-code-hooks",
    "claude-workflow",
    "model-context-protocol",
    "agent",
    "ai-tools",
}


async def poll_github_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active GitHub sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "github",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_github_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_github_sources_dispatched", count=len(sources))


async def ingest_github_source(ctx: dict, source_id: str) -> None:
    """Per-source job: search GitHub API, deduplicate, and store repository items."""
    redis_client = ctx["redis"]

    if _db.async_session_factory is None:
        logger.error("db_not_initialized", source_id=source_id)
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(select(Source).where(Source.id == source_id))
        source = result.scalar_one_or_none()

        if source is None or not source.is_active:
            logger.info(
                "source_skipped", source_id=source_id, reason="not_found_or_inactive"
            )
            return

        if await is_source_on_cooldown(
            redis_client, source_id, source.poll_interval_seconds
        ):
            logger.info("source_on_cooldown", source_id=source_id)
            return

        github_token = get_settings().GITHUB_TOKEN
        queries = source.config.get("queries", DEFAULT_GITHUB_QUERIES)

        try:
            dedup = DedupService(session)
            new_count = 0
            promote_candidates: list[dict] = []

            for query in queries:
                try:
                    data, headers = await fetch_github_search(query, github_token)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (429, 403):
                        logger.warning(
                            "github_rate_limited",
                            source_id=source_id,
                            query=query,
                            status=exc.response.status_code,
                        )
                        break
                    raise

                # CRITICAL: process items FIRST, then check rate limit.
                # The API call already consumed a request; stored data is valid.
                for repo in data.get("items", []):
                    repo_url = repo["html_url"]

                    if await dedup.check_url_exists(repo_url):
                        continue

                    # Layer 2: content fingerprint dedup (cross-source duplicate detection)
                    description = repo.get("description", "")
                    content_text = (
                        (repo.get("full_name") or repo_url) + " " + (description or "")
                    )
                    if content_text.strip():
                        existing = await dedup.find_duplicate_by_content(content_text)
                        if existing:
                            logger.info(
                                "DEDUP_CONTENT_SKIP",
                                url=repo_url,
                                existing_id=str(existing.id),
                            )
                            continue

                    url_hash = dedup._compute_url_hash(repo_url)
                    content_hash = dedup._get_content_fingerprint(description or "")

                    created_at_str = repo.get("created_at")
                    published_at = (
                        datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        if created_at_str
                        else None
                    )
                    item = IntelItem(
                        source_id=source_id,
                        external_id=str(repo["id"]),
                        url=repo_url,
                        url_hash=url_hash,
                        title=repo.get("full_name") or repo_url,
                        content=description or "",
                        excerpt=description[:500] if description else None,
                        primary_type="unknown",
                        tags=repo.get("topics") or [],
                        status="raw",
                        content_hash=content_hash,
                        source_name=source.name,
                        published_at=published_at,
                    )
                    try:
                        async with session.begin_nested():
                            session.add(item)
                        new_count += 1
                    except IntegrityError:
                        # Race condition: concurrent source job already inserted this URL
                        logger.debug("github_duplicate_url_race", url=repo_url)
                        continue

                    # Collect auto-promote candidates
                    stars = repo.get("stargazers_count", 0)
                    topics = set(repo.get("topics") or [])
                    if stars > 50 and topics & RELEVANT_TOPICS:
                        promote_candidates.append(
                            {
                                "full_name": repo["full_name"],
                                "url": repo_url,
                                "stars": stars,
                            }
                        )

                # AFTER processing items, check remaining rate limit quota
                remaining = int(headers.get("x-ratelimit-remaining", "1"))
                if remaining <= 2:
                    logger.warning(
                        "github_rate_limit_approaching",
                        source_id=source_id,
                        remaining=remaining,
                    )
                    break

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_github_complete", source_id=source_id, new_items=new_count
            )

            # Auto-promote high-star repos with relevant topics to github-deep
            if promote_candidates:
                await _auto_promote_to_deep(session, promote_candidates)

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise


async def _auto_promote_to_deep(session, candidates: list[dict]) -> None:
    """Create github-deep Source rows for high-star repos with relevant topics.

    Runs after the main ingestion commit to keep promotion in a separate
    transaction. Uses check-then-insert with IntegrityError fallback to
    handle race conditions between concurrent queries.
    """
    promoted = 0
    for candidate in candidates:
        full_name = candidate["full_name"]
        repo_url = candidate["url"]
        stars = candidate["stars"]

        owner, repo = full_name.split("/", 1)
        deep_source_id = f"github-deep:{owner}/{repo}"

        # Check if already tracked
        result = await session.execute(
            text("SELECT 1 FROM sources WHERE id = :sid AND type = 'github-deep'"),
            {"sid": deep_source_id},
        )
        if result.scalar_one_or_none() is not None:
            continue

        new_source = Source(
            id=deep_source_id,
            name=f"{owner}/{repo} (deep, auto-promoted)",
            type="github-deep",
            url=repo_url,
            is_active=True,
            config={
                "star_milestones": [100, 500, 1000, 5000, 10000],
                "commit_burst_threshold": 20,
                "watched_files": ["CHANGELOG.md"],
                "auto_promoted": True,
                "promoted_at_stars": stars,
            },
            poll_interval_seconds=3600,
            tier="tier2",
        )

        try:
            async with session.begin_nested():
                session.add(new_source)
                await session.flush()
            promoted += 1
            logger.info(
                "auto_promoted_to_deep",
                repo=f"{owner}/{repo}",
                stars=stars,
            )
        except IntegrityError:
            # Race condition: another query already created this source.
            # Savepoint rolled back, session still usable — no full rollback needed.
            logger.debug(
                "auto_promote_already_exists",
                repo=f"{owner}/{repo}",
            )

    if promoted:
        await session.commit()
        logger.info("auto_promote_batch_complete", promoted=promoted)
