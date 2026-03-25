"""arXiv research paper ingestion worker: cron dispatcher + per-source job."""

import asyncio
from datetime import datetime, timezone

import feedparser
from sqlalchemy import select

import src.core.init_db as _db
from src.core.logger import get_logger
from src.workers.dms_worker import update_ingestion_heartbeat
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.feed_fetcher import fetch_arxiv_feed
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)

logger = get_logger(__name__)


async def poll_arxiv_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active arXiv sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "arxiv",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_arxiv_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_arxiv_sources_dispatched", count=len(sources))


async def ingest_arxiv_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch arXiv papers via Atom API, deduplicate, and store items."""
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

        queries: list[str] = source.config.get("queries", ["cat:cs.AI AND ti:agent"])

        try:
            dedup = DedupService(session)
            new_count = 0

            for idx, query in enumerate(queries):
                # arXiv rate limit: 3-second delay between requests (not before first)
                if idx > 0:
                    await asyncio.sleep(3)

                content = await fetch_arxiv_feed(query, max_results=50)

                # Parse Atom XML in thread to avoid blocking the event loop
                parsed = await asyncio.to_thread(feedparser.parse, content)

                for entry in parsed.entries:
                    # arXiv paper URL is the canonical ID (e.g. http://arxiv.org/abs/2501.12345v1)
                    url = str(entry.get("id", ""))
                    if not url:
                        continue

                    if await dedup.check_url_exists(url):
                        continue

                    url_hash = dedup._compute_url_hash(url)

                    # Abstract is available directly in the Atom feed (no PDF download)
                    abstract = str(entry.get("summary", ""))
                    content_hash = dedup._get_content_fingerprint(abstract)

                    # Strip newlines from arXiv titles (they often have line breaks)
                    title = (
                        str(entry.get("title", "Untitled")).replace("\n", " ").strip()
                    )

                    excerpt = abstract[:500] if abstract else None

                    published_parsed = entry.get("published_parsed") or entry.get(
                        "updated_parsed"
                    )
                    published_at = (
                        datetime(*published_parsed[:6], tzinfo=timezone.utc)
                        if published_parsed
                        else None
                    )

                    # Extract arXiv categories from entry tags
                    tags = [
                        t.get("term") for t in entry.get("tags", []) if t.get("term")
                    ]

                    item = IntelItem(
                        source_id=source_id,
                        external_id=url,
                        url=url,
                        url_hash=url_hash,
                        title=title,
                        content=abstract,
                        excerpt=excerpt,
                        primary_type="unknown",
                        tags=tags,
                        status="raw",
                        content_hash=content_hash,
                        source_name=source.name,
                        published_at=published_at,
                    )
                    session.add(item)
                    new_count += 1

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_arxiv_complete", source_id=source_id, new_items=new_count
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
