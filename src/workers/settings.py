from arq import cron
from arq.connections import RedisSettings
from src.core.config import get_settings
from src.core.init_db import init_db, close_db
from src.core.logger import configure_logging, get_logger
from src.services.spend_tracker import SpendTracker, SpendLimitExceeded
import src.core.init_db as _db

logger = get_logger(__name__)
from src.workers.ingest_rss import ingest_rss_source, poll_rss_sources
from src.workers.ingest_github import ingest_github_source, poll_github_sources
from src.workers.ingest_hn import ingest_hn_source, poll_hn_sources
from src.workers.ingest_reddit import ingest_reddit_source, poll_reddit_sources
from src.workers.ingest_youtube import ingest_youtube_source, poll_youtube_sources
from src.workers.ingest_gh_releases import (
    ingest_gh_releases_source,
    poll_gh_releases_sources,
)
from src.workers.ingest_npm import ingest_npm_source, poll_npm_sources
from src.workers.ingest_mcp_registry import (
    ingest_mcp_registry_source,
    poll_mcp_registry_sources,
)
from src.workers.ingest_awesome import ingest_awesome_source, poll_awesome_sources
from src.workers.ingest_releasebot import (
    ingest_releasebot_source,
    poll_releasebot_sources,
)
from src.workers.ingest_arxiv import ingest_arxiv_source, poll_arxiv_sources
from src.workers.ingest_github_deep import (
    ingest_github_deep_source,
    poll_github_deep_sources,
)
from src.workers.ingest_scraper import ingest_scraper_source, poll_scraper_sources
from src.workers.ingest_github_discussions import (
    ingest_github_discussions_source,
    poll_github_discussions_sources,
)
from src.workers.ingest_pypi import ingest_pypi_source, poll_pypi_sources
from src.workers.ingest_vscode import ingest_vscode_source, poll_vscode_sources
from src.workers.ingest_bluesky import ingest_bluesky_source, poll_bluesky_sources
from src.workers.ingest_sitemap import ingest_sitemap_source, poll_sitemap_sources
from src.workers.ingest_marketplace import (
    ingest_marketplace_source,
    poll_marketplace_sources,
)
from src.workers.pipeline_workers import embed_items, gate_relevance, classify_items
from src.workers.cluster_worker import cluster_items
from src.workers.alert_workers import check_alerts
from src.workers.quality_workers import score_quality, track_github_stars_broad
from src.workers.dms_worker import (
    check_dead_mans_switch,
    update_ingestion_heartbeat,
    DMS_KEY,
)
from src.workers.library_worker import (
    synthesize_library_topics,
    graduate_candidates,
    detect_stale_entries,
)
from src.workers.slack_digest_worker import post_daily_digest
from src.workers.user_activity_worker import post_user_activity_digest
from src.workers.source_recovery_worker import check_source_recovery_cron
from src.workers.storage_worker import cleanup_filtered_embeddings
from src.workers.readme_refresh_worker import (
    backfill_readmes,
    star_patrol,
    refresh_readmes,
)
from src.workers.storage_monitor import check_storage
from src.workers.source_tier_worker import adjust_source_tiers
from src.workers.slack_source_health_worker import post_weekly_source_health
from src.workers.slack_coverage_gap_worker import check_coverage_gaps

_settings = get_settings()


async def startup(ctx: dict) -> None:
    configure_logging()
    await init_db()
    # Spend gate startup visibility (OPS-03): log warning if limit already hit.
    # Do NOT raise — workers still do ingestion; only LLM calls are individually gated.
    redis_client = ctx["redis"]
    tracker = SpendTracker(redis_client)
    try:
        await tracker.check_spend_gate()
    except SpendLimitExceeded as e:
        logger.warning(
            "SPEND_GATE_BLOCKED_AT_STARTUP",
            current=e.current,
            limit=e.limit,
        )
    # Seed DMS heartbeat on cold start to prevent false alert
    existing = await redis_client.exists(DMS_KEY)
    if not existing:
        from datetime import datetime, timezone

        await redis_client.set(
            DMS_KEY,
            datetime.now(timezone.utc).isoformat(),
            ex=172800,
        )
        logger.info("DMS_HEARTBEAT_SEEDED")


async def shutdown(ctx: dict) -> None:
    await close_db()


class WorkerSettings:
    """Fast queue: cron dispatchers + per-source ingest jobs (I/O-bound)."""

    functions = [
        ingest_rss_source,
        ingest_github_source,
        ingest_hn_source,
        ingest_reddit_source,
        ingest_youtube_source,
        ingest_gh_releases_source,
        ingest_npm_source,
        ingest_mcp_registry_source,
        ingest_awesome_source,
        ingest_releasebot_source,
        ingest_arxiv_source,
        ingest_github_deep_source,
        ingest_scraper_source,
        ingest_github_discussions_source,
        ingest_pypi_source,
        ingest_vscode_source,
        ingest_bluesky_source,
        ingest_sitemap_source,
        ingest_marketplace_source,
    ]
    cron_jobs = [
        cron(poll_rss_sources, minute={0, 30}),  # every 30 min
        cron(poll_github_sources, minute={15, 45}),  # every 30 min, offset +15
        cron(poll_hn_sources, minute={5, 35}),  # every 30 min, offset +5
        cron(poll_reddit_sources, minute={10, 40}),  # every 30 min, offset +10
        cron(poll_youtube_sources, minute={20, 50}),  # every 30 min, offset +20
        cron(poll_gh_releases_sources, minute={25, 55}),  # every 30 min, offset +25
        cron(poll_npm_sources, minute={8, 38}),  # every 30 min, offset +8
        cron(poll_mcp_registry_sources, minute={18, 48}),  # every 30 min, offset +18
        cron(poll_awesome_sources, minute={12, 42}),  # every 30 min, offset +12
        cron(poll_releasebot_sources, minute={22, 52}),  # every 30 min, offset +22
        cron(
            poll_arxiv_sources, hour={1}, minute={2}
        ),  # once/day at 1:02am UTC (arXiv updates at midnight)
        cron(poll_github_deep_sources, minute={3, 33}),  # every 30 min, offset +3
        cron(
            poll_scraper_sources, hour={0, 6, 12, 18}, minute={7}
        ),  # every 6 hours, offset +7
        cron(
            poll_github_discussions_sources, minute={17, 47}
        ),  # every 30 min, offset +17
        cron(
            poll_pypi_sources, hour={3}, minute={1}
        ),  # once/day at 3:01am UTC (PyPI daily index)
        cron(
            poll_vscode_sources, hour={4, 16}, minute={23}
        ),  # twice/day at 4:23am and 4:23pm UTC
        cron(poll_bluesky_sources, minute={16, 46}),  # every 30 min, offset +16
        cron(
            poll_sitemap_sources, hour={6, 18}, minute={19}
        ),  # twice/day at 6:19am and 6:19pm UTC
        cron(
            poll_marketplace_sources, hour={2, 14}, minute={27}
        ),  # twice/day at 2:27am and 2:27pm UTC
    ]
    queue_name = "fast"
    redis_settings = RedisSettings.from_dsn(_settings.REDIS_URL)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 50
    max_tries = 3
    health_check_interval = 300


