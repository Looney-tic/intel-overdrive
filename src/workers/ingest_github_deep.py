"""Deep GitHub repository analysis worker: cron dispatcher + per-source job.

Monitors watched repositories for significant events:
  - Star milestones (configurable thresholds)
  - Commit bursts (activity spike vs. prior week)
  - Description changes (lightweight proxy for README/project-direction shifts)

Each watched repo is a separate Source row with type=github-deep for independent
circuit breaker isolation.
"""

import asyncio
import base64
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.feed_fetcher import (
    fetch_github_file_contents,
    fetch_github_repo_info,
    fetch_github_repo_stats,
)
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)
from src.workers.dms_worker import update_ingestion_heartbeat

logger = get_logger(__name__)

DEFAULT_STAR_MILESTONES = [100, 500, 1000, 5000, 10000, 50000, 100000]
DEFAULT_COMMIT_BURST_THRESHOLD = 20


def _parse_owner_repo(url: str) -> tuple[str, str]:
    """Extract owner and repo name from a GitHub repository URL.

    Supports both https://github.com/owner/repo and github.com/owner/repo.

    Raises:
        ValueError: if the URL cannot be parsed as a GitHub repo URL.
    """
    parsed = urlparse(url if "://" in url else f"https://{url}")
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Cannot parse owner/repo from URL: {url!r}")
    return parts[0], parts[1]


def _compute_description_hash(description: str | None) -> str:
    """Return a short SHA-256 hex digest of the description string."""
    text = (description or "").strip()
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _get_iso_week(dt: datetime) -> str:
    """Return ISO year+week string like '2026-W11' for the given datetime."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


async def poll_github_deep_sources(ctx: dict) -> None:
    """Cron dispatcher: find active github-deep sources and enqueue per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "github-deep",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_github_deep_source", source.id, _queue_name="fast"
        )

    if sources:
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_github_deep_sources_dispatched", count=len(sources))


