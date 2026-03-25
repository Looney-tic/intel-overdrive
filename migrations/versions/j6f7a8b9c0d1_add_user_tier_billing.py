"""add user tier and billing columns

Revision ID: j6f7a8b9c0d1
Revises: f300efa73516
Create Date: 2026-03-19

M-10: Add tier (default 'free') and stripe_customer_id columns to users table
for billing-ready user segmentation.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "j6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "f300efa73516"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tier", sa.String(), nullable=False, server_default="free"),
    )
    op.add_column(
        "users",
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "stripe_customer_id")
    op.drop_column("users", "tier")
