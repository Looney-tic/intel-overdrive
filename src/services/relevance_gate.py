"""
Relevance gate: determines whether an embedded IntelItem is relevant based
on cosine similarity to the curated reference set (positive + negative items).

Gate score formula:
  score = max(0.0, min(1.0, max_positive_sim - max_negative_sim * 0.5))

Positive references are "gold examples" of relevant content.
Negative references are "noise examples" — high similarity to noise penalises
the score at 0.5 weight.

If the reference set is empty (not yet seeded), items pass by default to avoid
blocking the pipeline.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ..core.config import get_settings
from ..core.logger import get_logger

logger = get_logger(__name__)


async def query_reference_proximity(
    session: AsyncSession,
    item_embedding: list[float],
    limit: int = 20,
) -> list[tuple[bool, float]]:
    """
    Query reference_items for the closest neighbours to item_embedding.

    Uses pgvector <=> (cosine distance) identical to DedupService pattern.
    Cosine similarity = 1 - cosine_distance, so similarity 1.0 = identical.

    Returns a list of (is_positive, cosine_similarity) tuples ordered by
    ascending distance (most similar first).
    """
    stmt = text(
        """
        SELECT is_positive,
               1.0 - (embedding <=> CAST(:vec AS vector)) AS cosine_sim
          FROM reference_items
         WHERE embedding IS NOT NULL
         ORDER BY embedding <=> CAST(:vec AS vector)
         LIMIT :limit
        """
    )
    result = await session.execute(
        stmt,
        {"vec": str(item_embedding), "limit": limit},
    )
    return [(bool(row.is_positive), float(row.cosine_sim)) for row in result.fetchall()]


async def compute_gate_score(
    session: AsyncSession,
    item_embedding: list[float],
    threshold: float | None = None,
) -> tuple[float, bool]:
    """
    Compute the relevance gate score for an embedded IntelItem.

    Returns (score, is_relevant):
      - score: float in [0, 1] — higher = more similar to positive references
      - is_relevant: True if score >= threshold (item should proceed to queued)

    If no reference items exist, returns (0.0, True) to avoid blocking the
    pipeline before the reference set is seeded.

    The optional threshold parameter allows test overrides; when None, reads
    RELEVANCE_THRESHOLD from Settings at call time.
    """
    if threshold is None:
        threshold = get_settings().RELEVANCE_THRESHOLD

    rows = await query_reference_proximity(session, item_embedding)

    if not rows:
        logger.warning(
            "GATE_NO_REFERENCES_FOUND",
            message="No embedded reference items found; passing item by default",
        )
        return (0.0, True)

    # Weighted gate: positive similarity lifts score, negative penalises at 0.5
    positives = [sim for is_pos, sim in rows if is_pos]
    negatives = [sim for is_pos, sim in rows if not is_pos]

    if not positives and negatives:
        logger.warning(
            "GATE_NO_POSITIVE_REFERENCES",
            message="Reference set has only negative examples; score will be 0 for all items",
            negative_count=len(negatives),
        )

    max_positive = max(positives, default=0.0)
    max_negative = max(negatives, default=0.0)

    score = max(0.0, min(1.0, max_positive - max_negative * 0.5))
    is_relevant = score >= threshold

    return (score, is_relevant)
