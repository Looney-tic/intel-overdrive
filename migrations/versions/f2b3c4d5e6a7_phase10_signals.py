"""phase10_signals: item_signals table + contrarian_signals column on intel_items

Revision ID: f2b3c4d5e6a7
Revises: d4e5f6a7b8c9
Create Date: 2026-03-17

Adds community signal tracking (INTEL-06) and contrarian signal detection (INTEL-12):
- item_signals table: one row per (item_id, api_key_id), action = upvote/bookmark/dismiss
- contrarian_signals column on intel_items: JSON array of category strings

Note: Both f1a2b3c4d5e6 (10-01) and this migration (10-02) are Wave 1 with the same
down_revision (d4e5f6a7b8c9). A merge migration is required before running both.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2b3c4d5e6a7"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "item_signals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("api_key_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["intel_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "api_key_id", name="uq_item_signal_per_key"),
    )
    op.create_index("ix_item_signals_item_id", "item_signals", ["item_id"])
    op.add_column(
        "intel_items",
        sa.Column("contrarian_signals", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("intel_items", "contrarian_signals")
    op.drop_index("ix_item_signals_item_id", table_name="item_signals")
    op.drop_table("item_signals")
