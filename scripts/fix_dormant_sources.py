"""Fix dormant sources and seed per-source relevance thresholds.

Usage: python scripts/fix_dormant_sources.py

1. Sets relevance_threshold=0.50 for release-type sources (github-releases, pypi)
   and named release RSS feeds — these are pre-qualified by source identity, so a
   lower threshold prevents over-filtering of terse version-string titles.

2. Diagnoses dormant scraper sources (never successfully polled) — prints their
   current selector configs so the operator can assess what's broken.

3. Reactivates dormant sources that were disabled by circuit breaker before ever
   succeeding, but only if consecutive_errors < 3 (truly dead sources stay off).

Idempotent: safe to re-run.
"""

import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.core.config import get_settings
from src.models.models import Source
from sqlalchemy import select, update

# Named release RSS sources that should use a lower threshold
RELEASE_SOURCE_IDS = [
    "rss:gh-crewai",
    "rss:gh-pydantic-ai",
    "rss:gh-langchain",
    "rss:gh-openai-codex",
    "rss:gh-openai-node",
    "rss:gh-openai-python",
    "rss:gh-autogen",
    "rss:gh-copilot-changelog",
]

# Source types that are inherently release-focused
RELEASE_SOURCE_TYPES = ["github-releases", "pypi"]

# Dormant scraper sources to diagnose
DORMANT_SCRAPER_IDS = [
    "scraper:openai-changelog",
    "scraper:cursor-blog",
    "scraper:cursor-changelog",
    "scraper:aider-history",
]

RELEASE_THRESHOLD = 0.50


async def main():
    settings = get_settings()
    await init_db()

    async with _db.async_session_factory() as session:
        # ---------------------------------------------------------------
        # 1. Set threshold for named release sources
        # ---------------------------------------------------------------
        named_result = await session.execute(
            select(Source).where(Source.id.in_(RELEASE_SOURCE_IDS))
        )
        named_sources = named_result.scalars().all()

        named_count = 0
        for source in named_sources:
            current = source.config.get("relevance_threshold")
            if current != RELEASE_THRESHOLD:
                # Dict reassignment triggers SQLAlchemy JSON mutation detection
                source.config = {
                    **source.config,
                    "relevance_threshold": RELEASE_THRESHOLD,
                }
                named_count += 1
                print(
                    f"  [threshold] {source.id}: set relevance_threshold={RELEASE_THRESHOLD}"
                )
            else:
                print(f"  [threshold] {source.id}: already {RELEASE_THRESHOLD} (skip)")

        # ---------------------------------------------------------------
        # 2. Set threshold for release-type sources (github-releases, pypi)
        # ---------------------------------------------------------------
        type_result = await session.execute(
            select(Source).where(Source.type.in_(RELEASE_SOURCE_TYPES))
        )
        type_sources = type_result.scalars().all()

        type_count = 0
        for source in type_sources:
            current = source.config.get("relevance_threshold")
            if current != RELEASE_THRESHOLD:
                source.config = {
                    **source.config,
                    "relevance_threshold": RELEASE_THRESHOLD,
                }
                type_count += 1
                print(
                    f"  [threshold] {source.id} (type={source.type}): set relevance_threshold={RELEASE_THRESHOLD}"
                )
            else:
                print(
                    f"  [threshold] {source.id} (type={source.type}): already {RELEASE_THRESHOLD} (skip)"
                )

        await session.commit()
        print(f"\nThresholds updated: {named_count} named + {type_count} by type")

        # ---------------------------------------------------------------
        # 3. Diagnose dormant scraper sources
        # ---------------------------------------------------------------
        print("\n--- Dormant Scraper Diagnosis ---")

        dormant_result = await session.execute(
            select(Source).where(Source.id.in_(DORMANT_SCRAPER_IDS))
        )
        dormant_sources = dormant_result.scalars().all()

        if not dormant_sources:
            print("  No dormant scraper sources found in database.")
        else:
            for source in dormant_sources:
                is_dormant = source.last_successful_poll is None
                selectors = source.config.get("selectors", {})
                print(f"\n  Source: {source.id}")
                print(f"    Name: {source.name}")
                print(f"    Active: {source.is_active}")
                print(f"    Errors: {source.consecutive_errors}")
                print(f"    Last success: {source.last_successful_poll}")
                print(f"    Selectors: {selectors}")
                if is_dormant:
                    print(
                        f"    WARNING: Never successfully polled — check URL and selectors"
                    )

        # ---------------------------------------------------------------
        # 4. Reactivate dormant sources with low error counts
        # ---------------------------------------------------------------
        print("\n--- Reactivation ---")

        reactivate_result = await session.execute(
            select(Source).where(
                Source.is_active == False,  # noqa: E712
                Source.last_successful_poll.is_(None),
                Source.consecutive_errors < 3,
            )
        )
        reactivate_sources = reactivate_result.scalars().all()

        reactivated_count = 0
        for source in reactivate_sources:
            source.is_active = True
            source.consecutive_errors = 0
            reactivated_count += 1
            print(
                f"  [reactivated] {source.id} (was errors={source.consecutive_errors})"
            )

        if reactivated_count == 0:
            print("  No sources eligible for reactivation.")

        await session.commit()
        print(f"\nReactivated: {reactivated_count} sources")

    await close_db()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
