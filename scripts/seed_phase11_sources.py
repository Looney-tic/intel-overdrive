"""Seed script for Phase 11 advanced source adapters.

Usage: python scripts/seed_phase11_sources.py

Creates sources for:
- arXiv paper feeds (AI/SE categories)
- Deep GitHub repository analysis (key ecosystem repos)
- Playwright web scraper targets (blogs without RSS)
- Additional RSS sources (dev.to, Hashnode — social media alternative)

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

ARXIV_SOURCES = [
    {
        "id": "arxiv:ai-agents",
        "name": "arXiv AI Agents",
        "type": "arxiv",
        "url": "http://export.arxiv.org/api/query",
        "poll_interval_seconds": 86400,  # daily
        "tier": "tier1",
        "config": {
            "queries": [
                "cat:cs.AI AND (ti:agent OR ti:tool OR ti:claude)",
                "cat:cs.SE AND (ti:LLM OR ti:copilot OR ti:code generation)",
            ]
        },
    },
    {
        "id": "arxiv:mcp-protocols",
        "name": "arXiv MCP/Protocol Research",
        "type": "arxiv",
        "url": "http://export.arxiv.org/api/query",
        "poll_interval_seconds": 86400,  # daily
        "tier": "tier1",
        "config": {
            "queries": [
                "all:model context protocol",
                "all:tool use AND all:language model",
            ]
        },
    },
]

GITHUB_DEEP_SOURCES = [
    {
        "id": "github-deep:anthropics/claude-code",
        "name": "anthropics/claude-code (deep)",
        "type": "github-deep",
        "url": "https://github.com/anthropics/claude-code",
        "poll_interval_seconds": 1800,
        "tier": "tier1",
        "config": {
            "star_milestones": [1000, 5000, 10000, 50000],
            "commit_burst_threshold": 20,
        },
    },
    {
        "id": "github-deep:modelcontextprotocol/servers",
        "name": "modelcontextprotocol/servers (deep)",
        "type": "github-deep",
        "url": "https://github.com/modelcontextprotocol/servers",
        "poll_interval_seconds": 1800,
        "tier": "tier1",
        "config": {
            "star_milestones": [1000, 5000, 10000],
            "commit_burst_threshold": 15,
        },
    },
    {
        "id": "github-deep:microsoft/playwright",
        "name": "microsoft/playwright (deep)",
        "type": "github-deep",
        "url": "https://github.com/microsoft/playwright",
        "poll_interval_seconds": 1800,
        "tier": "tier2",
        "config": {
            "star_milestones": [50000, 100000],
            "commit_burst_threshold": 30,
        },
    },
    {
        "id": "github-deep:anthropics/anthropic-sdk-python",
        "name": "anthropics/anthropic-sdk-python (deep)",
        "type": "github-deep",
        "url": "https://github.com/anthropics/anthropic-sdk-python",
        "poll_interval_seconds": 1800,
        "tier": "tier1",
        "config": {
            "star_milestones": [1000, 5000],
            "commit_burst_threshold": 15,
        },
    },
    {
        "id": "github-deep:openai/openai-python",
        "name": "openai/openai-python (deep)",
        "type": "github-deep",
        "url": "https://github.com/openai/openai-python",
        "poll_interval_seconds": 1800,
        "tier": "tier2",
        "config": {
            "star_milestones": [5000, 10000],
            "commit_burst_threshold": 20,
        },
    },
]

SCRAPER_SOURCES = [
    {
        "id": "scraper:openai-changelog",
        "name": "OpenAI Changelog",
        "type": "scraper",
        "url": "https://platform.openai.com/docs/changelog",
        "poll_interval_seconds": 21600,  # every 6 hours
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "div[data-testid='changelog-entry'], section.changelog-entry, article",
                "title": "h2, h3",
                "url": "h2 a, h3 a",
                "date": "time, span.date",
                "excerpt": "p",
            },
            "seen_urls": [],
            "wait_for_selector": "article, section, div[data-testid]",
        },
    },
    {
        "id": "scraper:cursor-blog",
        "name": "Cursor Blog",
        "type": "scraper",
        "url": "https://www.cursor.com/blog",
        "poll_interval_seconds": 21600,  # every 6 hours
        "tier": "tier1",
        "config": {
            "selectors": {
                "item": "article, a[href*='/blog/']",
                "title": "h2, h3",
                "url": "a",
                "excerpt": "p",
            },
            "seen_urls": [],
        },
    },
]

# Additional RSS sources — social media alternative per research recommendation
RSS_SOCIAL_SOURCES = [
    {
        "id": "rss:devto-claude",
        "name": "dev.to Claude Tag",
        "type": "rss",
        "url": "https://dev.to/feed/tag/claude",
        "poll_interval_seconds": 3600,  # hourly
        "tier": "tier2",
        "config": {},
    },
    {
        "id": "rss:devto-mcp",
        "name": "dev.to MCP Tag",
        "type": "rss",
        "url": "https://dev.to/feed/tag/mcp",
        "poll_interval_seconds": 3600,  # hourly
        "tier": "tier2",
        "config": {},
    },
]

ALL_SOURCES = ARXIV_SOURCES + GITHUB_DEEP_SOURCES + SCRAPER_SOURCES + RSS_SOCIAL_SOURCES


# ---------------------------------------------------------------------------
# Main seed function
# ---------------------------------------------------------------------------


async def seed() -> None:
    await init_db()

    async with _db.async_session_factory() as session:
        inserted = 0
        skipped = 0

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
            f"\nDone: {inserted} inserted, {skipped} skipped (total {len(ALL_SOURCES)})"
        )

    await close_db()


if __name__ == "__main__":
    asyncio.run(seed())
