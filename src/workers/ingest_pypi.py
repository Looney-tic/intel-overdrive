"""PyPI package adapter: cron dispatcher + per-source job."""

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
_PYPI_JSON_URL = "https://pypi.org/pypi/{package}/json"

DEFAULT_PYPI_PACKAGES = [
    "anthropic",
    "claude-code",
    "mcp",
    "voyageai",
    "instructor",
    "pydantic-ai",
    "langchain-anthropic",
    "litellm",
]


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def fetch_pypi_package(package_name: str) -> dict:
    """Fetch package metadata from PyPI JSON API.

    Retries on transient network errors only. Does not retry on 4xx/5xx.

    Returns:
        Parsed JSON response with keys: info, urls, releases
    """
    url = _PYPI_JSON_URL.format(package=package_name)
    headers = {"User-Agent": _USER_AGENT}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def poll_pypi_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active PyPI sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "pypi",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_pypi_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_pypi_sources_dispatched", count=len(sources))


async def ingest_pypi_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch PyPI package metadata, deduplicate, and store items."""
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

        packages = source.config.get("packages", DEFAULT_PYPI_PACKAGES)
        last_versions: dict = source.config.get("last_versions", {})

        try:
            dedup = DedupService(session)
            new_count = 0

            for pkg_name in packages:
                try:
                    data = await fetch_pypi_package(pkg_name)
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "pypi_package_fetch_failed",
                        package=pkg_name,
                        status=exc.response.status_code,
                    )
                    # Polite delay between packages regardless of outcome
                    await asyncio.sleep(1.0)
                    continue

                info = data.get("info", {})
                version = info.get("version", "")
                if not version:
                    await asyncio.sleep(1.0)
                    continue

                # Skip if we've already seen this version
                if last_versions.get(pkg_name) == version:
                    await asyncio.sleep(1.0)
                    continue

                # Build canonical versioned URL
                pkg_url = f"https://pypi.org/project/{pkg_name}/{version}/"

                # Dedup by URL (handles re-runs without version change detection)
                if await dedup.check_url_exists(pkg_url):
                    last_versions[pkg_name] = version
                    await asyncio.sleep(1.0)
                    continue

                summary = info.get("summary", "") or ""
                # Full README from PyPI JSON API (description field)
                full_description = info.get("description", "") or ""
                # Use full description if substantive, else fall back to summary
                if full_description and len(full_description) > 50:
                    content_body = full_description[:5000]
                else:
                    content_body = summary

                url_hash = dedup._compute_url_hash(pkg_url)
                content_hash = dedup._get_content_fingerprint(content_body)

                # Parse upload time from first file in urls list
                published_at: datetime | None = None
                urls_list = data.get("urls", [])
                if urls_list:
                    upload_time_str = urls_list[0].get("upload_time_iso_8601", "")
                    if upload_time_str:
                        try:
                            dt = datetime.fromisoformat(
                                upload_time_str.replace("Z", "+00:00")
                            )
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            published_at = dt.astimezone(timezone.utc)
                        except (ValueError, AttributeError):
                            published_at = None

                # Build tags from classifiers (short labels) or keywords
                tags: list[str] = []
                keywords_raw = info.get("keywords", "") or ""
                if keywords_raw:
                    # keywords is a comma-separated string on PyPI
                    tags = [k.strip() for k in keywords_raw.split(",") if k.strip()]
                if not tags:
                    # Fall back to short classifier labels (last segment)
                    classifiers = info.get("classifiers", []) or []
                    tags = list(
                        {
                            c.split(" :: ")[-1].lower()
                            for c in classifiers
                            if " :: " in c
                        }
                    )[:10]

                item = IntelItem(
                    source_id=source_id,
                    external_id=f"{pkg_name}=={version}",
                    url=pkg_url,
                    url_hash=url_hash,
                    title=(
                        f"{pkg_name} {version} — {summary[:80]}"
                        if summary and len(summary) > 5
                        else f"{pkg_name} {version} release"
                    ),
                    content=content_body,
                    excerpt=summary[:500] if summary else None,
                    primary_type="unknown",
                    tags=tags,
                    status="raw",
                    content_hash=content_hash,
                    source_name=source.name,
                    published_at=published_at,
                )
                session.add(item)
                last_versions[pkg_name] = version
                new_count += 1

                # Polite crawling delay between package fetches
                await asyncio.sleep(1.0)

            # CRITICAL: dict reassignment for SQLAlchemy JSON mutation detection
            source.config = {**source.config, "last_versions": last_versions}

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_pypi_complete", source_id=source_id, new_items=new_count
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
