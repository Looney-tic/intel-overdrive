"""npm Registry search adapter: cron dispatcher + per-source job."""

import asyncio

import httpx
from datetime import datetime, timezone
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
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)

logger = get_logger(__name__)

_USER_AGENT = "Overdrive-Intel/1.0 (feed aggregator)"
_NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"
_NPM_PACKAGE_URL = "https://registry.npmjs.org/{package}"


async def _fetch_npm_readme(pkg_name: str, max_chars: int = 5000) -> str | None:
    """Fetch full README from npm registry for a package.

    The npm registry package endpoint returns a `readme` field with full
    Markdown README content.

    Returns:
        README text (up to max_chars), or None on any failure.
    """
    try:
        url = _NPM_PACKAGE_URL.format(package=pkg_name)
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(url, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
            data = response.json()

        readme = data.get("readme", "") or ""
        if readme and len(readme) > 50:
            return readme[:max_chars]
        return None
    except Exception as exc:
        logger.debug("fetch_npm_readme_failed", package=pkg_name, error=str(exc)[:100])
        return None


DEFAULT_NPM_QUERIES = [
    "mcp",
    "claude",
    "claude-code",
    "agent-skill",
]


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def fetch_npm_search(text: str, size: int = 250, from_offset: int = 0) -> dict:
    """Search npm registry for packages matching the given text.

    Retries on transient network errors only. Does not retry on 4xx/5xx.

    Returns:
        Parsed JSON response with shape: {"objects": [...], "total": N}
    """
    params: dict[str, str | int] = {
        "text": text,
        "size": size,
        "from": from_offset,
    }
    headers = {"User-Agent": _USER_AGENT}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(_NPM_SEARCH_URL, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


async def poll_npm_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active npm sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "npm",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_npm_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_npm_sources_dispatched", count=len(sources))


async def ingest_npm_source(ctx: dict, source_id: str) -> None:
    """Per-source job: search npm registry, deduplicate, and store package items."""
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

        queries = source.config.get("queries", DEFAULT_NPM_QUERIES)
        last_poll_ts = source.config.get("last_poll_ts", 0)

        try:
            dedup = DedupService(session)
            new_count = 0
            max_pkg_ts = last_poll_ts

            for query in queries:
                data = await fetch_npm_search(text=query)

                for pkg_obj in data.get("objects", []):
                    pkg = pkg_obj.get("package", {})

                    # Parse package date to Unix timestamp for watermark comparison.
                    # npm date fields are UTC; naive datetimes are assumed UTC.
                    pkg_date_str = pkg.get("date", "")
                    pkg_ts = 0
                    if pkg_date_str:
                        try:
                            dt = datetime.fromisoformat(
                                pkg_date_str.replace("Z", "+00:00")
                            )
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            pkg_ts = int(dt.astimezone(timezone.utc).timestamp())
                        except (ValueError, AttributeError):
                            pkg_ts = 0

                    # Track watermark BEFORE dedup — all returned packages advance
                    # the timestamp regardless of whether they're duplicates
                    if pkg_ts > max_pkg_ts:
                        max_pkg_ts = pkg_ts

                    # Skip packages older than or equal to last poll watermark
                    if pkg_ts and pkg_ts <= last_poll_ts:
                        continue

                    # Use links.npm as canonical URL; fall back to constructing from name
                    links = pkg.get("links", {})
                    npm_url = links.get("npm")
                    if not npm_url:
                        pkg_name = pkg.get("name", "")
                        if not pkg_name:
                            continue
                        npm_url = f"https://www.npmjs.com/package/{pkg_name}"

                    if await dedup.check_url_exists(npm_url):
                        continue

                    pkg_name = pkg.get("name", "")
                    description = pkg.get("description", "") or ""

                    # Fetch full README from npm registry for richer content
                    content_body = description
                    if pkg_name:
                        readme = await _fetch_npm_readme(pkg_name)
                        if readme:
                            content_body = readme
                        # Polite delay between README fetches
                        await asyncio.sleep(0.5)

                    url_hash = dedup._compute_url_hash(npm_url)
                    content_hash = dedup._get_content_fingerprint(content_body)

                    item = IntelItem(
                        source_id=source_id,
                        external_id=pkg_name,
                        url=npm_url,
                        url_hash=url_hash,
                        title=pkg_name,
                        content=content_body,
                        excerpt=description[:500] if description else None,
                        primary_type="unknown",
                        tags=pkg.get("keywords") or [],
                        status="raw",
                        content_hash=content_hash,
                        source_name=source.name,
                    )
                    session.add(item)
                    new_count += 1

            # CRITICAL: dict reassignment for SQLAlchemy JSON mutation detection
            if max_pkg_ts > last_poll_ts:
                source.config = {**source.config, "last_poll_ts": max_pkg_ts}

            await session.commit()
            await handle_source_success(session, source)
            logger.info("ingest_npm_complete", source_id=source_id, new_items=new_count)

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
