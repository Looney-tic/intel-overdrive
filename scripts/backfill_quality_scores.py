"""
One-time backfill: rescore all heuristic-scored items with the new penalty formula.

Selects items where quality_score_details contains 'heuristic' or quality_score = 0.1
(GitHub API failures), fetches their tier/content/summary/tags/title, and recomputes
using compute_heuristic_quality with the new penalty formula.

Usage:
    python -m scripts.backfill_quality_scores

Processes in batches of 500, commits after each batch. Safe to re-run (idempotent).
"""

import asyncio
import json
import sys

import src.core.init_db as _init_db
from sqlalchemy import text
from src.core.config import get_settings
from src.core.init_db import init_db, close_db
from src.core.logger import configure_logging, get_logger
from src.services.quality_service import compute_heuristic_quality

logger = get_logger(__name__)

BATCH_SIZE = 500


async def backfill() -> None:
    configure_logging()
    await init_db()

    if _init_db.async_session_factory is None:
        logger.error("backfill_quality_scores_db_not_initialized")
        return

    total_rescored = 0
    score_buckets: dict[str, int] = {}  # "0.0-0.1" -> count for distribution

    try:
        offset = 0
        while True:
            async with _init_db.async_session_factory() as session:
                result = await session.execute(
                    text(
                        """
                        SELECT i.id, i.title, i.content, i.summary, i.tags,
                               s.tier
                        FROM intel_items i
                        LEFT JOIN sources s ON s.id = i.source_id
                        WHERE (
                            i.quality_score_details::text LIKE '%heuristic%'
                            OR i.quality_score = 0.1
                        )
                        ORDER BY i.created_at ASC
                        LIMIT :batch_size
                        OFFSET :offset
                    """
                    ),
                    {"batch_size": BATCH_SIZE, "offset": offset},
                )
                rows = result.fetchall()

                if not rows:
                    break

                batch_count = 0
                for row in rows:
                    item_id = row[0]
                    title = row[1] or ""
                    content = row[2] or ""
                    summary = row[3] or ""
                    tags = row[4]
                    tier = row[5] or "tier3"

                    quality_score, quality_details = compute_heuristic_quality(
                        tier, content, summary, tags, title
                    )

                    await session.execute(
                        text(
                            """
                            UPDATE intel_items
                            SET quality_score = :qs,
                                quality_score_details = CAST(:details AS json)
                            WHERE id = CAST(:id AS uuid)
                        """
                        ),
                        {
                            "qs": quality_score,
                            "details": json.dumps(quality_details),
                            "id": str(item_id),
                        },
                    )
                    batch_count += 1

                    # Track score distribution
                    bucket = f"{quality_score:.1f}"
                    score_buckets[bucket] = score_buckets.get(bucket, 0) + 1

                await session.commit()
                total_rescored += batch_count
                logger.info(
                    "backfill_quality_batch",
                    batch_size=batch_count,
                    total_so_far=total_rescored,
                )
                offset += BATCH_SIZE

        logger.info(
            "backfill_quality_complete",
            total_rescored=total_rescored,
            score_distribution=score_buckets,
        )
        print(f"\nBackfill complete: {total_rescored} items rescored")
        print("Score distribution:")
        for bucket in sorted(score_buckets.keys()):
            print(f"  {bucket}: {score_buckets[bucket]} items")

    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(backfill())
