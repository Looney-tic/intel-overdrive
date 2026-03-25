"""
One-time script: re-gate filtered items that still have embeddings.

After rebalancing the reference set, filtered items may now pass the relevance
gate. This script resets filtered items WITH embeddings back to 'embedded' status
so the gate_relevance worker re-evaluates them — no re-embedding cost.

Usage:
    python scripts/regate_filtered_items.py           # apply reset
    python scripts/regate_filtered_items.py --dry-run  # count only, no changes

Idempotent: only targets status='filtered' AND embedding IS NOT NULL.
"""
import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from sqlalchemy import text

COUNT_SQL = """
    SELECT COUNT(*)
    FROM intel_items
    WHERE status = 'filtered'
      AND embedding IS NOT NULL
"""

RESET_SQL = """
    UPDATE intel_items
    SET status = 'embedded',
        updated_at = NOW()
    WHERE status = 'filtered'
      AND embedding IS NOT NULL
"""


async def main() -> None:
    dry_run = "--dry-run" in sys.argv

    await init_db()

    try:
        async with _db.async_session_factory() as session:
            count_result = await session.execute(text(COUNT_SQL))
            count = count_result.scalar()

            print(f"Found {count} filtered items with embeddings (free to re-gate)")

            if count == 0:
                print("Nothing to re-gate.")
                return

            if dry_run:
                print("Dry run — no changes applied.")
                return

            await session.execute(text(RESET_SQL))
            await session.commit()
            print(
                f"Reset {count} items to 'embedded' — gate_relevance worker will re-evaluate"
            )

    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
