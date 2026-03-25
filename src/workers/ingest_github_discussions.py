"""GitHub Discussions GraphQL adapter: cron dispatcher + per-source job.

Fetches community discussions from GitHub repos via GraphQL API.
Requires GITHUB_TOKEN to be set in settings.

EXT-06: GitHub Discussions adapter
"""

from __future__ import annotations

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

logger = get_logger(__name__)

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
_USER_AGENT = "Overdrive-Intel/1.0 (feed aggregator)"

DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    discussions(first: 25, orderBy: {field: CREATED_AT, direction: DESC}, after: $after) {
      nodes {
        id
        title
        url
        createdAt
        author {
          login
        }
        category {
          name
        }
        bodyText
        upvoteCount
        comments {
          totalCount
        }
      }
    }
  }
}
"""


def _parse_iso_timestamp(ts_str: str) -> datetime | None:
    """Parse ISO 8601 timestamp string to timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=30),
    reraise=True,
)
async def fetch_github_discussions(
    owner: str,
    name: str,
    token: str,
    after: str | None = None,
) -> list[dict]:
    """Fetch discussions from a GitHub repo via GraphQL API.

    Retries on transient network errors only. Does not retry on 4xx/5xx.
    Raises ValueError if the response contains GraphQL errors.

    Returns:
        List of discussion node dicts from the GraphQL response.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    payload = {
        "query": DISCUSSIONS_QUERY,
        "variables": {"owner": owner, "name": name, "after": after},
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.post(
            _GITHUB_GRAPHQL_URL,
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

    if "errors" in data:
        raise ValueError(f"GitHub GraphQL errors for {owner}/{name}: {data['errors']}")

    # Log rate limit info if available
    rate_limit_remaining = response.headers.get("x-ratelimit-remaining")
    if rate_limit_remaining is not None:
        logger.debug(
            "github_graphql_rate_limit",
            owner=owner,
            name=name,
            remaining=rate_limit_remaining,
        )

    return data["data"]["repository"]["discussions"]["nodes"]


async def poll_github_discussions_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active github-discussions sources and enqueues per-source jobs."""
    settings = get_settings()
    if not settings.GITHUB_TOKEN:
        logger.warning(
            "github_discussions_skipped",
            reason="GITHUB_TOKEN not configured",
        )
        return

    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "github-discussions",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_github_discussions_source", source.id, _queue_name="fast"
        )

    if sources:
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_github_discussions_dispatched", count=len(sources))


async def ingest_github_discussions_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch GitHub Discussions, deduplicate, and store items."""
    redis_client = ctx["redis"]

    settings = get_settings()
    if not settings.GITHUB_TOKEN:
        logger.warning(
            "github_discussions_skipped",
            source_id=source_id,
            reason="GITHUB_TOKEN not configured",
        )
        return

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

        repos = source.config.get("repos", [])
        if not repos:
            logger.warning("github_discussions_no_repos", source_id=source_id)
            return

        # last_discussion_ids: dict of "owner/name" -> last_seen_discussion_node_id
        last_discussion_ids: dict[str, str] = source.config.get(
            "last_discussion_ids", {}
        )
        # last_poll_ts: Unix timestamp of last successful poll (for watermark)
        last_poll_ts: float = source.config.get("last_poll_ts", 0.0)
        last_poll_dt: datetime | None = None
        if last_poll_ts:
            last_poll_dt = datetime.fromtimestamp(last_poll_ts, tz=timezone.utc)

        try:
            dedup = DedupService(session)
            new_count = 0
            new_last_discussion_ids = dict(last_discussion_ids)
            max_created_ts: float = last_poll_ts

            for repo in repos:
                owner = repo.get("owner", "")
                name = repo.get("name", "")
                if not owner or not name:
                    logger.warning(
                        "github_discussions_invalid_repo",
                        source_id=source_id,
                        repo=repo,
                    )
                    continue

                repo_key = f"{owner}/{name}"

                try:
                    nodes = await fetch_github_discussions(
                        owner, name, settings.GITHUB_TOKEN
                    )
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code in (403, 429):
                        logger.warning(
                            "github_discussions_rate_limited",
                            source_id=source_id,
                            repo=repo_key,
                            status_code=status_code,
                        )
                        # Transient — treat as success for circuit breaker purposes
                        continue
                    raise

                for node in nodes:
                    created_at_str = node.get("createdAt", "")
                    created_dt = _parse_iso_timestamp(created_at_str)

                    # Watermark filter: skip discussions older than last poll
                    if created_dt is not None and last_poll_dt is not None:
                        if created_dt <= last_poll_dt:
                            continue

                    # Track max timestamp across all repos
                    if created_dt is not None:
                        node_ts = created_dt.timestamp()
                        if node_ts > max_created_ts:
                            max_created_ts = node_ts
                            # Track the discussion node ID for this repo
                            new_last_discussion_ids[repo_key] = node["id"]

                    node_url = node.get("url", "")
                    if not node_url:
                        continue

                    # Dedup check
                    if await dedup.check_url_exists(node_url):
                        continue

                    url_hash = dedup._compute_url_hash(node_url)
                    body_text = node.get("bodyText") or ""
                    content_hash = (
                        dedup._get_content_fingerprint(body_text) if body_text else None
                    )

                    # Build tags
                    tags: list[str] = ["github-discussions"]
                    category = node.get("category")
                    if category and category.get("name"):
                        tags.append(category["name"].lower())

                    item = IntelItem(
                        source_id=source_id,
                        external_id=node["id"],
                        url=node_url,
                        url_hash=url_hash,
                        title=node.get("title") or "(No title)",
                        content=body_text[:2000] if body_text else "",
                        excerpt=body_text[:500] if body_text else None,
                        primary_type="unknown",
                        tags=tags,
                        status="raw",
                        content_hash=content_hash,
                        source_name=source.name,
                        published_at=created_dt,
                    )
                    session.add(item)
                    new_count += 1

            # Persist watermark via dict reassignment (SQLAlchemy JSON mutation detection)
            if max_created_ts > last_poll_ts:
                source.config = {
                    **source.config,
                    "last_poll_ts": max_created_ts,
                    "last_discussion_ids": new_last_discussion_ids,
                }

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_github_discussions_complete",
                source_id=source_id,
                new_items=new_count,
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
