"""
Pipeline helpers for the Intel pipeline worker.

Provides:
- build_embed_input: canonical text format for embedding (used by pipeline + seed script)
- safe_transition: atomic status transition with optimistic locking
- VALID_TRANSITIONS: state machine definition
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ..core.logger import get_logger

logger = get_logger(__name__)

# State machine: maps each status to its valid next states
VALID_TRANSITIONS: dict[str, list[str]] = {
    "raw": ["embedded", "failed"],
    "embedded": ["queued", "filtered"],
    "queued": ["processing"],
    "processing": ["processed", "failed"],
    "filtered": [],  # terminal
    "processed": [],  # terminal
    "failed": ["raw"],  # retry path
}


def build_embed_input(title: str, content: str) -> str:
    """
    Build the canonical embedding input text from title + content.

    This is the single source of truth for embed text format — both the
    pipeline embedding worker and the seed script MUST use this function
    to ensure cosine similarity comparisons are meaningful.

    Content is capped at 4000 chars (Intel items are shorter than long-form
    articles; cap prevents over-spending on embedding tokens).
    """
    return f"{title}\n\n{content[:4000]}"


async def safe_transition(
    session: AsyncSession,
    item_id: str,
    expected_from: str,
    new_status: str,
) -> bool:
    """
    Atomically transition an IntelItem's status, only if it is currently
    in the expected_from state (optimistic locking via RETURNING).

    Returns True if the transition succeeded (row was in expected state).
    Returns False if the item was not in the expected state (concurrent worker
    already advanced it, or item never existed).

    This is the PIPE-05 state machine enforcement helper — prevents double-
    processing by making transitions conditional and atomic at DB level.
    """
    stmt = text(
        """
        UPDATE intel_items
           SET status = :new_status,
               updated_at = NOW()
         WHERE id = CAST(:item_id AS uuid)
           AND status = :expected
        RETURNING id
        """
    )
    result = await session.execute(
        stmt,
        {
            "new_status": new_status,
            "item_id": item_id,
            "expected": expected_from,
        },
    )
    row = result.fetchone()
    if row is None:
        logger.warning(
            "SAFE_TRANSITION_SKIPPED",
            item_id=item_id,
            expected_from=expected_from,
            new_status=new_status,
        )
        return False
    return True
