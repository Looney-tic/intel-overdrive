"""phase10_watchlists: add watchlists table with concept_embedding vector column

Revision ID: f3c4d5e6a7b8
Revises: g3c4d5e6f7a8
Create Date: 2026-03-17

Wave 2 migration for Phase 10 Plan 05. Depends on the Wave 1 merge migration
(g3c4d5e6f7a8) which merged the api_key_cursor and signals migrations.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "f3c4d5e6a7b8"
down_revision: Union[str, Sequence[str], None] = "g3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("concept", sa.Text(), nullable=False),
        sa.Column("concept_embedding", Vector(1024), nullable=True),
        sa.Column(
            "similarity_threshold", sa.Float(), nullable=False, server_default="0.75"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_watchlists_user_id", "watchlists", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_watchlists_user_id", table_name="watchlists")
    op.drop_table("watchlists")
