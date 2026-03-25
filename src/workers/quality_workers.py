"""
Quality scoring worker for the slow ARQ queue.

Cron job that processes GitHub-backed items in batches,
fetching cheap GitHub signals and computing transparent sub-scores.
Also includes broad star/maintenance tracking for all GitHub-URL items.
"""

import json
from datetime import datetime, timezone

import src.core.init_db as _init_db
from sqlalchemy import text
from src.services.quality_service import (
    parse_github_url,
    fetch_github_signals,
    compute_quality_subscores,
    compute_aggregate_quality,
    compute_heuristic_quality,
)
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)

QUALITY_BATCH_SIZE = (
    50  # Each item = 1 GitHub API call; 50/run × 6/hr = 300/hr (limit: 5000/hr)
)


async def score_quality(ctx: dict) -> None:
    """Slow queue cron: compute quality scores for processed GitHub-backed items."""
    if _init_db.async_session_factory is None:
        logger.error("score_quality_called_before_db_init")
        return

    settings = get_settings()

    async with _init_db.async_session_factory() as session:
        # Fetch processed items that have no quality_score_details yet
        # AND have a GitHub URL (quality scoring is GitHub-specific)
        result = await session.execute(
            text(
                """
                SELECT i.id, i.url, i.content, i.title, i.summary, i.tags, s.tier
                FROM intel_items i
                LEFT JOIN sources s ON s.id = i.source_id
                WHERE i.status = 'processed'
                  AND i.quality_score_details IS NULL
                  AND i.url LIKE '%%github.com%%'
                ORDER BY i.created_at ASC
                LIMIT :batch_size
                FOR UPDATE OF i SKIP LOCKED
            """
            ),
            {"batch_size": QUALITY_BATCH_SIZE},
        )
        rows = result.fetchall()

        if not rows:
            return

        scored_count = 0
        for row_idx, row in enumerate(rows):
            item_id, url, content, title, summary, tags, tier = (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
            )

            parsed = parse_github_url(url)
            if parsed is None:
                # URL matched LIKE but failed full parse -- set default score
                await session.execute(
                    text(
                        """
                        UPDATE intel_items
                        SET quality_score = 0.5,
                            quality_score_details = CAST(:details AS json)
                        WHERE id = CAST(:id AS uuid)
                    """
                    ),
                    {
                        "id": str(item_id),
                        "details": json.dumps({"note": "non-parseable GitHub URL"}),
                    },
                )
                continue

            owner, repo = parsed
            signals = await fetch_github_signals(owner, repo, settings.GITHUB_TOKEN)

            if signals is not None and signals.get("rate_limited"):
                # GitHub API rate limit hit — abort the batch immediately.
                # Remaining items are left unscored for the next cycle;
                # no point burning quota on doomed calls.
                logger.warning(
                    "score_quality_rate_limited_abort",
                    scored_so_far=scored_count,
                    remaining_in_batch=len(rows) - row_idx - 1,
                )
                break

            if signals is None:
                # API failure (404, timeout) — use heuristic score instead of
                # punitive 0.1, so the item gets a fair ranking. The broad
                # tracker (track_github_stars_broad) will re-score with real
                # signals if the repo becomes reachable later.
                logger.warning(
                    "score_quality_github_fetch_failed",
                    item_id=str(item_id),
                    url=url,
                )
                fallback_score, fallback_details = compute_heuristic_quality(
                    tier, content, summary, tags, title
                )
                fallback_details["note"] = "github_api_fetch_failed_heuristic_fallback"
                fallback_details["url"] = url
                await session.execute(
                    text(
                        """
                        UPDATE intel_items
                        SET quality_score = :score,
                            quality_score_details = CAST(:details AS json)
                        WHERE id = CAST(:id AS uuid)
                    """
                    ),
                    {
                        "id": str(item_id),
                        "score": fallback_score,
                        "details": json.dumps(fallback_details),
                    },
                )
                continue

            subscores = compute_quality_subscores(signals, content)
            aggregate = compute_aggregate_quality(subscores)

            await session.execute(
                text(
                    """
                    UPDATE intel_items
                    SET quality_score = :score,
                        quality_score_details = CAST(:details AS json)
                    WHERE id = CAST(:id AS uuid)
                """
                ),
                {
                    "id": str(item_id),
                    "score": aggregate,
                    "details": json.dumps(subscores),
                },
            )
            scored_count += 1

        await session.commit()

    # Score non-GitHub items with baseline heuristic (no API call needed)
    # Separate session to avoid holding a single connection during GitHub API calls above
    non_gh_scored = 0
    async with _init_db.async_session_factory() as session:
        non_gh_result = await session.execute(
            text(
                """
                SELECT i.id, i.url, i.content, i.summary, i.tags, s.tier, i.title
                FROM intel_items i
                LEFT JOIN sources s ON s.id = i.source_id
                WHERE i.status = 'processed'
                  AND i.quality_score_details IS NULL
                  AND i.url NOT LIKE '%%github.com%%'
                ORDER BY i.created_at ASC
                LIMIT 100
                FOR UPDATE OF i SKIP LOCKED
            """
            ),
        )
        non_gh_rows = non_gh_result.fetchall()
        for row in non_gh_rows:
            item_id, url, content, summary, tags, tier, title = (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
            )
            score, details = compute_heuristic_quality(
                tier, content, summary, tags, title
            )
            await session.execute(
                text(
                    """
                    UPDATE intel_items
                    SET quality_score = :score,
                        quality_score_details = CAST(:details AS json)
                    WHERE id = CAST(:id AS uuid)
                """
                ),
                {"id": str(item_id), "score": score, "details": json.dumps(details)},
            )
            non_gh_scored += 1

        if non_gh_scored:
            await session.commit()

    logger.info(
        "score_quality_complete", scored=scored_count, non_github_scored=non_gh_scored
    )


