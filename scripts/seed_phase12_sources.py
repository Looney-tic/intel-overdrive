"""Seed script for Phase 12 extended ingestion sources.

Usage: python scripts/seed_phase12_sources.py

Creates sources for:
- PyPI package releases (Claude/MCP ecosystem packages)
- VS Code Marketplace extensions (AI coding tools)
- Bluesky social search feeds
- Sitemap crawl targets (Anthropic and OpenAI docs)
- GitHub Discussions threads
- Newsletter email ingest (Mailgun webhook receiver)

Idempotent: checks if source.id exists before inserting, skips if present.

Also updates existing github-deep sources that lack watched_files to add
CHANGELOG.md monitoring (Phase 12 CHANGELOG diffing feature).
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

PYPI_SOURCES = [
    {
        "id": "pypi:claude-ecosystem",
        "name": "PyPI Claude Ecosystem Packages",
        "type": "pypi",
        "url": "https://pypi.org",
        "poll_interval_seconds": 86400,  # daily
        "tier": "tier1",
        "config": {
            "packages": [
                "anthropic",
                "claude-code",
                "mcp",
                "voyageai",
                "instructor",
                "pydantic-ai",
                "langchain-anthropic",
                "litellm",
                "marvin",
                "openai",
            ]
        },
    },
]

VSCODE_SOURCES = [
    {
        "id": "vscode:ai-extensions",
        "name": "VS Code AI Extensions",
        "type": "vscode-marketplace",
        "url": "https://marketplace.visualstudio.com",
        "poll_interval_seconds": 43200,  # twice daily
        "tier": "tier2",
        "config": {
            "queries": ["mcp", "claude", "copilot", "cline", "continue", "cursor"]
        },
    },
]

BLUESKY_SOURCES = [
    {
        "id": "bluesky:search-claude-code",
        "name": "Bluesky Claude Code Search",
        "type": "bluesky",
        "url": "https://bsky.app/search?q=claude+code",
        "poll_interval_seconds": 1800,  # every 30 min
        "tier": "tier2",
        "config": {},
    },
    {
        "id": "bluesky:search-mcp-protocol",
        "name": "Bluesky MCP Protocol Search",
        "type": "bluesky",
        "url": "https://bsky.app/search?q=model+context+protocol",
        "poll_interval_seconds": 1800,  # every 30 min
        "tier": "tier2",
        "config": {},
    },
]

SITEMAP_SOURCES = [
    {
        "id": "sitemap:anthropic-docs",
        "name": "Anthropic Documentation",
        "type": "sitemap",
        "url": "https://docs.anthropic.com/sitemap.xml",
        "poll_interval_seconds": 43200,  # twice daily
        "tier": "tier1",
        "config": {"url_filter": "/en/docs/"},
    },
    {
        "id": "sitemap:openai-docs",
        "name": "OpenAI Documentation",
        "type": "sitemap",
        "url": "https://platform.openai.com/sitemap.xml",
        "poll_interval_seconds": 43200,  # twice daily
        "tier": "tier2",
        "config": {"url_filter": "/docs/"},
    },
]

DISCUSSIONS_SOURCES = [
    {
        "id": "github-discussions:claude-code",
        "name": "Claude Code Discussions",
        "type": "github-discussions",
        "url": "https://github.com/anthropics/claude-code/discussions",
        "poll_interval_seconds": 1800,  # every 30 min
        "tier": "tier1",
        "config": {
            "repos": [
                {"owner": "anthropics", "name": "claude-code"},
                {"owner": "modelcontextprotocol", "name": "specification"},
            ]
        },
    },
]

NEWSLETTER_SOURCES = [
    {
        "id": "newsletter:inbound-email",
        "name": "Newsletter Email Ingest",
        "type": "newsletter-email",
        "url": "mailgun://inbound",
        "poll_interval_seconds": 0,  # event-driven via webhook, not polled
        "tier": "tier1",
        "config": {},
    },
]

ALL_SOURCES = (
    PYPI_SOURCES
    + VSCODE_SOURCES
    + BLUESKY_SOURCES
    + SITEMAP_SOURCES
    + DISCUSSIONS_SOURCES
    + NEWSLETTER_SOURCES
)

# ---------------------------------------------------------------------------
# github-deep sources that should have watched_files for CHANGELOG diffing
# ---------------------------------------------------------------------------

GITHUB_DEEP_WATCHED_FILES_UPDATES = {
    "github-deep:anthropics/anthropic-sdk-python": ["CHANGELOG.md"],
    "github-deep:openai/openai-python": ["CHANGELOG.md"],
    "github-deep:anthropics/claude-code": ["CHANGELOG.md", "RELEASE_NOTES.md"],
    "github-deep:modelcontextprotocol/servers": ["CHANGELOG.md"],
}


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------


async def seed() -> None:
    await init_db()

    async with _db.async_session_factory() as session:
        inserted = 0
        skipped = 0

        # --- Insert new Phase 12 sources ---
        for spec in ALL_SOURCES:
            result = await session.execute(
                select(Source).where(Source.id == spec["id"])
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
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
                config=spec["config"],
            )
            session.add(source)
            print(f"  ADD   {spec['id']}")
            inserted += 1

        await session.commit()
        print(
            f"\nNew sources: {inserted} inserted, {skipped} skipped (total {len(ALL_SOURCES)})"
        )

        # --- Update existing github-deep sources to add watched_files ---
        updated = 0
        no_update_needed = 0

        for source_id, watched_files in GITHUB_DEEP_WATCHED_FILES_UPDATES.items():
            result = await session.execute(
                select(Source)
                .where(Source.id == source_id)
                .execution_options(populate_existing=True)
            )
            source = result.scalar_one_or_none()

            if source is None:
                print(
                    f"  SKIP  {source_id} (not found, run seed_phase11_sources.py first)"
                )
                continue

            current_config = source.config or {}
            if "watched_files" in current_config:
                print(f"  SKIP  {source_id} (watched_files already set)")
                no_update_needed += 1
                continue

            # Dict reassignment triggers SQLAlchemy JSON mutation detection
            new_config = dict(current_config)
            new_config["watched_files"] = watched_files
            source.config = new_config
            session.add(source)
            print(f"  UPDATE {source_id} — watched_files: {watched_files}")
            updated += 1

        await session.commit()
        print(
            f"\ngithub-deep watched_files: {updated} updated, {no_update_needed} already set"
        )

    await close_db()


if __name__ == "__main__":
    asyncio.run(seed())
