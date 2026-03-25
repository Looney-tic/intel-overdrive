"""Hacker News Algolia REST adapter: cron dispatcher + per-source job."""

from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

import src.core.init_db as _db
from src.core.logger import get_logger
from src.workers.dms_worker import update_ingestion_heartbeat
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.feed_fetcher import _USER_AGENT
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)

logger = get_logger(__name__)

_HN_ALGOLIA_BASE = "https://hn.algolia.com/api/v1/search_by_date"


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def fetch_hn_stories(
    query: str,
    since_ts: int,
    hits_per_page: int = 100,
) -> dict:
    """Fetch HN stories from Algolia search_by_date API.

    Retries on transient network errors only (TimeoutException, NetworkError).

    Returns:
        Parsed JSON dict from Algolia API.
    """
    params: dict[str, str | int] = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{since_ts}",
        "hitsPerPage": hits_per_page,
    }

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        response = await client.get(
            _HN_ALGOLIA_BASE,
            params=params,
            headers={"User-Agent": _USER_AGENT},
        )
        response.raise_for_status()
        return response.json()


async def poll_hn_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active HN sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "hn",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_hn_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_hn_sources_dispatched", count=len(sources))


async def ingest_hn_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch HN stories from Algolia, deduplicate, and store items."""
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

        query = source.config.get("query", "claude code")
        last_poll_ts = source.config.get("last_poll_ts", 0)

        try:
            data = await fetch_hn_stories(query, last_poll_ts)

            dedup = DedupService(session)
            new_count = 0
            max_created_at_i = last_poll_ts

            for hit in data.get("hits", []):
                # Track watermark BEFORE dedup — all returned hits advance
                # the timestamp regardless of whether they're duplicates
                created_at_i = hit.get("created_at_i", 0)
                if created_at_i > max_created_at_i:
                    max_created_at_i = created_at_i

                # Fallback URL for HN discussion items that have no external URL
                raw_url = hit.get("url")
                url = (
                    raw_url
                    if raw_url
                    else f"https://news.ycombinator.com/item?id={hit['objectID']}"
                )

                if await dedup.check_url_exists(url):
                    continue

                url_hash = dedup._compute_url_hash(url)
                story_text = str(hit.get("story_text") or hit.get("title") or "")
                content_hash = dedup._get_content_fingerprint(story_text)

                title = str(hit.get("title") or url)
                excerpt = story_text[:500] if story_text else None

                created_at_i = hit.get("created_at_i")
                published_at = (
                    datetime.fromtimestamp(created_at_i, tz=timezone.utc)
                    if created_at_i
                    else None
                )
                item = IntelItem(
                    source_id=source_id,
                    external_id=str(hit["objectID"]),
                    url=url,
                    url_hash=url_hash,
                    title=title,
                    content=story_text,
                    excerpt=excerpt,
                    primary_type="unknown",
                    tags=[],
                    status="raw",
                    content_hash=content_hash,
                    source_name=source.name,
                    published_at=published_at,
                )
                session.add(item)
                new_count += 1

            # CRITICAL: dict reassignment for SQLAlchemy JSON mutation detection
            if max_created_at_i > last_poll_ts:
                source.config = {**source.config, "last_poll_ts": max_created_at_i}

            await session.commit()
            await handle_source_success(session, source)
            logger.info("ingest_hn_complete", source_id=source_id, new_items=new_count)

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
