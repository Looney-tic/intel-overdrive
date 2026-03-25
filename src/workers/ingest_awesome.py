"""Awesome-list git adapter: full README parse + auto-promote GitHub repos.

Cron dispatcher + per-source job.  Parses the FULL README.md on every run
(not just diffs) and auto-promotes GitHub repos with >50 stars to github-deep.
"""

import asyncio
import os
import re
from pathlib import Path

import git
import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import src.core.init_db as _db
from src.core.logger import get_logger
from src.workers.content_fetcher import fetch_github_description
from src.workers.dms_worker import update_ingestion_heartbeat
from src.models.models import IntelItem, Source
from src.services.dedup_service import DedupService
from src.services.feed_fetcher import fetch_github_repo_info
from src.services.quality_service import parse_github_url
from src.services.source_health import (
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)

logger = get_logger(__name__)

REPO_CACHE_DIR = Path(os.environ.get("GIT_CACHE_DIR", "/tmp/overdrive-intel-git-cache"))

_ENTRY_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")

# Badge image patterns to skip: URLs ending in .svg or containing /badges/
_BADGE_URL_RE = re.compile(r"(\.svg(\?[^)]*)?$|/badges/)", re.IGNORECASE)

# Auto-promote threshold: repos with more than this many stars get github-deep tracking
_AUTO_PROMOTE_STAR_THRESHOLD = 50

# Max GitHub API calls per awesome-list source per run for auto-promotion
_MAX_PROMOTE_API_CALLS = 10

# Max consecutive API failures before stopping promotion attempts
_MAX_CONSECUTIVE_FAILURES = 3


def _is_github_url(url: str) -> bool:
    """Check if URL matches github.com/owner/repo pattern."""
    return parse_github_url(url) is not None


def _pull_or_clone(repo_url: str, local_dir: Path) -> git.Repo:
    """Clone the repo if it doesn't exist locally, otherwise pull latest.

    Runs synchronously — must be called via asyncio.to_thread().
    """
    if not local_dir.exists():
        return git.Repo.clone_from(repo_url, str(local_dir), depth=50)
    else:
        repo = git.Repo(str(local_dir))
        repo.remotes.origin.pull()
        return repo


