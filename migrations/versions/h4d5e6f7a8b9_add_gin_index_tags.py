"""Add GIN index on intel_items.tags and composite partial index for library queries.

Revision ID: h4d5e6f7a8b9
Revises: g3c4d5e6f7a8
Create Date: 2026-03-18 21:21:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "h4d5e6f7a8b9"
down_revision: Union[str, None] = "g3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # GIN index on tags JSONB column — accelerates @> containment queries used
    # by library, landscape, context_pack, and threads endpoints.
    # Note: In production, run CREATE INDEX CONCURRENTLY manually for zero downtime.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intel_items_tags_gin "
        "ON intel_items USING gin(CAST(tags AS jsonb))"
    )

    # Composite partial index for library evergreen score query —
    # covers status=processed filter and quality/relevance ORDER BY.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_intel_items_library_composite "
        "ON intel_items (status, quality_score DESC, relevance_score DESC) "
        "WHERE status = 'processed'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_intel_items_library_composite")
    op.execute("DROP INDEX IF EXISTS ix_intel_items_tags_gin")
