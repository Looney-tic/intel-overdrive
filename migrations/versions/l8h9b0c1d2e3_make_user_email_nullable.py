"""make user email nullable for anonymous registration

Revision ID: l8h9b0c1d2e3
Revises: k7g8a9b0c1d2
Create Date: 2026-03-21

Anonymous users register without email. NULL is non-duplicate in Postgres UNIQUE
constraints, so multiple anon users work without conflict.
"""
from typing import Sequence, Union
from alembic import op

revision: str = "l8h9b0c1d2e3"
down_revision: Union[str, None] = "k7g8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "email", nullable=True)


def downgrade() -> None:
    op.alter_column("users", "email", nullable=False)