async def ingest_github_deep_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch repo stats, detect threshold events, create IntelItems."""
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
            owner, repo = _parse_owner_repo(source.url)
        except ValueError as exc:
            logger.error(
                "github_deep_bad_url",
                source_id=source_id,
                url=source.url,
                error=str(exc),
            )
            return

        github_token = get_settings().GITHUB_TOKEN

        # Read persisted state from source config
        last_star_count: int = source.config.get("last_star_count", 0)
        last_commit_week_total: int = source.config.get("last_commit_week_total", 0)
        last_readme_hash: str = source.config.get("last_readme_hash", "")
        star_milestones: list[int] = source.config.get(
            "star_milestones", DEFAULT_STAR_MILESTONES
        )
        commit_burst_threshold: int = source.config.get(
            "commit_burst_threshold", DEFAULT_COMMIT_BURST_THRESHOLD
        )
        watched_files: list[str] = source.config.get("watched_files", ["CHANGELOG.md"])
        file_hashes: dict[str, str] = source.config.get("file_hashes", {})
        is_first_file_run: bool = not file_hashes

        try:
            # ----------------------------------------------------------------
            # Fetch repository info (stars, description, etc.)
            # ----------------------------------------------------------------
            try:
                repo_info = await fetch_github_repo_info(owner, repo, github_token)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    logger.warning(
                        "github_deep_rate_limited",
                        source_id=source_id,
                        status=exc.response.status_code,
                    )
                    # Don't count as source error — expected transient condition
                    await handle_source_success(session, source)
                    return
                raise

            current_stars: int = repo_info.get("stargazers_count", 0)
            description: str = repo_info.get("description") or ""

            # Rate limit awareness
            # Note: fetch_github_repo_info uses httpx directly — no header passthrough.
            # Rate limit logging is handled via x-ratelimit headers in the raw response.
            # (Headers not surfaced by the helper; rely on error handling for 403/429.)

            dedup = DedupService(session)
            new_count = 0

            # ----------------------------------------------------------------
            # 1. Star milestone detection
            # ----------------------------------------------------------------
            crossed_milestones = [
                m for m in star_milestones if last_star_count < m <= current_stars
            ]
            for milestone in crossed_milestones:
                event_url = (
                    f"https://github.com/{owner}/{repo}#star-milestone-{milestone}"
                )
                if await dedup.check_url_exists(event_url):
                    logger.debug(
                        "star_milestone_already_seen",
                        source_id=source_id,
                        milestone=milestone,
                    )
                    continue

                url_hash = dedup._compute_url_hash(event_url)
                content = (
                    f"Repository {owner}/{repo} has reached {milestone:,} GitHub stars. "
                    f"Current count: {current_stars:,}."
                )
                if description:
                    content += f" {description}"
                content_hash = dedup._get_content_fingerprint(content)

                item = IntelItem(
                    source_id=source_id,
                    external_id=f"{owner}/{repo}:star_milestone:{milestone}",
                    url=event_url,
                    url_hash=url_hash,
                    title=f"{owner}/{repo} reached {milestone:,} stars",
                    content=content,
                    excerpt=content[:500],
                    primary_type="unknown",
                    tags=["github", "stars", "milestone"],
                    status="raw",
                    content_hash=content_hash,
                    source_name=source.name,
                    published_at=datetime.now(timezone.utc),
                )
                session.add(item)
                new_count += 1
                logger.info(
                    "star_milestone_detected",
                    source_id=source_id,
                    repo=f"{owner}/{repo}",
                    milestone=milestone,
                    current_stars=current_stars,
                )

            # ----------------------------------------------------------------
            # 2. Commit burst detection
            # ----------------------------------------------------------------
            last_week_commits = 0
            try:
                participation = await fetch_github_repo_stats(
                    owner, repo, "participation", github_token
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    logger.warning(
                        "github_deep_stats_rate_limited",
                        source_id=source_id,
                        status=exc.response.status_code,
                    )
                    participation = None
                else:
                    raise

            if participation is not None:
                all_commits = participation.get("all", [])
                if all_commits:
                    last_week_commits = all_commits[-1]  # most recent week

                is_burst = (
                    last_week_commits > commit_burst_threshold
                    and last_week_commits > last_commit_week_total * 2
                )
                if is_burst:
                    now = datetime.now(timezone.utc)
                    iso_week = _get_iso_week(now)
                    event_url = (
                        f"https://github.com/{owner}/{repo}#commit-burst-{iso_week}"
                    )

                    if not await dedup.check_url_exists(event_url):
                        url_hash = dedup._compute_url_hash(event_url)
                        content = (
                            f"Repository {owner}/{repo} had a commit burst: "
                            f"{last_week_commits} commits this week"
                            + (
                                f" (up from {last_commit_week_total} last recorded week)."
                                if last_commit_week_total > 0
                                else "."
                            )
                        )
                        content_hash = dedup._get_content_fingerprint(content)

                        item = IntelItem(
                            source_id=source_id,
                            external_id=f"{owner}/{repo}:commit_burst:{iso_week}",
                            url=event_url,
                            url_hash=url_hash,
                            title=(
                                f"{owner}/{repo} commit burst: "
                                f"{last_week_commits} commits this week"
                            ),
                            content=content,
                            excerpt=content[:500],
                            primary_type="unknown",
                            tags=["github", "commits", "burst"],
                            status="raw",
                            content_hash=content_hash,
                            source_name=source.name,
                            published_at=datetime.now(timezone.utc),
                        )
                        session.add(item)
                        new_count += 1
                        logger.info(
                            "commit_burst_detected",
                            source_id=source_id,
                            repo=f"{owner}/{repo}",
                            commits=last_week_commits,
                            previous=last_commit_week_total,
                        )

            # ----------------------------------------------------------------
            # 3. Description change detection
            # ----------------------------------------------------------------
            current_readme_hash = _compute_description_hash(description)
            if last_readme_hash and current_readme_hash != last_readme_hash:
                # Skip first run (last_readme_hash == "") to avoid noise on
                # initial setup
                event_url = (
                    f"https://github.com/{owner}/{repo}"
                    f"#description-changed-{current_readme_hash}"
                )
                if not await dedup.check_url_exists(event_url):
                    url_hash = dedup._compute_url_hash(event_url)
                    content = f"Repository {owner}/{repo} description changed." + (
                        f" New description: {description}" if description else ""
                    )
                    content_hash = dedup._get_content_fingerprint(content)

                    item = IntelItem(
                        source_id=source_id,
                        external_id=(
                            f"{owner}/{repo}:description_changed:{current_readme_hash}"
                        ),
                        url=event_url,
                        url_hash=url_hash,
                        title=f"{owner}/{repo} description changed",
                        content=content,
                        excerpt=content[:500],
                        primary_type="unknown",
                        tags=["github", "description", "change"],
                        status="raw",
                        content_hash=content_hash,
                        source_name=source.name,
                        published_at=datetime.now(timezone.utc),
                    )
                    session.add(item)
                    new_count += 1
                    logger.info(
                        "description_changed_detected",
                        source_id=source_id,
                        repo=f"{owner}/{repo}",
                    )

            # ----------------------------------------------------------------
            # 4. CHANGELOG.md diffing (watched file SHA change detection)
            # ----------------------------------------------------------------
            for watched_path in watched_files:
                try:
                    file_data = await fetch_github_file_contents(
                        owner, repo, watched_path, github_token
                    )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (403, 429):
                        logger.warning(
                            "github_deep_file_rate_limited",
                            source_id=source_id,
                            path=watched_path,
                            status=exc.response.status_code,
                        )
                        # Rate limited — skip remaining files, don't circuit-break
                        break
                    raise

                if file_data is None:
                    logger.warning(
                        "github_deep_watched_file_not_found",
                        source_id=source_id,
                        repo=f"{owner}/{repo}",
                        path=watched_path,
                    )
                    continue

                current_sha: str = file_data["sha"]
                stored_sha: str = file_hashes.get(watched_path, "")

                if not is_first_file_run and current_sha != stored_sha:
                    decoded_content = base64.b64decode(
                        file_data.get("content", "")
                    ).decode("utf-8", errors="replace")

                    event_url = (
                        f"https://github.com/{owner}/{repo}"
                        f"/blob/HEAD/{watched_path}#changelog-{current_sha[:8]}"
                    )

                    if not await dedup.check_url_exists(event_url):
                        url_hash = dedup._compute_url_hash(event_url)
                        file_content = decoded_content[:2000]
                        content_hash = dedup._get_content_fingerprint(file_content)
                        tags = ["changelog", "update"]
                        if "BREAKING" in decoded_content.upper():
                            tags.append("breaking-changes")

                        item = IntelItem(
                            source_id=source_id,
                            external_id=(
                                f"{owner}/{repo}:file_changed"
                                f":{watched_path}:{current_sha[:8]}"
                            ),
                            url=event_url,
                            url_hash=url_hash,
                            title=f"{owner}/{repo} {watched_path} updated",
                            content=file_content,
                            excerpt=decoded_content[:500],
                            primary_type="unknown",
                            tags=tags,
                            status="raw",
                            content_hash=content_hash,
                            source_name=source.name,
                            published_at=datetime.now(timezone.utc),
                        )
                        session.add(item)
                        new_count += 1
                        logger.info(
                            "watched_file_changed_detected",
                            source_id=source_id,
                            repo=f"{owner}/{repo}",
                            path=watched_path,
                            sha=current_sha[:8],
                        )

                # Always update the stored SHA (including first run)
                file_hashes[watched_path] = current_sha

            # ----------------------------------------------------------------
            # Persist updated state via dict reassignment (SQLAlchemy mutation)
            # ----------------------------------------------------------------
            source.config = {
                **source.config,
                "last_star_count": current_stars,
                "last_commit_week_total": last_week_commits,
                "last_readme_hash": current_readme_hash,
                "watched_files": watched_files,
                "file_hashes": file_hashes,
            }

            await session.commit()
            await handle_source_success(session, source)
            logger.info(
                "ingest_github_deep_complete",
                source_id=source_id,
                repo=f"{owner}/{repo}",
                stars=current_stars,
                new_items=new_count,
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
