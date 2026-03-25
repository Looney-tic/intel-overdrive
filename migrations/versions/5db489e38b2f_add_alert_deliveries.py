"""add_alert_deliveries

Revision ID: 5db489e38b2f
Revises: 6df0bd4d1be7
Create Date: 2026-03-15 00:01:51.085957

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "5db489e38b2f"
down_revision: Union[str, None] = "6df0bd4d1be7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create alert_deliveries outbox table (idempotent — skip if already exists)
    op.create_table(
        "alert_deliveries",
        sa.Column(
            "id", sa.Uuid(), nullable=False, default=sa.text("gen_random_uuid()")
        ),
        sa.Column("alert_rule_id", sa.Uuid(), nullable=False),
        sa.Column("intel_item_id", sa.Uuid(), nullable=False),
        sa.Column("urgency", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["alert_rule_id"], ["alert_rules.id"]),
        sa.ForeignKeyConstraint(["intel_item_id"], ["intel_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("alert_deliveries")
