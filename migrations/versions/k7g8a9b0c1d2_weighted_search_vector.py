"""weighted search_vector with setweight(A/B/C)

Revision ID: k7g8a9b0c1d2
Revises: j6f7a8b9c0d1
Create Date: 2026-03-21

Production has the unweighted search_vector from 6df0bd4d1be7 (title || excerpt || summary).
Migration c3d4e5f6a7b8 tried to ADD IF NOT EXISTS with weights but was a no-op because
the column already existed. This migration explicitly drops and recreates with weighted
setweight(A/B/C) for title, excerpt, and content.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "k7g8a9b0c1d2"
down_revision: Union[str, None] = "j6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop existing unweighted search_vector and its indexes
    op.execute("DROP INDEX IF EXISTS intel_items_search_idx")
    op.execute("DROP INDEX IF EXISTS idx_intel_items_search_vector")
    op.execute("ALTER TABLE intel_items DROP COLUMN IF EXISTS search_vector")

    # Recreate with weighted setweight(A/B/C)
    op.execute(
        """
        ALTER TABLE intel_items
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(excerpt, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(content, '')), 'C')
        ) STORED
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS intel_items_search_idx
        ON intel_items USING gin(search_vector)
    """
    )


def downgrade() -> None:
    # Reverse back to unweighted version
    op.execute("DROP INDEX IF EXISTS intel_items_search_idx")
    op.execute("ALTER TABLE intel_items DROP COLUMN IF EXISTS search_vector")
    op.execute(
        """
        ALTER TABLE intel_items
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english', coalesce(title, '') || ' ' || coalesce(excerpt, '') || ' ' || coalesce(summary, ''))
        ) STORED
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS intel_items_search_idx
        ON intel_items USING gin(search_vector)
    """
    )
