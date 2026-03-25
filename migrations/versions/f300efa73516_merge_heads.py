"""merge heads

Revision ID: f300efa73516
Revises: f3c4d5e6a7b8, i5e6f7a8b9c0
Create Date: 2026-03-19 09:59:48.981209

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = 'f300efa73516'
down_revision: Union[str, None] = ('f3c4d5e6a7b8', 'i5e6f7a8b9c0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