def _extract_new_entries(
    repo: git.Repo,
    from_sha: str | None,
    to_sha: str,
) -> list[dict[str, str]]:
    """Extract new awesome-list entries from the README.

    If from_sha is None (first run): reads entire README and extracts all entries
    from lines starting with '- ['.

    If from_sha is provided: diffs README.md between the two SHAs and extracts
    only added lines starting with '+- ['.

    Runs synchronously — must be called via asyncio.to_thread().

    Returns:
        List of dicts: [{"name": str, "url": str, "description": str}]
    """
    entries: list[dict[str, str]] = []

    if from_sha is None:
        # First run: parse the entire README
        try:
            readme_blob = repo.head.commit.tree["README.md"]
            readme_text = readme_blob.data_stream.read().decode(
                "utf-8", errors="replace"
            )
        except (KeyError, Exception) as exc:
            logger.warning("awesome_readme_not_found", error=str(exc))
            return entries

        for line in readme_text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- ["):
                continue
            matches = _ENTRY_PATTERN.findall(stripped)
            for name, url in matches:
                # Skip badge image links (SVG URLs, /badges/ paths)
                if _BADGE_URL_RE.search(url):
                    continue
                # Skip image alt-text leaking as title (e.g. "![badge text")
                if name.startswith("!"):
                    continue
                # Extract description: text after the closing ) if present
                after_link = _ENTRY_PATTERN.sub("", stripped, count=1).strip(" -—:")
                entries.append({"name": name, "url": url, "description": after_link})
    else:
        # Incremental: only process added list-item lines
        try:
            diff_output = repo.git.diff(from_sha, to_sha, "--", "README.md")
        except git.exc.GitCommandError as exc:
            logger.warning(
                "awesome_diff_sha_miss",
                from_sha=from_sha,
                to_sha=to_sha,
                error=str(exc),
                action="falling_back_to_full_parse",
            )
            return _extract_new_entries(repo, None, to_sha)
        added_lines = [
            line[1:]  # strip the leading '+'
            for line in diff_output.splitlines()
            if line.startswith("+- [")
        ]

        if not added_lines and any(
            line.startswith("+- ") for line in diff_output.splitlines()
        ):
            logger.warning(
                "awesome_diff_added_items_no_urls",
                from_sha=from_sha,
                to_sha=to_sha,
            )

        for line in added_lines:
            stripped = line.strip()
            matches = _ENTRY_PATTERN.findall(stripped)
            for name, url in matches:
                # Skip badge image links (SVG URLs, /badges/ paths)
                if _BADGE_URL_RE.search(url):
                    continue
                # Skip image alt-text leaking as title (e.g. "![badge text")
                if name.startswith("!"):
                    continue
                after_link = _ENTRY_PATTERN.sub("", stripped, count=1).strip(" -—:")
                entries.append({"name": name, "url": url, "description": after_link})

    return entries


async def _auto_promote_repos(
    session,
    new_github_urls: list[str],
    source_id: str,
    github_token: str | None,
) -> int:
    """Auto-promote GitHub repos with >50 stars to github-deep tracking.

    Limits to _MAX_PROMOTE_API_CALLS per source per run. Stops after
    _MAX_CONSECUTIVE_FAILURES consecutive API failures.

    Returns:
        Number of repos promoted.
    """
    promoted = 0
    api_calls = 0
    consecutive_failures = 0

    for url in new_github_urls:
        if api_calls >= _MAX_PROMOTE_API_CALLS:
            logger.info(
                "auto_promote_api_limit_reached",
                source_id=source_id,
                checked=api_calls,
                promoted=promoted,
            )
            break

        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                "auto_promote_stopped_failures",
                source_id=source_id,
                consecutive_failures=consecutive_failures,
                checked=api_calls,
                promoted=promoted,
            )
            break

        parsed = parse_github_url(url)
        if parsed is None:
            continue

        owner, repo = parsed
        deep_source_id = f"github-deep:{owner}/{repo}"

        # Check if source already exists
        existing = await session.execute(
            select(Source).where(Source.id == deep_source_id)
        )
        if existing.scalar_one_or_none() is not None:
            continue

        # Fetch star count from GitHub API
        api_calls += 1
        try:
            repo_info = await fetch_github_repo_info(owner, repo, github_token)
            consecutive_failures = 0
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (403, 429):
                logger.warning(
                    "auto_promote_rate_limited",
                    source_id=source_id,
                    owner=owner,
                    repo=repo,
                    status=status,
                )
                # Break the promote loop on rate limit, don't circuit-break source
                break
            consecutive_failures += 1
            logger.warning(
                "auto_promote_api_error",
                source_id=source_id,
                owner=owner,
                repo=repo,
                status=status,
                error=str(exc),
            )
            continue
        except Exception as exc:
            consecutive_failures += 1
            logger.warning(
                "auto_promote_api_error",
                source_id=source_id,
                owner=owner,
                repo=repo,
                error=str(exc),
            )
            continue

        stars = repo_info.get("stargazers_count", 0)
        if stars <= _AUTO_PROMOTE_STAR_THRESHOLD:
            continue

        # Create github-deep source
        github_url = f"https://github.com/{owner}/{repo}"
        new_source = Source(
            id=deep_source_id,
            name=f"{owner}/{repo} (deep, auto-promoted)",
            type="github-deep",
            url=github_url,
            is_active=True,
            config={
                "star_milestones": [1000, 5000, 10000],
                "commit_burst_threshold": 20,
                "watched_files": ["CHANGELOG.md"],
                "auto_promoted": True,
                "promoted_from": source_id,
            },
            poll_interval_seconds=3600,
            tier="tier2",
        )
        try:
            async with session.begin_nested():
                session.add(new_source)
                await session.flush()
            promoted += 1
            logger.info(
                "auto_promoted_repo",
                source_id=source_id,
                deep_source_id=deep_source_id,
                stars=stars,
            )
        except IntegrityError:
            # Race condition — another worker created it; safe to skip.
            # Savepoint rolled back, session still usable — no full rollback needed.
            logger.debug(
                "auto_promote_duplicate",
                deep_source_id=deep_source_id,
            )

    return promoted


