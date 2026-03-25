"""Story clustering worker: assigns cluster_id to items with similar embeddings."""

import uuid

from sqlalchemy import text

import src.core.init_db as _db
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger(__name__)

# Redis distributed lock key and TTL for cluster_items exclusion
CLUSTER_LOCK_KEY = "cluster:lock"
CLUSTER_LOCK_TTL = 120  # 2-minute TTL — cluster run should complete well within this


async def cluster_items(ctx: dict) -> None:
    """Assign cluster_id to processed items with highly similar embeddings.

    Algorithm: Uses a batch SQL CTE with CROSS JOIN LATERAL to find nearest
    neighbors for all unassigned items in a single query, replacing the O(N^2)
    per-item HNSW query loop. A Redis distributed lock prevents concurrent
    invocations from processing the same items simultaneously.

    Singletons (no neighbor within threshold) remain cluster_id=NULL.
    """
    if _db.async_session_factory is None:
        logger.error("cluster_items_called_before_db_init")
        return

    settings = get_settings()
    threshold = float(getattr(settings, "CLUSTER_DISTANCE_THRESHOLD", 0.15))
    batch_size = int(getattr(settings, "CLUSTER_BATCH_SIZE", 2000))

    # Acquire Redis distributed lock to prevent concurrent overlapping runs.
    # Skip locking if Redis is not available in ctx (e.g. unit tests without ARQ).
    redis_client = ctx.get("redis")
    if redis_client is not None:
        acquired = await redis_client.set(
            CLUSTER_LOCK_KEY, "1", nx=True, ex=CLUSTER_LOCK_TTL
        )
        if not acquired:
            logger.info("cluster_items_skipped_lock_held")
            return
    else:
        acquired = None  # no lock acquired, no cleanup needed

    try:
        await _run_cluster(threshold, batch_size)
    finally:
        if redis_client is not None and acquired:
            await redis_client.delete(CLUSTER_LOCK_KEY)


async def _run_cluster(threshold: float, batch_size: int) -> None:
    """Core clustering logic: batch SQL CTE + single-commit assignment."""
    async with _db.async_session_factory() as session:
        # Batch SQL approach: one CTE query finds nearest neighbor for all
        # unassigned items simultaneously, replacing N individual HNSW queries.
        # FOR UPDATE SKIP LOCKED on the unassigned CTE prevents double-processing
        # by concurrent invocations even when the Redis lock is absent.
        batch_sql = text(
            """
            WITH unassigned AS (
                SELECT id, embedding
                FROM intel_items
                WHERE status = 'processed'
                  AND embedding IS NOT NULL
                  AND cluster_id IS NULL
                  AND created_at >= NOW() - INTERVAL '7 days'
                ORDER BY created_at DESC
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
            ),
            nearest AS (
                SELECT
                    u.id AS item_id,
                    n.id AS neighbor_id,
                    n.cluster_id AS neighbor_cluster_id,
                    n.dist AS distance
                FROM unassigned u
                CROSS JOIN LATERAL (
                    SELECT id, cluster_id, (u.embedding <=> embedding) AS dist
                    FROM intel_items
                    WHERE status = 'processed'
                      AND embedding IS NOT NULL
                      AND id != u.id
                      AND (u.embedding <=> embedding) < :threshold
                    ORDER BY u.embedding <=> embedding
                    LIMIT 1
                ) n
            )
            SELECT item_id, neighbor_id, neighbor_cluster_id, distance
            FROM nearest
        """
        )

        rows = (
            (
                await session.execute(
                    batch_sql,
                    {"batch_size": batch_size, "threshold": threshold},
                )
            )
            .mappings()
            .all()
        )

        if not rows:
            logger.info("cluster_items_complete", assigned=0, scanned=0)
            return

        # Process results in Python: build cluster assignments from batch results.
        # Maps item_id -> cluster_id for all assignments (both new and inherited).
        assignments: dict[str, str] = {}

        for row in rows:
            item_id = str(row["item_id"])
            neighbor_id = str(row["neighbor_id"])
            neighbor_cluster_id = row["neighbor_cluster_id"]

            if neighbor_cluster_id:
                # Inherit existing cluster_id from the neighbor
                cluster_id = str(neighbor_cluster_id)
            else:
                # Neither item has a cluster_id yet — check if we already
                # assigned one to the neighbor in this batch pass
                if neighbor_id in assignments:
                    cluster_id = assignments[neighbor_id]
                else:
                    cluster_id = str(uuid.uuid4())
                    assignments[neighbor_id] = cluster_id

            assignments[item_id] = cluster_id

        # Commit all assignments in a single transaction
        assigned_count = 0
        for item_id, cluster_id in assignments.items():
            await session.execute(
                text(
                    "UPDATE intel_items SET cluster_id = :cid"
                    " WHERE id = CAST(:iid AS uuid) AND cluster_id IS NULL"
                ),
                {"cid": cluster_id, "iid": item_id},
            )
            assigned_count += 1

        await session.commit()
        logger.info(
            "cluster_items_complete",
            assigned=assigned_count,
            scanned=len(rows),
        )
