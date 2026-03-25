"""Seed dedicated RAG/embedding sources for vector DB blogs, releases, and community feeds.

Usage: python scripts/seed_rag_sources.py

Adds 8 new sources covering:
- Vector DB provider blogs (Pinecone, Weaviate, Qdrant)
- GitHub Releases (LlamaIndex, Chroma, Qdrant)
- PyPI RAG framework packages
- dev.to RAG community tag feed

Idempotent: checks if source.id exists before inserting, skips if present.
"""

import asyncio
import sys

sys.path.insert(0, ".")  # Allow running from project root

import src.core.init_db as _db
from src.core.init_db import init_db, close_db
from src.models.models import Source
from sqlalchemy import select

RAG_SOURCES = [
    # ── RSS/Atom feeds: Vector DB provider blogs ──────────────────────
    {
        "id": "rss:pinecone-blog",
        "name": "Pinecone Blog",
        "type": "rss",
        "url": "https://www.pinecone.io/blog/feed/",
        "tier": "tier2",
        "poll_interval_seconds": 14400,
        "config": {},
    },
    {
        "id": "rss:weaviate-blog",
        "name": "Weaviate Blog",
        "type": "rss",
        "url": "https://weaviate.io/blog/rss.xml",
        "tier": "tier2",
        "poll_interval_seconds": 14400,
        "config": {},
    },
    {
        "id": "rss:qdrant-blog",
        "name": "Qdrant Blog",
        "type": "rss",
        "url": "https://qdrant.tech/blog/rss.xml",
        "tier": "tier2",
        "poll_interval_seconds": 14400,
        "config": {},
    },
    # ── GitHub Releases: RAG/vector DB frameworks ─────────────────────
    {
        "id": "rss:gh-llamaindex",
        "name": "LlamaIndex Releases",
        "type": "rss",
        "url": "https://github.com/run-llama/llama_index/releases.atom",
        "tier": "tier2",
        "poll_interval_seconds": 7200,
        "config": {},
    },
    {
        "id": "rss:gh-chroma",
        "name": "Chroma Releases",
        "type": "rss",
        "url": "https://github.com/chroma-core/chroma/releases.atom",
        "tier": "tier2",
        "poll_interval_seconds": 7200,
        "config": {},
    },
    {
        "id": "rss:gh-qdrant",
        "name": "Qdrant Releases",
        "type": "rss",
        "url": "https://github.com/qdrant/qdrant/releases.atom",
        "tier": "tier2",
        "poll_interval_seconds": 7200,
        "config": {},
    },
    # ── PyPI: RAG framework packages ──────────────────────────────────
    {
        "id": "pypi:rag-frameworks",
        "name": "RAG Framework Packages (PyPI)",
        "type": "pypi",
        "url": "https://pypi.org",
        "tier": "tier2",
        "poll_interval_seconds": 14400,
        "config": {
            "packages": [
                "llama-index",
                "llama-index-core",
                "chromadb",
                "qdrant-client",
                "pinecone-client",
                "weaviate-client",
                "langchain-community",
                "langchain-core",
            ]
        },
    },
    # ── Community: dev.to RAG tag feed ────────────────────────────────
    {
        "id": "rss:devto-rag",
        "name": "dev.to — RAG and Vector Database",
        "type": "rss",
        "url": "https://dev.to/feed/tag/rag",
        "tier": "tier3",
        "poll_interval_seconds": 14400,
        "config": {},
    },
]


async def main() -> None:
    await init_db()

    async with _db.async_session_factory() as session:
        added = 0
        skipped = 0

        for spec in RAG_SOURCES:
            result = await session.execute(
                select(Source).where(Source.id == spec["id"])
            )
            if result.scalar_one_or_none():
                print(f"  SKIP  {spec['id']}")
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
            added += 1

        await session.commit()
        print(f"\nRAG source seeding complete: {added} added, {skipped} skipped")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
