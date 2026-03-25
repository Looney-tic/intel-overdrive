"""Claude Code plugin marketplace adapter: cron dispatcher + per-source job.

Fetches marketplace.json files from public Claude Code plugin marketplaces,
extracts plugin entries, creates IntelItems for each plugin, and auto-creates
github-deep sources for plugins hosted on GitHub repos with significant traction.

Each marketplace source has a URL pointing to a Git repo containing
.claude-plugin/marketplace.json. The adapter fetches the raw JSON via
GitHub/GitLab raw content URLs.
"""

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

_USER_AGENT = (
    "Overdrive-Intel/1.0 (https://inteloverdrive.com; plugin marketplace crawler)"
)


def _build_raw_url(repo_url: str, path: str = ".claude-plugin/marketplace.json") -> str:
    """Convert a GitHub/GitLab repo URL to a raw content URL.

    Supports:
    - https://github.com/owner/repo -> raw.githubusercontent.com/owner/repo/HEAD/path
    - https://gitlab.com/owner/repo -> gitlab.com/owner/repo/-/raw/main/path
    - Direct URLs ending in .json -> used as-is
    """
    if repo_url.endswith(".json"):
        return repo_url

    repo_url = repo_url.rstrip("/").removesuffix(".git")

    if "github.com" in repo_url:
        # https://github.com/owner/repo -> raw URL
        parts = repo_url.replace("https://github.com/", "").split("/")
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{path}"

    if "gitlab.com" in repo_url:
        parts = repo_url.replace("https://gitlab.com/", "").split("/")
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            return f"https://gitlab.com/{owner}/{repo}/-/raw/main/{path}"

    # Fallback: assume GitHub-style
    return f"{repo_url}/raw/HEAD/{path}"


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=30),
    reraise=True,
)
async def _fetch_marketplace_json(url: str) -> dict:
    """Fetch and parse a marketplace.json file."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(url, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        return response.json()


def _extract_plugin_repo_url(plugin: dict) -> str | None:
    """Extract a browsable URL from a plugin's source field.

    Returns a GitHub/GitLab URL if the source points to one, None otherwise.
    """
    source = plugin.get("source", "")

    # String source (relative path) — not useful
    if isinstance(source, str):
        return None

    if isinstance(source, dict):
        src_type = source.get("source", "")

        if src_type == "github":
            repo = source.get("repo", "")
            if repo:
                return f"https://github.com/{repo}"

        if src_type in ("url", "git-subdir"):
            url = source.get("url", "")
            if url and ("github.com" in url or "gitlab.com" in url):
                return url.removesuffix(".git")

        if src_type == "npm":
            package = source.get("package", "")
            if package:
                return f"https://www.npmjs.com/package/{package}"

    return None


async def poll_marketplace_sources(ctx: dict) -> None:
    """Cron dispatcher: queries active marketplace sources and enqueues per-source jobs."""
    if _db.async_session_factory is None:
        logger.error("db_not_initialized")
        return

    async with _db.async_session_factory() as session:
        result = await session.execute(
            select(Source).where(
                Source.is_active == True,  # noqa: E712
                Source.type == "marketplace",
            )
        )
        sources = result.scalars().all()

    for source in sources:
        await ctx["redis"].enqueue_job(
            "ingest_marketplace_source", source.id, _queue_name="fast"
        )

    if sources:
        await update_ingestion_heartbeat(ctx["redis"])

    logger.info("poll_marketplace_sources_dispatched", count=len(sources))


async def ingest_marketplace_source(ctx: dict, source_id: str) -> None:
    """Per-source job: fetch marketplace.json, extract plugins, deduplicate, store."""
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
            # Build raw URL from the source's repo URL
            raw_url = _build_raw_url(source.url)
            marketplace_data = await _fetch_marketplace_json(raw_url)

            marketplace_name = marketplace_data.get("name", "unknown")
            plugins = marketplace_data.get("plugins", [])
            dedup = DedupService(session)
            new_count = 0
            github_repos: list[str] = []

            for plugin in plugins:
                name = plugin.get("name", "")
                if not name:
                    continue

                description = plugin.get("description", "")
                version = plugin.get("version", "")
                author_info = plugin.get("author", {})
                author_name = (
                    author_info.get("name", "") if isinstance(author_info, dict) else ""
                )
                category = plugin.get("category", "")
                keywords = plugin.get("keywords", [])
                homepage = plugin.get("homepage", "")
                repository = plugin.get("repository", "")
                license_str = plugin.get("license", "")

                # Determine the best URL for this plugin
                plugin_url = (
                    homepage
                    or repository
                    or _extract_plugin_repo_url(plugin)
                    or f"marketplace://{marketplace_name}/{name}"
                )

                if await dedup.check_url_exists(plugin_url):
                    continue

                # Build rich content from all available metadata
                content_parts = []
                if description:
                    content_parts.append(description)
                if version:
                    content_parts.append(f"Version: {version}")
                if author_name:
                    content_parts.append(f"Author: {author_name}")
                if category:
                    content_parts.append(f"Category: {category}")
                if license_str:
                    content_parts.append(f"License: {license_str}")

                # Note what components the plugin provides
                components = []
                if plugin.get("mcpServers"):
                    components.append("mcp-server")
                if plugin.get("commands"):
                    components.append("commands")
                if plugin.get("agents"):
                    components.append("agents")
                if plugin.get("hooks"):
                    components.append("hooks")
                if plugin.get("lspServers"):
                    components.append("lsp-server")
                if components:
                    content_parts.append(f"Components: {', '.join(components)}")

                content_text = " | ".join(content_parts) if content_parts else name

                # Build tags
                tags = ["claude-code-plugin", f"marketplace:{marketplace_name}"]
                if isinstance(keywords, list):
                    tags.extend(keywords[:10])
                if category:
                    tags.append(category)
                for comp in components:
                    tags.append(comp)

                url_hash = dedup._compute_url_hash(plugin_url)
                content_hash = dedup._get_content_fingerprint(content_text)

                item = IntelItem(
                    source_id=source_id,
                    external_id=f"{marketplace_name}/{name}",
                    url=plugin_url,
                    url_hash=url_hash,
                    title=f"{name} (Claude Code plugin)",
                    content=content_text,
                    excerpt=description[:500] if description else None,
                    primary_type="unknown",
                    tags=tags,
                    status="raw",
                    content_hash=content_hash,
                    source_name=source.name,
                )

                try:
                    async with session.begin_nested():
                        session.add(item)
                    new_count += 1
                except IntegrityError:
                    logger.debug("marketplace_duplicate_url", url=plugin_url)
                    continue

                # Collect GitHub repo URLs for auto-creating github-deep sources
                repo_url = _extract_plugin_repo_url(plugin)
                if repo_url and "github.com" in repo_url:
                    github_repos.append(repo_url)

            await session.commit()

            # Auto-create github-deep sources for plugin repos
            if github_repos:
                promoted = await _auto_track_plugin_repos(
                    session, github_repos, source_id
                )
                if promoted > 0:
                    await session.commit()

            await handle_source_success(session, source)
            logger.info(
                "ingest_marketplace_complete",
                source_id=source_id,
                marketplace=marketplace_name,
                total_plugins=len(plugins),
                new_items=new_count,
            )

        except Exception as exc:
            await session.rollback()
            await handle_source_error(session, source, exc)
            raise


async def _auto_track_plugin_repos(
    session, github_urls: list[str], source_id: str
) -> int:
    """Auto-create github-deep sources for plugin repos from marketplace.

    Unlike the awesome-list auto-promote, this doesn't check star count —
    any plugin in a public marketplace is worth tracking.
    """
    promoted = 0

    for url in github_urls:
        url = url.rstrip("/").removesuffix(".git")
        parts = url.replace("https://github.com/", "").split("/")
        if len(parts) < 2:
            continue

        owner, repo = parts[0], parts[1]
        deep_source_id = f"github-deep:{owner}/{repo}"

        # Check if already tracked
        existing = await session.execute(
            select(Source).where(Source.id == deep_source_id)
        )
        if existing.scalar_one_or_none() is not None:
            continue

        new_source = Source(
            id=deep_source_id,
            name=f"{owner}/{repo} (deep, marketplace-discovered)",
            type="github-deep",
            url=url,
            is_active=True,
            config={
                "star_milestones": [100, 500, 1000, 5000],
                "commit_burst_threshold": 20,
                "watched_files": ["CHANGELOG.md"],
                "marketplace_discovered": True,
                "discovered_from": source_id,
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
                "marketplace_auto_tracked_repo",
                source_id=source_id,
                deep_source_id=deep_source_id,
            )
        except IntegrityError:
            logger.debug(
                "marketplace_repo_already_tracked", deep_source_id=deep_source_id
            )

    return promoted