BROAD_TRACKER_BATCH_SIZE = 100  # Each item = 1 GitHub API call; runs daily


async def track_github_stars_broad(ctx: dict) -> None:
    """Daily cron: refresh star counts and maintenance signals for ALL GitHub-URL items.

    Complements score_quality (which handles items WITHOUT quality_score_details).
    This worker RE-SCORES items that ALREADY have details, prioritizing those
    that haven't been tracked recently via last_tracked_at.
    """
    if _init_db.async_session_factory is None:
        logger.error("track_github_stars_broad_called_before_db_init")
        return

    settings = get_settings()

    async with _init_db.async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT id, url
                FROM intel_items
                WHERE status = 'processed'
                  AND url LIKE '%%github.com%%'
                ORDER BY COALESCE(
                    (quality_score_details->>'last_tracked_at')::timestamptz,
                    '1970-01-01'::timestamptz
                ) ASC
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
            """
            ),
            {"batch_size": BROAD_TRACKER_BATCH_SIZE},
        )
        rows = result.fetchall()

        if not rows:
            return

        updated_count = 0
        consecutive_failures = 0

        for row in rows:
            item_id, url = row[0], row[1]

            parsed = parse_github_url(url)
            if parsed is None:
                continue

            owner, repo = parsed
            signals = await fetch_github_signals(owner, repo, settings.GITHUB_TOKEN)

            if signals is None:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    logger.warning(
                        "broad_tracker_rate_limited",
                        consecutive_failures=consecutive_failures,
                    )
                    break
                continue

            consecutive_failures = 0

            # Recompute quality scores with fresh signals
            subscores = compute_quality_subscores(signals)
            aggregate = compute_aggregate_quality(subscores)

            # Merge fresh tracking data into existing details
            subscores["stars"] = signals["stars"]
            subscores["forks"] = signals["forks"]
            subscores["open_issues"] = signals["open_issues"]
            subscores["pushed_at"] = signals["pushed_at"]
            subscores["archived"] = signals["archived"]
            subscores["last_tracked_at"] = datetime.now(timezone.utc).isoformat()

            await session.execute(
                text(
                    """
                    UPDATE intel_items
                    SET quality_score = :score,
                        quality_score_details = CAST(:details AS json)
                    WHERE id = CAST(:id AS uuid)
                """
                ),
                {
                    "id": str(item_id),
                    "score": aggregate,
                    "details": json.dumps(subscores),
                },
            )
            updated_count += 1

        await session.commit()

    logger.info(
        "track_github_stars_broad_complete",
        updated=updated_count,
        total_rows=len(rows),
    )
