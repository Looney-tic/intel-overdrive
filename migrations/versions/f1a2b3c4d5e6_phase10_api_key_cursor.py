"""phase10_api_key_cursor: add last_seen_at to api_keys for incremental cursor

Revision ID: f1a2b3c4d5e6
Revises: d4e5f6a7b8c9
Create Date: 2026-03-17

Adds last_seen_at column to api_keys for per-key cursor tracking.
Used by GET /v1/feed?new=true to return only items the API key has not seen before.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_last_seen_at", "api_keys", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_last_seen_at", table_name="api_keys")
    op.drop_column("api_keys", "last_seen_at")
