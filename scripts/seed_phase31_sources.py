"""Seed script for Phase 31 web framework release sources.

Usage: python scripts/seed_phase31_sources.py

Creates sources for:
- Next.js, React, TypeScript, Node.js releases (core web frameworks)
- Vite, Prisma, Svelte releases (high-value extras)

All sources use the existing github-releases type and will be ingested
by the existing ingest_gh_releases.py worker -- no new worker code needed.

Idempotent: checks if source.id exists before inserting, skips if present.
"""

import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.models.models import Source
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Source definitions
# ---------------------------------------------------------------------------

FRAMEWORK_SOURCES = [
    # Required: core web frameworks (tier1, hourly poll)
    {
        "id": "github-releases:vercel/next.js",
        "name": "Next.js Releases",
        "type": "github-releases",
        "url": "https://github.com/vercel/next.js/releases.atom",
        "poll_interval_seconds": 3600,
        "tier": "tier1",
        "config": {},
    },
    {
        "id": "github-releases:facebook/react",
        "name": "React Releases",
        "type": "github-releases",
        "url": "https://github.com/facebook/react/releases.atom",
        "poll_interval_seconds": 3600,
        "tier": "tier1",
        "config": {},
    },
    {
        "id": "github-releases:microsoft/TypeScript",
        "name": "TypeScript Releases",
        "type": "github-releases",
        "url": "https://github.com/microsoft/TypeScript/releases.atom",
        "poll_interval_seconds": 3600,
        "tier": "tier1",
        "config": {},
    },
    {
        "id": "github-releases:nodejs/node",
        "name": "Node.js Releases",
        "type": "github-releases",
        "url": "https://github.com/nodejs/node/releases.atom",
        "poll_interval_seconds": 86400,  # daily -- high release cadence
        "tier": "tier1",
        "config": {},
    },
    # High-value extras (tier1, hourly poll)
    {
        "id": "github-releases:vitejs/vite",
        "name": "Vite Releases",
        "type": "github-releases",
        "url": "https://github.com/vitejs/vite/releases.atom",
        "poll_interval_seconds": 3600,
        "tier": "tier1",
        "config": {},
    },
    {
        "id": "github-releases:prisma/prisma",
        "name": "Prisma Releases",
        "type": "github-releases",
        "url": "https://github.com/prisma/prisma/releases.atom",
        "poll_interval_seconds": 3600,
        "tier": "tier1",
        "config": {},
    },
    {
        "id": "github-releases:sveltejs/svelte",
        "name": "Svelte Releases",
        "type": "github-releases",
        "url": "https://github.com/sveltejs/svelte/releases.atom",
        "poll_interval_seconds": 3600,
        "tier": "tier1",
        "config": {},
    },
]


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------


async def seed() -> None:
    await init_db()

    async with _db.async_session_factory() as session:
        inserted = 0
        skipped = 0

        for spec in FRAMEWORK_SOURCES:
            result = await session.execute(
                select(Source).where(Source.id == spec["id"])
            )
            if result.scalar_one_or_none():
                print(f"  SKIP  {spec['id']} (already exists)")
                skipped += 1
                continue

            source = Source(
                id=spec["id"],
                name=spec["name"],
                type=spec["type"],
                url=spec["url"],
                is_active=True,
                poll_interval_seconds=spec["poll_interval_seconds"],
                tier=spec["tier"],
                config=spec.get("config", {}),
            )
            session.add(source)
            print(f"  ADD   {spec['id']}")
            inserted += 1

        await session.commit()
        print(
            f"\nFramework sources: {inserted} inserted, {skipped} skipped "
            f"(total {len(FRAMEWORK_SOURCES)})"
        )

    await close_db()


if __name__ == "__main__":
    asyncio.run(seed())