class SlowWorkerSettings:
    """Slow queue: embedding + LLM classification (Phase 3)."""

    functions: list = [
        embed_items,
        gate_relevance,
        classify_items,
        cluster_items,
        check_alerts,
        score_quality,
        track_github_stars_broad,
        check_dead_mans_switch,
        graduate_candidates,
        detect_stale_entries,
        synthesize_library_topics,
        post_daily_digest,
        post_user_activity_digest,
        check_source_recovery_cron,
        cleanup_filtered_embeddings,
        check_storage,
        backfill_readmes,
        star_patrol,
        refresh_readmes,
        adjust_source_tiers,
        post_weekly_source_health,
        check_coverage_gaps,
    ]
    cron_jobs: list = [
        cron(
            embed_items, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}
        ),  # every 5 min
        cron(
            gate_relevance, minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57}
        ),  # every 5 min, offset +2
        cron(
            classify_items,
            minute={4, 34},
            timeout=1800,  # 30 min — Batch API takes 15+ min for large batches
        ),  # every 30 min — pools items for larger batches + better prompt cache hits
        cron(
            cluster_items, minute={0, 30}, run_at_startup=False
        ),  # every 30 min — clusters processed items by embedding similarity
        cron(
            check_alerts,
            minute={1, 11, 21, 31, 41, 51},
        ),  # every 10 min, offset +1 (after classify)
        cron(
            score_quality, minute={6, 36}
        ),  # every 30 min, offset +6 — safety net for items that missed inline scoring
        cron(check_dead_mans_switch, hour={0, 6, 12, 18}),  # every 6 hours — DMS check
        cron(
            graduate_candidates, hour={2}, minute={45}
        ),  # daily 2:45am UTC — score and promote intel_items to library_items
        cron(
            detect_stale_entries, hour={2}, minute={46}
        ),  # daily 2:46am UTC — flag stale entries, archive abandoned reviews (offset +1 from graduation)
        cron(
            synthesize_library_topics, hour={3}, minute={30}
        ),  # daily 3:30am UTC — Haiku synthesis of top topic guides
        cron(
            track_github_stars_broad, hour={5}, minute={30}
        ),  # daily 5:30am UTC — broad star/maintenance tracking for all GitHub-URL items
        cron(
            post_daily_digest, hour={8}, minute={0}
        ),  # daily 8:00am UTC — Slack team digest
        cron(
            post_user_activity_digest, hour={9}, minute={0}
        ),  # daily 9:00am UTC — admin user activity report
        cron(
            check_source_recovery_cron, hour={6, 18}, minute={15}
        ),  # every 12h at 6:15am/6:15pm UTC — re-enable deactivated sources after 48h
        cron(
            cleanup_filtered_embeddings, hour={4}, minute={0}
        ),  # daily 4:00am UTC — null embeddings on filtered items to reclaim storage
        cron(
            check_storage, hour={3, 9, 15, 21}, minute={45}
        ),  # every 6h at :45 — DB size monitoring with Slack alerts at 80%/90%
        cron(
            backfill_readmes, minute={5, 15, 25, 35, 45, 55}, timeout=1800
        ),  # every 10 min — aggressive README backfill for items with short content (1000/run)
        cron(
            star_patrol, hour={6}, minute={0}
        ),  # daily 6:00am UTC — re-check star counts, detect rising repos (500/run)
        cron(
            refresh_readmes, weekday={0}, hour={7}, minute={0}
        ),  # weekly Monday 7:00am UTC — re-fetch READMEs for high-star repos (200/run)
        cron(
            adjust_source_tiers, weekday={6}, hour={3}, minute={0}
        ),  # weekly Sunday 3:00am UTC — auto-adjust source tiers based on signal ratios
        cron(
            post_weekly_source_health, weekday={6}, hour={9}, minute={0}
        ),  # weekly Sunday 9:00am UTC — source health report
        cron(
            check_coverage_gaps, hour={9}, minute={30}
        ),  # daily 9:30am UTC — coverage gap detection from auto_miss feedback
    ]
    queue_name = "slow"
    redis_settings = RedisSettings.from_dsn(_settings.REDIS_URL)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 5
    max_tries = 3
    job_timeout = 1800  # 30 min — Batch API can take 15+ min for large batches
    health_check_interval = 300
