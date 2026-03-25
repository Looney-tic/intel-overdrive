"""VS Code Marketplace adapter: cron dispatcher + per-source job."""

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
_VSCODE_GALLERY_URL = (
    "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery"
)

# flags=914 requests: IncludeVersions(1) + IncludeFiles(2) + IncludeStatistics(16) +
# IncludeMetadata(256) + IncludeAssetUri(512) + ExcludeNonValidated(128)
# sortBy=4 = InstallCount (most popular first); sortOrder=0 = Descending
_GALLERY_FLAGS = 914
_SORT_BY_INSTALL_COUNT = 4
_SORT_ORDER_DESCENDING = 0

DEFAULT_VSCODE_QUERIES = ["mcp", "claude", "copilot", "cline", "continue"]


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def fetch_vscode_extensions(query: str, page_size: int = 50) -> list[dict]:
    """Search VS Code Marketplace for extensions matching the given query.

    Uses the gallery POST API with install-count sort and standard flags.
    Retries on transient network errors only. Does not retry on 4xx/5xx.

    Returns:
        List of extension dicts from results[0]["extensions"]
    """
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json;api-version=7.2-preview.1",
        "Content-Type": "application/json",
    }
    body = {
        "filters": [
            {
                "criteria": [
                    # filterType=8: target platform (VS Code)
                    {"filterType": 8, "value": "Microsoft.VisualStudio.Code"},
                    # filterType=10: search text
                    {"filterType": 10, "value": query},
                ],
                "pageSize": page_size,
                "pageNumber": 1,
                "sortBy": _SORT_BY_INSTALL_COUNT,
                "sortOrder": _SORT_ORDER_DESCENDING,
            }
        ],
        "flags": _GALLERY_FLAGS,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(_VSCODE_GALLERY_URL, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

    results = data.get("results", [])
    if not results:
        return []
    return results[0].get("extensions", [])


async def poll_vscode_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active VS Code Marketplace sources and enqueues jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "vscode-marketplace",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_vscode_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_vscode_sources_dispatched", count=len(sources))


async def ingest_vscode_source(ctx: dict, source_id: str) -> None:
    """Per-source job: search VS Code Marketplace, deduplicate, and store items."""
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

        queries = source.config.get("queries", DEFAULT_VSCODE_QUERIES)
        last_poll_ts: float = source.config.get("last_poll_ts", 0)

        try:
            dedup = DedupService(session)
            new_count = 0
            max_ext_ts = last_poll_ts

            for query in queries:
                try:
                    extensions = await fetch_vscode_extensions(query)
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "vscode_query_fetch_failed",
                        query=query,
                        status=exc.response.status_code,
                    )
                    continue

                for ext in extensions:
                    publisher = ext.get("publisher", {}).get("publisherName", "")
                    name = ext.get("extensionName", "")
                    if not publisher or not name:
                        continue

                    display_name = ext.get("displayName", name)
                    description = ext.get("shortDescription", "") or ""

                    versions = ext.get("versions", [])
                    version = versions[0].get("version", "") if versions else ""
                    last_updated_str = (
                        versions[0].get("lastUpdated", "") if versions else ""
                    )

                    # Parse lastUpdated to Unix timestamp for watermark comparison
                    ext_ts: float = 0.0
                    published_at: datetime | None = None
                    if last_updated_str:
                        try:
                            dt = datetime.fromisoformat(
                                last_updated_str.replace("Z", "+00:00")
                            )
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            dt_utc = dt.astimezone(timezone.utc)
                            ext_ts = dt_utc.timestamp()
                            published_at = dt_utc
                        except (ValueError, AttributeError):
                            ext_ts = 0.0

                    # Track max timestamp across all extensions for watermark update
                    if ext_ts > max_ext_ts:
                        max_ext_ts = ext_ts

                    # Skip extensions older than or equal to last poll watermark
                    if ext_ts and ext_ts <= last_poll_ts:
                        continue

                    # Canonical marketplace URL
                    ext_url = f"https://marketplace.visualstudio.com/items?itemName={publisher}.{name}"
                    # Use versioned external_id for dedup across version updates
                    external_id = f"{publisher}.{name}=={version}"

                    if await dedup.check_url_exists(ext_url):
                        continue

                    url_hash = dedup._compute_url_hash(ext_url)
                    content_hash = dedup._get_content_fingerprint(description)

                    # Extract install count and rating from statistics array
                    stats: list[dict] = ext.get("statistics", []) or []
                    stat_map = {
                        s.get("statisticName", ""): s.get("value", 0) for s in stats
                    }
                    install_count = int(stat_map.get("install", 0))
                    rating = float(stat_map.get("weightedRating", 0.0))

                    # Build content with stats for richer signal
                    content_parts = [description]
                    if install_count:
                        content_parts.append(
                            f"Installs: {install_count:,}. Rating: {rating:.1f}"
                        )
                    content = " | ".join(p for p in content_parts if p)

                    item = IntelItem(
                        source_id=source_id,
                        external_id=external_id,
                        url=ext_url,
                        url_hash=url_hash,
                        title=f"{display_name} ({publisher}.{name})",
                        content=content,
                        excerpt=description[:500] if description else None,
                        primary_type="unknown",
                        tags=[query],
                        status="raw",
                        content_hash=content_hash,
                        source_name=source.name,
                        published_at=published_at,
                    )
                    session.add(item)
                    new_count += 1

            # CRITICAL: dict reassignment for SQLAlchemy JSON mutation detection
            if max_ext_ts > last_poll_ts:
                source.config = {**source.config, "last_poll_ts": max_ext_ts}

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_vscode_complete", source_id=source_id, new_items=new_count
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
