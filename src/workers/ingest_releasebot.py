"""Releasebot RSS adapter: cron dispatcher + per-source job.

Follows the exact pattern of ingest_rss.py. Releasebot aggregates release notes
from Anthropic, OpenAI, and GitHub via RSS/Atom feeds. The Source.url field stores
the feed URL configured at seed time — if Releasebot RSS proves unreachable, the
Source can be pointed at direct GitHub Releases Atom feeds for the same vendors
(e.g., https://github.com/anthropics/anthropic-sdk-python/releases.atom).
The adapter code itself is RSS-generic; only the Source.type filter distinguishes it.
"""

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


async def poll_releasebot_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active releasebot sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "releasebot",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_releasebot_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_releasebot_sources_dispatched", count=len(sources))


async def ingest_releasebot_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch, parse, deduplicate, and store Releasebot RSS entries."""
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
                logger.info("ingest_releasebot_not_modified", source_id=source_id)
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
                entry_url = entry.get("link", "")
                if not entry_url:
                    continue

                if await dedup.check_url_exists(entry_url):
                    continue

                url_hash = dedup._compute_url_hash(entry_url)
                content_text = str(entry.get("summary") or entry.get("title") or "")
                content_hash = dedup._get_content_fingerprint(content_text)

                summary_text = str(entry.get("summary") or "")
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
                    external_id=str(entry.get("id", entry_url)),
                    url=entry_url,
                    url_hash=url_hash,
                    title=str(entry.get("title", "Untitled")),
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
                "ingest_releasebot_complete", source_id=source_id, new_items=new_count
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
