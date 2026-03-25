"""add query_logs table for lightweight query analytics

Revision ID: m9i0c1d2e3f4
Revises: l8h9b0c1d2e3
Create Date: 2026-03-21

Logs query metadata (type, text, result_count) per API call.
No response body stored — just enough for analytics.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "m9i0c1d2e3f4"
down_revision: Union[str, None] = "l8h9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_logs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "api_key_id", sa.Integer, sa.ForeignKey("api_keys.id"), nullable=False
        ),
        sa.Column("query_type", sa.String, nullable=False),
        sa.Column("query_text", sa.String, nullable=True),
        sa.Column("result_count", sa.Integer, server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
    )
    op.create_index("ix_query_logs_api_key_id", "query_logs", ["api_key_id"])
    op.create_index("ix_query_logs_created_at", "query_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_query_logs_created_at", table_name="query_logs")
    op.drop_index("ix_query_logs_api_key_id", table_name="query_logs")
    op.drop_table("query_logs")
