"""add search_vector generated column and GIN index

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-03-17
"""
from typing import Sequence, Union
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE intel_items
        ADD COLUMN IF NOT EXISTS search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(excerpt, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(content, '')), 'C')
        ) STORED
    """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_intel_items_search_vector
        ON intel_items USING GIN (search_vector)
    """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_intel_items_search_vector")
    op.execute("ALTER TABLE intel_items DROP COLUMN IF EXISTS search_vector")
