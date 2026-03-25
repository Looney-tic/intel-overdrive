"""Bluesky ingest worker.

Polls Bluesky account feeds and keyword searches using the atproto SDK.
Session string is cached in Redis (20hr TTL) to stay below the 300/day
createSession rate limit. The on_session_change callback updates Redis
whenever the SDK auto-refreshes the access token.

Ported from geo-dashboard/backend/app/workers/ingest_bluesky.py.
Adapted to overdrive-intel patterns (SQLAlchemy, IntelItem, DedupService).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)
from src.workers.dms_worker import update_ingestion_heartbeat
from sqlalchemy import select

logger = get_logger(__name__)

# --- Constants ----------------------------------------------------------------

BLUESKY_SESSION_REDIS_KEY = "bluesky:session_string"
BLUESKY_SESSION_TTL_SECONDS = 72000  # 20 hours


# --- Session management -------------------------------------------------------


async def get_or_create_bluesky_client(redis_client: Any) -> Any:
    """Return authenticated AsyncClient, reusing cached session when available.

    Session caching strategy:
    - Check Redis for a cached session string first
    - If found, restore via login(session_string=...)
    - If not found, do a fresh login(handle, password) and cache the result
    - on_session_change callback persists refreshed tokens back to Redis
    """
    from atproto import AsyncClient, Session, SessionEvent

    settings = get_settings()
    client = AsyncClient()

    async def on_session_change(event: SessionEvent, session: Session) -> None:
        """Persist refreshed session string back to Redis on every token refresh."""
        if event in (SessionEvent.CREATE, SessionEvent.REFRESH):
            updated_string = client.export_session_string()
            await redis_client.set(
                BLUESKY_SESSION_REDIS_KEY,
                updated_string,
                ex=BLUESKY_SESSION_TTL_SECONDS,
            )

    client.on_session_change(on_session_change)

    cached = await redis_client.get(BLUESKY_SESSION_REDIS_KEY)
    if cached:
        # Redis may return bytes or str depending on client config;
        # atproto login expects str
        cached_string = cached if isinstance(cached, str) else cached.decode("utf-8")
        await client.login(session_string=cached_string)
        logger.debug("bluesky_session_restored_from_redis")
    else:
        await client.login(settings.BLUESKY_HANDLE, settings.BLUESKY_APP_PASSWORD)
        session_string = client.export_session_string()
        await redis_client.set(
            BLUESKY_SESSION_REDIS_KEY,
            session_string,
            ex=BLUESKY_SESSION_TTL_SECONDS,
        )
        logger.info("bluesky_session_created_fresh")

    return client


# --- URL helpers --------------------------------------------------------------


def is_keyword_search_source(source: Source) -> bool:
    """Check if a Bluesky source is a keyword search (vs. account feed)."""
    return "bsky.app/search" in source.url


def extract_bsky_handle(source: Source) -> str:
    """Extract handle from a Bluesky profile URL.

    Example: "https://bsky.app/profile/lilyray.nyc" -> "lilyray.nyc"
    """
    return source.url.rstrip("/").split("/")[-1]


def extract_bsky_query(source: Source) -> str:
    """Extract search query from a Bluesky search URL.

    Example: "https://bsky.app/search?q=generative+engine+optimization"
             -> "generative engine optimization"
    """
    parsed = urlparse(source.url)
    return parse_qs(parsed.query).get("q", [""])[0]


# --- ARQ cron dispatcher ------------------------------------------------------


async def poll_bluesky_sources(ctx: dict[str, Any]) -> None:
    """ARQ cron: dispatch ingest_bluesky_source jobs for all active Bluesky sources."""
    settings = get_settings()
    if not settings.BLUESKY_HANDLE or not settings.BLUESKY_APP_PASSWORD:
        logger.warning("bluesky_credentials_not_configured")
        return

    if _db.async_session_factory is None:
        logger.error("poll_bluesky_sources_called_before_db_init")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "bluesky",
            )
        )
        sources = list(result.scalars().all())

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_bluesky_source", source.id, _queue_name="fast"
        )

    if sources:
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_bluesky_sources_dispatched", count=len(sources))


# --- ARQ job ------------------------------------------------------------------


async def ingest_bluesky_source(ctx: dict[str, Any], source_id: str) -> None:
    """ARQ job: fetch posts from one Bluesky source (account feed or keyword search).

    Protocol:
    1. Check cooldown via Redis
    2. Create/restore authenticated client (session from Redis via ctx["redis"])
    3. Route: account feed vs. keyword search based on URL
    4. Fetch posts, store new ones via DedupService + IntelItem
    5. handle_source_success or handle_source_error
    """
    redis_client = ctx["redis"]

    if _db.async_session_factory is None:
        logger.error("ingest_bluesky_source_called_before_db_init", source_id=source_id)
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
            client = await get_or_create_bluesky_client(redis_client)
            dedup = DedupService(session)

            new_count = 0
            if is_keyword_search_source(source):
                # Keyword search mode
                query = extract_bsky_query(source)
                response = await client.app.bsky.feed.search_posts(
                    params={"q": query, "limit": 25, "sort": "latest"}
                )
                for post_view in response.posts:
                    stored = await _store_bluesky_post_if_new(
                        session, post_view, source, dedup
                    )
                    if stored:
                        new_count += 1
            else:
                # Account feed mode
                handle = extract_bsky_handle(source)
                response = await client.get_author_feed(
                    actor=handle, limit=25, filter="posts_no_replies"
                )
                for feed_view in response.feed:
                    stored = await _store_bluesky_post_if_new(
                        session, feed_view.post, source, dedup
                    )
                    if stored:
                        new_count += 1

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_bluesky_source_complete",
                source_id=source_id,
                new_items=new_count,
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise


# --- Post storage helper ------------------------------------------------------


async def _store_bluesky_post_if_new(
    session: Any,
    post_view: Any,
    source: Source,
    dedup: DedupService,
) -> bool:
    """Store Bluesky post as IntelItem if not already in DB.

    Returns True if stored, False if duplicate or empty text.

    Uses web URL (https://bsky.app/profile/...) not AT URI (at://...).
    """
    uri: str = post_view.uri
    rkey = uri.split("/")[-1]
    author_handle: str = post_view.author.handle
    web_url = f"https://bsky.app/profile/{author_handle}/post/{rkey}"

    text: str = (post_view.record.text or "").strip()
    if not text:
        return False

    if await dedup.check_url_exists(web_url):
        return False

    title = text[:100].strip()
    excerpt = text[:500]

    created_at_str: str = post_view.record.created_at or ""
    published_at: datetime | None = None
    if created_at_str:
        try:
            published_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        except ValueError:
            pass

    url_hash = dedup._compute_url_hash(web_url)
    content_hash = dedup._get_content_fingerprint(text)

    item = IntelItem(
        source_id=source.id,
        external_id=uri,
        url=web_url,
        url_hash=url_hash,
        title=title,
        content=text,
        excerpt=excerpt,
        published_at=published_at,
        source_name=source.name,
        primary_type="unknown",
        tags=["bluesky", "social"],
        status="raw",
        content_hash=content_hash,
    )
    session.add(item)
    return True
