"""Backfill quality_score for existing items using the updated formula.

Recomputes quality_score from stored signals in quality_score_details JSON
(no GitHub API calls needed). Processes in batches of 500.

Usage:
    python scripts/backfill_quality.py

Idempotent: safe to run multiple times. Skips items with heuristic-based scores.
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.core.config import get_settings
from src.services.quality_service import (
    compute_quality_subscores,
    compute_aggregate_quality,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BATCH_SIZE = 500


async def backfill():
    """Recompute quality_score for all items with stored GitHub signals."""
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    total_updated = 0
    batch_num = 0
    old_scores = []
    new_scores = []

    async with async_session() as session:
        # Count total eligible items (have quality_score_details with method != heuristic)
        count_sql = text(
            """
            SELECT COUNT(*) FROM intel_items
            WHERE quality_score_details IS NOT NULL
              AND quality_score_details != '{}'::jsonb
              AND COALESCE(quality_score_details->>'method', '') != 'heuristic'
            """
        )
        count_result = await session.execute(count_sql)
        total_eligible = count_result.scalar() or 0
        logger.info(f"Found {total_eligible} eligible items to backfill")

        if total_eligible == 0:
            logger.info("No items to backfill. Exiting.")
            await engine.dispose()
            return

        # Process in batches using OFFSET pagination
        while True:
            batch_num += 1
            fetch_sql = text(
                """
                SELECT id, quality_score, quality_score_details
                FROM intel_items
                WHERE quality_score_details IS NOT NULL
                  AND quality_score_details != '{}'::jsonb
                  AND COALESCE(quality_score_details->>'method', '') != 'heuristic'
                ORDER BY id
                LIMIT :batch_size
                OFFSET :offset
                """
            )
            result = await session.execute(
                fetch_sql,
                {"batch_size": BATCH_SIZE, "offset": (batch_num - 1) * BATCH_SIZE},
            )
            rows = result.mappings().all()

            if not rows:
                break

            batch_updated = 0
            for row in rows:
                item_id = row["id"]
                old_score = row["quality_score"]
                details = row["quality_score_details"]

                # Parse details JSON if needed
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except (json.JSONDecodeError, TypeError):
                        continue

                if not isinstance(details, dict):
                    continue

                # Extract signals from stored details
                signals = details.get("signals")
                if not signals or not isinstance(signals, dict):
                    # Try using the details dict itself as signals (older format)
                    if "stars" in details or "pushed_at" in details:
                        signals = details
                    else:
                        continue

                # Recompute subscores with updated formula (now includes community)
                try:
                    subscores = compute_quality_subscores(signals)
                    new_score = compute_aggregate_quality(subscores)
                except Exception as e:
                    logger.warning(f"Error computing score for item {item_id}: {e}")
                    continue

                # Update the item
                new_details = {
                    "method": "github_signals",
                    "subscores": {
                        "maintenance": subscores["maintenance"],
                        "community": subscores["community"],
                        "security": subscores["security"],
                        "compatibility": subscores["compatibility"],
                    },
                    "signals": subscores["signals"],
                    "is_stale": subscores["is_stale"],
                    "findings": subscores.get("findings", []),
                    "backfilled": True,
                }

                update_sql = text(
                    """
                    UPDATE intel_items
                    SET quality_score = :new_score,
                        quality_score_details = :new_details
                    WHERE id = :item_id
                    """
                )
                await session.execute(
                    update_sql,
                    {
                        "new_score": new_score,
                        "new_details": json.dumps(new_details),
                        "item_id": item_id,
                    },
                )

                old_scores.append(old_score or 0.0)
                new_scores.append(new_score)
                batch_updated += 1

            await session.commit()
            total_updated += batch_updated
            logger.info(
                f"Batch {batch_num}: updated {batch_updated} items "
                f"(total: {total_updated}/{total_eligible})"
            )

    # Log score distribution summary
    if old_scores:
        avg_old = sum(old_scores) / len(old_scores)
        avg_new = sum(new_scores) / len(new_scores)
        logger.info(
            f"Score distribution - Old avg: {avg_old:.3f}, New avg: {avg_new:.3f}, "
            f"Delta: {avg_new - avg_old:+.3f}"
        )

    logger.info(f"Backfill complete. Updated {total_updated} items total.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill())
