"""
One-time script: recalculate relevance_score for all processed items using
the recalibrated formula (0.65/0.15/0.20 weights).

Since only the composite score is stored (not content_match separately),
we reverse-engineer content_match from the old formula:
  old_score = content_match * 0.50 + authority * 0.30 + freshness * 0.20
  content_match = (old_score - authority * 0.30 - freshness * 0.20) / 0.50

Then apply the new formula:
  new_score = content_match * 0.65 + authority * 0.15 + freshness * 0.20

Usage:
    python scripts/backfill_scores.py           # apply recalculation
    python scripts/backfill_scores.py --dry-run  # show stats without writing

Idempotent: running multiple times converges (second run is a no-op since
scores already match the new formula).
"""
import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.services.scoring_service import (
    compute_authority_score,
    compute_freshness_score,
    compute_relevance_score,
)
from sqlalchemy import text

# Old formula weights (before recalibration)
OLD_CONTENT_W = 0.50
OLD_AUTHORITY_W = 0.30
OLD_FRESHNESS_W = 0.20

# Batch size for UPDATE statements
BATCH_SIZE = 1000

FETCH_SQL = """
    SELECT i.id, i.relevance_score, i.created_at, s.tier
    FROM intel_items i
    JOIN sources s ON i.source_id = s.id
    WHERE i.status = 'processed'
      AND i.relevance_score IS NOT NULL
    ORDER BY i.id
"""

UPDATE_SQL = """
    UPDATE intel_items
    SET relevance_score = :new_score,
        updated_at = NOW()
    WHERE id = :item_id
"""


async def main() -> None:
    dry_run = "--dry-run" in sys.argv

    await init_db()

    try:
        async with _db.async_session_factory() as session:
            result = await session.execute(text(FETCH_SQL))
            rows = result.fetchall()

            if not rows:
                print("No processed items with scores found. Nothing to backfill.")
                return

            print(f"Found {len(rows)} processed items to recalculate")

            old_scores = []
            new_scores = []
            updates = []
            skipped = 0

            for item_id, old_score, created_at, tier in rows:
                if old_score is None:
                    skipped += 1
                    continue

                authority = compute_authority_score(tier or "tier3")
                freshness = compute_freshness_score(created_at)

                # Reverse-engineer content_match from old formula
                content_match = (
                    old_score
                    - authority * OLD_AUTHORITY_W
                    - freshness * OLD_FRESHNESS_W
                ) / OLD_CONTENT_W

                # Clamp content_match to [0, 1] for safety (rounding errors)
                content_match = max(0.0, min(1.0, content_match))

                # Apply new formula via the scoring service (ensures consistency)
                new_score = compute_relevance_score(
                    content_match, tier or "tier3", {}, created_at
                )

                old_scores.append(float(old_score))
                new_scores.append(float(new_score))
                updates.append({"item_id": item_id, "new_score": new_score})

            if not updates:
                print("No items to update.")
                return

            old_avg = sum(old_scores) / len(old_scores)
            new_avg = sum(new_scores) / len(new_scores)
            old_min, old_max = min(old_scores), max(old_scores)
            new_min, new_max = min(new_scores), max(new_scores)

            print(f"\nScore statistics:")
            print(f"  Old: avg={old_avg:.4f}, range=[{old_min:.4f}, {old_max:.4f}]")
            print(f"  New: avg={new_avg:.4f}, range=[{new_min:.4f}, {new_max:.4f}]")
            print(f"  Items to update: {len(updates)}")
            if skipped:
                print(f"  Skipped (null score): {skipped}")

            if dry_run:
                print("\nDry run -- no changes applied.")
                return

            # Batch update
            total_updated = 0
            for i in range(0, len(updates), BATCH_SIZE):
                batch = updates[i : i + BATCH_SIZE]
                for params in batch:
                    await session.execute(text(UPDATE_SQL), params)
                total_updated += len(batch)
                print(f"  Updated {total_updated}/{len(updates)} items...")

            await session.commit()
            print(f"\nDone. Recalculated {total_updated} items.")

    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
