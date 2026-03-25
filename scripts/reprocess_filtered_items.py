"""
One-time idempotent migration script: requeue filtered release-source items for re-processing.

Resets filtered items from release-type sources (github-releases, pypi) that had
relevance_score >= 0.55 back to status='raw' with NULL embedding, so they get
re-embedded with enriched titles from the updated ingestion workers.

Usage:
    python scripts/reprocess_filtered_items.py           # apply reset
    python scripts/reprocess_filtered_items.py --dry-run  # count only, no changes

Idempotent: re-running on items already reset to 'raw' has no effect (WHERE status='filtered').
"""
import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.core.config import get_settings
from sqlalchemy import text

COUNT_SQL = """
    SELECT COUNT(*)
    FROM intel_items ii
    JOIN sources s ON ii.source_id = s.id
    WHERE ii.status = 'filtered'
      AND ii.relevance_score >= 0.55
      AND (s.type IN ('github-releases', 'pypi')
           OR s.id LIKE 'rss:gh-%'
           OR s.id LIKE 'rss:pypi-%')
"""

RESET_SQL = """
    UPDATE intel_items ii
    SET status = 'raw',
        embedding = NULL,
        updated_at = NOW()
    FROM sources s
    WHERE ii.source_id = s.id
      AND ii.status = 'filtered'
      AND ii.relevance_score >= 0.55
      AND (s.type IN ('github-releases', 'pypi')
           OR s.id LIKE 'rss:gh-%'
           OR s.id LIKE 'rss:pypi-%')
"""


async def main() -> None:
    dry_run = "--dry-run" in sys.argv

    settings = get_settings()
    await init_db()

    try:
        async with _db.async_session_factory() as session:
            # Count eligible items
            count_result = await session.execute(text(COUNT_SQL))
            count = count_result.scalar()

            print(f"Found {count} filtered release-source items with score >= 0.55")

            if count == 0:
                print("Nothing to reprocess.")
                return

            if dry_run:
                print("Dry run — no changes applied.")
                return

            # Apply reset
            await session.execute(text(RESET_SQL))
            await session.commit()
            print(f"Reset {count} items to raw for re-embedding with enriched titles")

    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
