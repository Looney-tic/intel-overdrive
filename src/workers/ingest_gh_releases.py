"""GitHub Releases Atom ingestion worker: cron dispatcher + per-source job."""

import asyncio
from datetime import datetime, timezone

import feedparser
from sqlalchemy import select

import src.core.init_db as _db
from src.core.logger import get_logger
from src.workers.dms_worker import update_ingestion_heartbeat
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.feed_fetcher import fetch_feed_conditional
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)

logger = get_logger(__name__)


def enrich_release_title(source_name: str, raw_title: str) -> str:
    """Prepend source name to bare version-string titles for better embeddings.

    Transforms "1.11.0" -> "CrewAI Release 1.11.0"
    Leaves descriptive titles unchanged (heuristic: title has more than one word).
    """
    raw = raw_title.strip()
    if not raw or raw == "Untitled":
        return source_name or raw_title
    # If the title looks like a bare version string (only one word/token, possibly with v prefix)
    # heuristic: no spaces means it's a bare version like "1.11.0" or "v0.116.0"
    if " " not in raw:
        return f"{source_name} {raw}"
    return raw


async def poll_gh_releases_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active GitHub Releases sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "github-releases",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_gh_releases_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_gh_releases_sources_dispatched", count=len(sources))


async def ingest_gh_releases_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch, parse, deduplicate, and store GitHub Releases Atom entries.

    URL format: https://github.com/{owner}/{repo}/releases.atom — stored in Source.url.
    Unauthenticated for public repos (60 req/hr). Existing circuit breaker handles 403/429.
    Entries have link (release page URL), summary (HTML release notes), and unique Atom entry id.
    """
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

        try:
            content, new_etag, new_lm = await fetch_feed_conditional(
                source.url,
                source.last_etag,
                source.last_modified_header,
            )

            if content is None:
                # 304 Not Modified — success, no items to process
                await handle_source_success(session, source, new_etag, new_lm)
                logger.info("ingest_gh_releases_not_modified", source_id=source_id)
                return

            # Parse in thread to avoid blocking event loop
            parsed = await asyncio.to_thread(feedparser.parse, content)

            # Bozo check: reject only if no entries at all
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"bozo feed with no entries: {parsed.bozo_exception}")
            if parsed.bozo and parsed.entries:
                logger.warning(
                    "bozo_feed_with_entries",
                    source_id=source_id,
                    bozo_exception=str(parsed.bozo_exception),
                )

            dedup = DedupService(session)
            new_count = 0

            for entry in parsed.entries:
                # GitHub Releases Atom: link is the release page URL
                entry_url = entry.get("link", "")
                if not entry_url:
                    continue

                if await dedup.check_url_exists(entry_url):
                    continue

                url_hash = dedup._compute_url_hash(entry_url)
                # summary contains HTML release notes; fall back to title
                content_text = str(entry.get("summary") or entry.get("title") or "")
                content_hash = dedup._get_content_fingerprint(content_text)

                summary_text = str(entry.get("summary") or "")
                # entry.get("id") is the unique Atom entry ID per release
                external_id = str(entry.get("id", entry_url))
                published_parsed = entry.get("published_parsed") or entry.get(
                    "updated_parsed"
                )
                published_at = (
                    datetime(*published_parsed[:6], tzinfo=timezone.utc)
                    if published_parsed
                    else None
                )
                item = IntelItem(
                    source_id=source_id,
                    external_id=external_id,
                    url=entry_url,
                    url_hash=url_hash,
                    title=enrich_release_title(
                        source.name, str(entry.get("title", "Untitled"))
                    ),
                    content=summary_text,
                    excerpt=summary_text[:500] if summary_text else None,
                    primary_type="unknown",
                    tags=[],
                    status="raw",
                    content_hash=content_hash,
                    source_name=source.name,
                    published_at=published_at,
                )
                session.add(item)
                new_count += 1

            await session.commit()
            await handle_source_success(session, source, new_etag, new_lm)
            logger.info(
                "ingest_gh_releases_complete",
                source_id=source_id,
                new_items=new_count,
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
