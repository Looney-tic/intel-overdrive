"""Sitemap ingest worker.

Polls XML sitemaps for sources without RSS feeds (Anthropic blog, tool
documentation sites, etc.). Fetches page HTML, extracts title/excerpt/body,
and stores via the dedup pipeline.

Ported from geo-dashboard/backend/app/workers/ingest_sitemap.py.
Adapted to overdrive-intel patterns (SQLAlchemy, IntelItem, DedupService).
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from xml.etree import ElementTree

import httpx
from sqlalchemy import select

import src.core.init_db as _db
from src.core.logger import get_logger
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.feed_fetcher import fetch_feed_conditional
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)
from src.workers.dms_worker import update_ingestion_heartbeat

logger = get_logger(__name__)

POLITE_DELAY_SECONDS = 1.0
USER_AGENT = "Overdrive-Intel/1.0 (feed aggregator)"

# XML namespaces used in sitemaps
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


# --- Date extraction from HTML ------------------------------------------------


def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO 8601 date string and return a timezone-aware UTC datetime.

    Python 3.12's datetime.fromisoformat() handles timezone offsets directly.
    Naive datetimes are assumed UTC.  Returns None if the string cannot be parsed.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _extract_published_date(html: str) -> Optional[datetime]:
    """Extract article publication date from HTML metadata.

    Tries three signals in priority order:
    1. Open Graph: <meta property="article:published_time" content="...">
    2. JSON-LD: <script type="application/ld+json"> with datePublished/dateModified
    3. <time datetime="..."> tag

    Returns a naive UTC datetime (tzinfo stripped) or None if no date found.
    """
    # 1. Open Graph article:published_time
    og_match = re.search(
        r'<meta\s+[^>]*property=["\']article:published_time["\']\s+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if not og_match:
        # Attribute order may be reversed
        og_match = re.search(
            r'<meta\s+[^>]*content=["\'](.*?)["\']\s+[^>]*property=["\']article:published_time["\']',
            html,
            re.IGNORECASE,
        )
    if og_match:
        dt = _parse_iso(og_match.group(1))
        if dt is not None:
            return dt

    # 2. JSON-LD datePublished / dateModified
    for ld_block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        try:
            data = json.loads(ld_block)
            # Handle both single objects and arrays
            if isinstance(data, list):
                objects = data
            else:
                objects = [data]
            for obj in objects:
                for key in ("datePublished", "dateModified"):
                    val = obj.get(key) if isinstance(obj, dict) else None
                    if val:
                        dt = _parse_iso(str(val))
                        if dt is not None:
                            return dt
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

    # 3. <time datetime="..."> tag
    time_match = re.search(
        r'<time[^>]+datetime=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if time_match:
        dt = _parse_iso(time_match.group(1))
        if dt is not None:
            return dt

    return None


# --- Sitemap XML parsing -------------------------------------------------------


def _parse_sitemap_urls(
    content: bytes,
) -> tuple[list[tuple[str, Optional[datetime]]], list[str]]:
    """Parse sitemap XML and return (page_entries, child_sitemap_urls).

    page_entries are (url, lastmod_datetime) tuples. lastmod is None if not present.
    Handles both regular sitemaps (<urlset>) and sitemap indexes (<sitemapindex>).
    """
    page_entries: list[tuple[str, Optional[datetime]]] = []
    child_sitemaps: list[str] = []

    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return page_entries, child_sitemaps

    # Strip namespace for easier tag matching
    tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

    if tag == "sitemapindex":
        # Sitemap index — contains references to child sitemaps
        for sitemap_el in root.findall("sm:sitemap/sm:loc", SITEMAP_NS):
            if sitemap_el.text:
                child_sitemaps.append(sitemap_el.text.strip())
    elif tag == "urlset":
        # Regular sitemap — contains page URLs with optional lastmod
        for url_el in root.findall("sm:url", SITEMAP_NS):
            loc = url_el.find("sm:loc", SITEMAP_NS)
            if loc is None or not loc.text:
                continue
            lastmod_el = url_el.find("sm:lastmod", SITEMAP_NS)
            lastmod = (
                _parse_iso(lastmod_el.text)
                if lastmod_el is not None and lastmod_el.text
                else None
            )
            page_entries.append((loc.text.strip(), lastmod))

    return page_entries, child_sitemaps


def _filter_entries(
    entries: list[tuple[str, Optional[datetime]]],
    url_filter: str | None,
    max_age_days: int | None = None,
) -> list[tuple[str, Optional[datetime]]]:
    """Filter sitemap entries by URL patterns and optional max age.

    url_filter: comma-separated path patterns, e.g. "/blog/,/news/".
    max_age_days: if set, skip entries with lastmod older than N days ago.
    """
    result = entries

    if url_filter:
        patterns = [p.strip() for p in url_filter.split(",") if p.strip()]
        if patterns:
            result = [
                (url, lastmod)
                for url, lastmod in result
                if any(pattern in url for pattern in patterns)
            ]

    if max_age_days is not None:
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        result = [
            (url, lastmod)
            for url, lastmod in result
            if lastmod is None
            or lastmod >= cutoff  # keep if no lastmod (can't filter) or recent
        ]

    return result


# --- HTML content extraction --------------------------------------------------


def _extract_page_content(html: str) -> dict[str, str]:
    """Extract title, excerpt, and body from HTML page.

    Uses regex-based extraction (no BeautifulSoup dependency).
    """
    # Title: <title> tag
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL
    )
    title = title_match.group(1).strip() if title_match else ""
    title = re.sub(r"<[^>]+>", "", title)  # strip any inner tags

    # Excerpt: meta description or og:description
    excerpt = ""
    meta_desc = re.search(
        r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if not meta_desc:
        meta_desc = re.search(
            r'<meta\s+content=["\'](.*?)["\']\s+name=["\']description["\']',
            html,
            re.IGNORECASE,
        )
    og_desc = re.search(
        r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']',
        html,
        re.IGNORECASE,
    )
    if not og_desc:
        og_desc = re.search(
            r'<meta\s+content=["\'](.*?)["\']\s+property=["\']og:description["\']',
            html,
            re.IGNORECASE,
        )
    excerpt = (meta_desc or og_desc).group(1).strip() if (meta_desc or og_desc) else ""

    # Body: extract from <article> or <main>, fall back to <body>
    body = ""
    for tag in ["article", "main"]:
        body_match = re.search(
            rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.IGNORECASE | re.DOTALL
        )
        if body_match:
            body = body_match.group(1)
            break
    if not body:
        body_match = re.search(
            r"<body[^>]*>(.*?)</body>", html, re.IGNORECASE | re.DOTALL
        )
        body = body_match.group(1) if body_match else ""

    # Strip HTML tags and normalize whitespace
    body = re.sub(
        r"<script[^>]*>.*?</script>", "", body, flags=re.IGNORECASE | re.DOTALL
    )
    body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()

    return {"title": title, "excerpt": excerpt, "body": body}


# --- Sitemap collection (recursive) -------------------------------------------


async def _collect_sitemap_entries(
    content: bytes,
) -> list[tuple[str, Optional[datetime]]]:
    """Parse sitemap XML and recursively resolve sitemap indexes to page entries.

    Returns list of (url, lastmod) tuples.
    """
    page_entries, child_sitemaps = _parse_sitemap_urls(content)

    # Recursively fetch child sitemaps (one level deep to avoid infinite loops)
    for child_url in child_sitemaps[:10]:  # cap at 10 child sitemaps
        try:
            child_content, _, _ = await fetch_feed_conditional(child_url)
            if child_content:
                child_entries, _ = _parse_sitemap_urls(child_content)
                page_entries.extend(child_entries)
        except Exception:
            logger.warning("child_sitemap_fetch_failed", url=child_url)

    return page_entries


# --- Page fetch and store helper ----------------------------------------------


async def _fetch_and_store_page(
    session: Any,
    url: str,
    source: Source,
    dedup: DedupService,
    lastmod: Optional[datetime] = None,
) -> bool:
    """Fetch a single page, extract content, and store via dedup pipeline.

    Returns True if stored (new or updated), False if duplicate or fetch failed.
    If lastmod is provided and the URL exists, re-fetches if the page was modified
    after the existing item's created_at (catches content updates).
    """
    # Quick dedup check: skip if URL already in DB and not updated
    is_update = False
    if await dedup.check_url_exists(url):
        # If we have lastmod, check if the page was updated since we last ingested
        if lastmod is not None:
            from sqlalchemy import text as sa_text

            result = await session.execute(
                sa_text(
                    "SELECT id, created_at FROM intel_items WHERE url = :url LIMIT 1"
                ),
                {"url": url},
            )
            row = result.first()
            if row and row.created_at:
                existing_date = (
                    row.created_at
                    if row.created_at.tzinfo
                    else row.created_at.replace(tzinfo=timezone.utc)
                )
                if lastmod <= existing_date:
                    return False  # Page hasn't been updated since we ingested it
                # Page was updated — re-fetch and update existing item
                is_update = True
                existing_item_id = row.id
                logger.info("sitemap_page_updated", url=url, lastmod=str(lastmod))
            else:
                return False
        else:
            return False

    # Fetch page HTML
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            html = response.text
    except Exception:
        logger.debug("page_fetch_failed", url=url)
        return False

    # Extract content
    extracted = _extract_page_content(html)
    title = extracted["title"].strip()
    if not title:
        return False

    excerpt_raw = extracted["excerpt"] or extracted["body"][:500]
    excerpt = excerpt_raw.strip() if excerpt_raw else None
    body = extracted["body"] or None

    # Extract published date from HTML metadata
    published_at = _extract_published_date(html)

    content_hash = dedup._get_content_fingerprint(body or title)

    if is_update:
        # Update existing item instead of inserting a duplicate
        from sqlalchemy import text as sa_text

        await session.execute(
            sa_text(
                """
                UPDATE intel_items
                SET title = :title, content = :content, excerpt = :excerpt,
                    published_at = :published_at, content_hash = :content_hash,
                    status = 'raw', embedding = NULL, updated_at = NOW()
                WHERE id = CAST(:id AS uuid)
            """
            ),
            {
                "id": str(existing_item_id),
                "title": title,
                "content": body or "",
                "excerpt": excerpt,
                "published_at": published_at,
                "content_hash": content_hash,
            },
        )
        return True

    url_hash = dedup._compute_url_hash(url)

    item = IntelItem(
        source_id=source.id,
        external_id=url,
        url=url,
        url_hash=url_hash,
        title=title,
        content=body or "",
        excerpt=excerpt,
        published_at=published_at,
        source_name=source.name,
        primary_type="unknown",
        tags=[],
        status="raw",
        content_hash=content_hash,
    )
    session.add(item)
    return True


# --- ARQ cron dispatcher ------------------------------------------------------


async def poll_sitemap_sources(ctx: dict[str, Any]) -> None:
    """ARQ cron: dispatch ingest_sitemap_source jobs for all active sitemap sources."""
    if _db.async_session_factory is None:
        logger.error("poll_sitemap_sources_called_before_db_init")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "sitemap",
            )
        )
        sources = list(result.scalars().all())

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_sitemap_source", source.id, _queue_name="fast"
        )

    if sources:
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_sitemap_sources_dispatched", count=len(sources))


# --- ARQ job ------------------------------------------------------------------


async def ingest_sitemap_source(ctx: dict[str, Any], source_id: str) -> None:
    """ARQ job: fetch and store new pages from a single sitemap source.

    Protocol:
    1. Check cooldown via Redis
    2. Fetch sitemap XML (with conditional GET support)
    3. Parse URLs, handle sitemap index recursion (cap at 10 child sitemaps)
    4. Filter by url_filter patterns from source.config
    5. For each new URL: fetch page, extract content, store via DedupService
    6. Polite 1-second delay between page fetches
    7. Update source.last_etag, source.last_modified_header after success
    """
    redis_client = ctx["redis"]

    if _db.async_session_factory is None:
        logger.error("ingest_sitemap_source_called_before_db_init", source_id=source_id)
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
            # Fetch sitemap XML with conditional GET
            content, new_etag, new_last_modified = await fetch_feed_conditional(
                source.url, source.last_etag, source.last_modified_header
            )
        except Exception as exc:
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise

        if content is None:
            # 304 Not Modified — nothing new
            logger.debug("ingest_sitemap_source_not_modified", source_id=source_id)
            await handle_source_success(session, source)
            return

        try:
            # Collect all page entries with lastmod (handles sitemap index recursion)
            all_entries = await _collect_sitemap_entries(content)

            # Filter by url_filter patterns and max_age_days from source config
            url_filter: str | None = (
                source.config.get("url_filter") if source.config else None
            )
            max_age_days: int | None = (
                source.config.get("max_age_days") if source.config else None
            )
            filtered_entries = _filter_entries(all_entries, url_filter, max_age_days)

            dedup = DedupService(session)
            new_count = 0

            for url, lastmod in filtered_entries:
                stored = await _fetch_and_store_page(
                    session, url, source, dedup, lastmod=lastmod
                )
                if stored:
                    new_count += 1
                # Polite delay between page fetches
                await asyncio.sleep(POLITE_DELAY_SECONDS)

            await session.commit()
            await handle_source_success(
                session, source, new_etag=new_etag, new_last_modified=new_last_modified
            )
            logger.info(
                "ingest_sitemap_source_complete",
                source_id=source_id,
                entries_found=len(filtered_entries),
                new_items=new_count,
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
