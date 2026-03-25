"""add source conditional get columns

Revision ID: e57b346b345d
Revises: 7393e1b973ce
Create Date: 2026-03-14 19:30:20.747427

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e57b346b345d"
down_revision: Union[str, None] = "7393e1b973ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("last_etag", sa.String(), nullable=True))
    op.add_column(
        "sources", sa.Column("last_modified_header", sa.String(), nullable=True)
    )
    op.add_column(
        "sources",
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "last_fetched_at")
    op.drop_column("sources", "last_modified_header")
    op.drop_column("sources", "last_etag")
