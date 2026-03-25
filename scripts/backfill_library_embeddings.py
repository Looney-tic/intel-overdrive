"""Backfill embeddings for existing library items with NULL embeddings.

One-time script to populate the embedding column on library_items that were
created before embedding generation was added to synthesize_library_topics.

Usage:
    python scripts/backfill_library_embeddings.py

Idempotent: only processes items where embedding IS NULL AND is_current = True.
Batches Voyage AI calls in groups of 10 for efficiency.
"""

import asyncio
import sys
import os

# Ensure src/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import voyageai
from sqlalchemy import text

from src.core.config import get_settings
from src.core.init_db import init_db, async_session_factory


BATCH_SIZE = 10


async def main() -> None:
    settings = get_settings()
    await init_db()

    if async_session_factory is None:
        print("ERROR: Database not initialized")
        return

    voyage_client = voyageai.AsyncClient()

    async with async_session_factory() as session:
        # Fetch all current library items with NULL embeddings
        result = await session.execute(
            text(
                """
                SELECT id, tldr, body
                FROM library_items
                WHERE embedding IS NULL
                  AND is_current = TRUE
                ORDER BY created_at ASC
                """
            )
        )
        rows = result.fetchall()

        if not rows:
            print("No library items with NULL embeddings found. Nothing to do.")
            return

        total = len(rows)
        embedded = 0
        print(f"Found {total} library items with NULL embeddings.")

        # Process in batches of BATCH_SIZE
        for i in range(0, total, BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            texts = [f"{row[1] or ''}\n\n{row[2] or ''}" for row in batch]
            item_ids = [row[0] for row in batch]

            try:
                embed_result = await voyage_client.embed(
                    texts, model=settings.EMBEDDING_MODEL
                )
            except Exception as exc:
                print(f"ERROR: Embedding batch {i // BATCH_SIZE + 1} failed: {exc}")
                continue

            for item_id, embedding in zip(item_ids, embed_result.embeddings):
                await session.execute(
                    text(
                        """
                        UPDATE library_items
                        SET embedding = CAST(:emb AS vector),
                            updated_at = NOW()
                        WHERE id = :id
                        """
                    ),
                    {"emb": str(embedding), "id": str(item_id)},
                )

            await session.commit()
            embedded += len(batch)
            print(f"Embedded {embedded}/{total} library items")

    print(f"Done. Embedded {embedded}/{total} library items.")


if __name__ == "__main__":
    asyncio.run(main())
