"""phase9_ux_columns: add published_at, source_name, cluster_id to intel_items

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-17

Adds three nullable columns to intel_items for Phase 9 UX improvements:
- published_at: original publication timestamp from source
- source_name: denormalized source name for API responses
- cluster_id: string grouping key for story clustering (Phase 9 plan 4)

Also adds:
- ix_intel_items_created_at: index on created_at for 'since' filter performance
- ix_intel_items_cluster_id: index on cluster_id for cluster lookup
- Backfills source_name from sources join
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "intel_items",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "intel_items",
        sa.Column("source_name", sa.String(), nullable=True),
    )
    op.add_column(
        "intel_items",
        sa.Column("cluster_id", sa.String(), nullable=True),
    )
    op.create_index("ix_intel_items_created_at", "intel_items", ["created_at"])
    op.create_index("ix_intel_items_cluster_id", "intel_items", ["cluster_id"])
    # Backfill source_name from sources join for existing rows
    op.execute(
        """
        UPDATE intel_items i
        SET source_name = s.name
        FROM sources s
        WHERE i.source_id = s.id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_intel_items_cluster_id", table_name="intel_items")
    op.drop_index("ix_intel_items_created_at", table_name="intel_items")
    op.drop_column("intel_items", "cluster_id")
    op.drop_column("intel_items", "source_name")
    op.drop_column("intel_items", "published_at")
