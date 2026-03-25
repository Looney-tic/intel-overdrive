"""add status check constraint

Revision ID: a1b2c3d4e5f6
Revises: e60caf7a666e
Create Date: 2026-03-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e60caf7a666e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_intel_items_status",
        "intel_items",
        "status IN ('raw','embedded','queued','filtered','processing','processed','failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_intel_items_status", "intel_items", type_="check")
