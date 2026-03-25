"""MCP Registry adapter: cron dispatcher + per-source job with cursor pagination."""

import asyncio
import os

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
from src.workers.content_fetcher import fetch_github_readme, parse_github_url
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
_MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
MAX_PAGES = 100


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def fetch_mcp_registry_page(cursor: str | None = None, limit: int = 100) -> dict:
    """Fetch a single page from the MCP Registry servers endpoint.

    Retries on transient network errors only. Does not retry on 4xx/5xx.

    Args:
        cursor: Pagination cursor from previous response's metadata.nextCursor.
                Pass None to start from the beginning.
        limit: Number of servers per page.

    Returns:
        Parsed JSON response with shape:
            {"servers": [...], "metadata": {"nextCursor": "..." or null}}
    """
    params: dict[str, str | int] = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor

    headers = {"User-Agent": _USER_AGENT}

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(_MCP_REGISTRY_URL, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


async def poll_mcp_registry_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active MCP registry sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "mcp-registry",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_mcp_registry_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_mcp_registry_sources_dispatched", count=len(sources))


async def ingest_mcp_registry_source(ctx: dict, source_id: str) -> None:
    """Per-source job: paginate MCP Registry, deduplicate, and store server items."""
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
            dedup = DedupService(session)
            new_count = 0
            page_count = 0
            cursor = None

            while True:
                if page_count >= MAX_PAGES:
                    logger.warning(
                        "mcp_registry_pagination_limit_reached",
                        source_id=source_id,
                        max_pages=MAX_PAGES,
                    )
                    break
                data = await fetch_mcp_registry_page(cursor=cursor)
                page_count += 1

                for entry in data.get("servers", []):
                    server = entry.get("server", {}) if "server" in entry else entry

                    # Prefer websiteUrl, fall back to repository.url
                    url = server.get("websiteUrl") or ""
                    if not url:
                        repository = server.get("repository") or {}
                        url = repository.get("url", "")
                    if not url:
                        continue

                    if await dedup.check_url_exists(url):
                        continue

                    server_name = server.get("name", "") or ""
                    description = server.get("description", "") or ""

                    # Enrich thin descriptions with GitHub README
                    content_body = description
                    if len(description) < 200:
                        parsed = parse_github_url(url)
                        if parsed:
                            owner, repo_name = parsed
                            github_token = os.environ.get("GITHUB_TOKEN")
                            readme = await fetch_github_readme(
                                owner, repo_name, token=github_token
                            )
                            if readme:
                                content_body = readme
                            # Rate limit GitHub API calls
                            await asyncio.sleep(0.5)

                    url_hash = dedup._compute_url_hash(url)
                    content_hash = dedup._get_content_fingerprint(content_body)

                    item = IntelItem(
                        source_id=source_id,
                        external_id=server_name,
                        url=url,
                        url_hash=url_hash,
                        title=server_name,
                        content=content_body,
                        excerpt=description[:500] if description else None,
                        primary_type="unknown",
                        tags=[],
                        status="raw",
                        content_hash=content_hash,
                        source_name=source.name,
                    )
                    session.add(item)
                    new_count += 1

                # Advance cursor; stop when nextCursor is null/absent
                cursor = data.get("metadata", {}).get("nextCursor")
                if not cursor:
                    break

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_mcp_registry_complete",
                source_id=source_id,
                new_items=new_count,
                pages_fetched=page_count,
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
