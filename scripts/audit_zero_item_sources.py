"""Diagnostic script: find active sources with zero ingested items.

Queries the database for active sources that have never produced any intel_items.
Groups results by source type, identifies tier1 critical sources, and outputs
recommended SQL UPDATE statements for operator review.

Usage:
    python scripts/audit_zero_item_sources.py

Does NOT auto-fix — outputs diagnostics and recommendations only.
"""

import asyncio
import json
import sys
import os

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text

from src.core.init_db import init_db, async_session_factory


# Critical tier1 sources that should always have items
TIER1_CRITICAL_NAMES = [
    "claude code changelog",
    "gemini api",
    "aider",
    "mistral",
    "anthropic",
    "openai",
    "cursor",
]


async def main() -> None:
    await init_db()

    if async_session_factory is None:
        print("ERROR: Database not initialized")
        return

    async with async_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT s.id, s.name, s.url, s.type, s.config,
                       s.last_successful_poll, s.is_active, s.tier,
                       s.consecutive_errors, s.last_fetched_at
                FROM sources s
                WHERE s.is_active = true
                  AND s.id NOT IN (
                      SELECT DISTINCT source_id
                      FROM intel_items
                      WHERE source_id IS NOT NULL
                  )
                ORDER BY s.tier ASC, s.type ASC, s.name ASC
                """
            )
        )
        rows = result.fetchall()

        if not rows:
            print("All active sources have at least one item. Nothing to report.")
            return

        # Group by type
        by_type: dict[str, list] = {}
        for row in rows:
            source_type = row[3] or "unknown"
            by_type.setdefault(source_type, []).append(row)

        type_counts = {t: len(sources) for t, sources in by_type.items()}
        total = len(rows)
        print(f"\n{'='*70}")
        print(f" ZERO-ITEM SOURCE AUDIT")
        print(f" {total} active sources with zero items")
        print(f" Type breakdown: {type_counts}")
        print(f"{'='*70}\n")

        # Identify tier1 critical sources
        critical_found = []
        for row in rows:
            name_lower = (row[1] or "").lower()
            if any(crit in name_lower for crit in TIER1_CRITICAL_NAMES):
                critical_found.append(row)

        if critical_found:
            print("CRITICAL — Tier1 sources with zero items:")
            print("-" * 50)
            for row in critical_found:
                config = row[4] or {}
                print(f"  Source: {row[1]} (id={row[0]})")
                print(f"  URL: {row[2]}")
                print(f"  Type: {row[3]}, Tier: {row[7]}")
                print(f"  Last successful poll: {row[5]}")
                print(f"  Last fetched at: {row[9]}")
                print(f"  Consecutive errors: {row[8]}")
                print(f"  Config: {json.dumps(config, indent=4)}")
                print()
            print()

        # Details by source type
        for source_type, sources in sorted(by_type.items()):
            print(f"\n--- {source_type.upper()} ({len(sources)} sources) ---")
            for row in sources:
                source_id = row[0]
                name = row[1]
                url = row[2]
                config = row[4] or {}
                last_poll = row[5]
                tier = row[7]
                errors = row[8]

                status_note = ""
                if errors and errors > 0:
                    status_note = f" [!{errors} errors]"

                print(f"  [{tier or '?'}] {name}{status_note}")
                print(f"       URL: {url}")
                print(f"       Last poll: {last_poll or 'never'}")

                # For scraper-type sources, check CSS selectors
                if source_type == "scraper" and config:
                    selector = config.get("item_selector") or config.get("css_selector")
                    if selector:
                        print(f"       CSS selector: {selector}")
                    else:
                        print("       WARNING: No CSS selector in config")

        # Output recommended SQL for operator review
        print(f"\n\n{'='*70}")
        print(" RECOMMENDED ACTIONS (review before executing)")
        print(f"{'='*70}\n")

        erroring = [r for r in rows if (r[8] or 0) > 0]
        never_polled = [r for r in rows if r[5] is None]

        if never_polled:
            ids = ", ".join(f"'{r[0]}'" for r in never_polled)
            print(f"-- {len(never_polled)} sources never successfully polled:")
            print(f"-- Review configs, then trigger manual poll or deactivate:")
            print(f"UPDATE sources SET is_active = false WHERE id IN ({ids});\n")

        if erroring:
            ids = ", ".join(f"'{r[0]}'" for r in erroring)
            print(f"-- {len(erroring)} sources with errors but zero items:")
            print(f"-- Reset errors to retry, or deactivate:")
            print(f"UPDATE sources SET consecutive_errors = 0 WHERE id IN ({ids});\n")

        print(f"\nTotal: {total} active sources with zero items.")
        print("Run this script periodically to track source health.")


if __name__ == "__main__":
    asyncio.run(main())
