"""codify_manually_added_columns

Three columns that exist in production but were missing from Alembic migrations:
- intel_items.significance (VARCHAR, default 'informational')
- intel_items.quality_score_details (JSONB, nullable)
- sources.recovery_attempts (INTEGER, default 0)

Uses IF NOT EXISTS so it is idempotent on production (columns already exist)
and additive on fresh databases.

Revision ID: n0j1d2e3f4a5
Revises: m9i0c1d2e3f4
Create Date: 2026-03-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n0j1d2e3f4a5'
down_revision: Union[str, None] = 'm9i0c1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add significance to intel_items (idempotent via IF NOT EXISTS)
    op.execute(
        """
        ALTER TABLE intel_items
            ADD COLUMN IF NOT EXISTS significance VARCHAR
            DEFAULT 'informational'
        """
    )

    # Add quality_score_details to intel_items (idempotent via IF NOT EXISTS)
    op.execute(
        """
        ALTER TABLE intel_items
            ADD COLUMN IF NOT EXISTS quality_score_details JSONB
        """
    )

    # Add recovery_attempts to sources (idempotent via IF NOT EXISTS)
    op.execute(
        """
        ALTER TABLE sources
            ADD COLUMN IF NOT EXISTS recovery_attempts INTEGER
            NOT NULL DEFAULT 0
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS recovery_attempts")
    op.execute("ALTER TABLE intel_items DROP COLUMN IF EXISTS quality_score_details")
    op.execute("ALTER TABLE intel_items DROP COLUMN IF EXISTS significance")