async def poll_awesome_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active awesome-list sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "awesome-list",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_awesome_source", source.id, _queue_name="fast"
        )

    if sources:
        # Heartbeat after dispatch (accepted proxy for ingestion activity)
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_awesome_sources_dispatched", count=len(sources))


async def ingest_awesome_source(ctx: dict, source_id: str) -> None:
    """Per-source job: clone/pull repo, diff README, deduplicate, store IntelItems."""
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
            REPO_CACHE_DIR.mkdir(parents=True, exist_ok=True)

            repo_url = source.config.get(
                "repo_url", "https://github.com/hesreallyhim/awesome-claude-code"
            )
            # Sanitize source ID for use as a directory name
            safe_id = source_id.replace("/", "_").replace(":", "_")
            local_dir = REPO_CACHE_DIR / safe_id

            # MUST run in thread — gitpython is synchronous and blocks the event loop
            repo = await asyncio.to_thread(_pull_or_clone, repo_url, local_dir)

            current_sha = repo.head.commit.hexsha
            last_sha: str | None = source.config.get("last_commit_sha")

            if last_sha == current_sha:
                # No new commits since last poll
                await handle_source_success(session, source)
                logger.info("ingest_awesome_no_new_commits", source_id=source_id)
                return

            # Full README parse on every run — always from_sha=None.
            # The dedup check prevents re-inserting known URLs, so full
            # parse is safe and idempotent. This captures entries that
            # were in the README before we started tracking.
            all_entries = await asyncio.to_thread(
                _extract_new_entries, repo, None, current_sha
            )

            dedup = DedupService(session)
            new_count = 0
            new_github_urls: list[str] = []

            for entry in all_entries:
                url = entry["url"]
                name = entry["name"]
                description = entry["description"]

                # Enrich empty descriptions for GitHub repos
                if not description and _is_github_url(url):
                    parsed = parse_github_url(url)
                    if parsed:
                        owner, repo_name = parsed
                        github_token = os.environ.get("GITHUB_TOKEN")
                        gh_desc = await fetch_github_description(
                            owner, repo_name, token=github_token
                        )
                        if gh_desc:
                            description = gh_desc
                        # Rate limit GitHub API calls
                        await asyncio.sleep(0.5)

                if await dedup.check_url_exists(url):
                    continue

                url_hash = dedup._compute_url_hash(url)
                content_hash = dedup._get_content_fingerprint(description)

                item = IntelItem(
                    source_id=source_id,
                    external_id=url,
                    url=url,
                    url_hash=url_hash,
                    title=name,
                    content=description,
                    excerpt=description[:500] if description else None,
                    primary_type="unknown",
                    tags=[],
                    status="raw",
                    content_hash=content_hash,
                    source_name=source.name,
                )
                try:
                    async with session.begin_nested():
                        session.add(item)
                    new_count += 1
                except IntegrityError:
                    # Race condition or cross-source duplicate
                    logger.debug("awesome_duplicate_url", url=url)
                    continue

                # Collect new GitHub URLs for auto-promotion
                if _is_github_url(url):
                    new_github_urls.append(url)

            # Dict reassignment triggers SQLAlchemy JSON mutation detection
            source.config = {**source.config, "last_commit_sha": current_sha}

            await session.commit()

            # Auto-promote GitHub repos with >50 stars to github-deep
            promoted = 0
            if new_github_urls:
                github_token = os.environ.get("GITHUB_TOKEN")
                promoted = await _auto_promote_repos(
                    session, new_github_urls, source_id, github_token
                )
                if promoted > 0:
                    await session.commit()

            await handle_source_success(session, source)
            logger.info(
                "ingest_awesome_complete",
                source_id=source_id,
                new_items=new_count,
                promoted=promoted,
                sha=current_sha,
            )

        except Exception as exc:
            # CRITICAL: rollback BEFORE handle_source_error to clear dirty session state
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise
