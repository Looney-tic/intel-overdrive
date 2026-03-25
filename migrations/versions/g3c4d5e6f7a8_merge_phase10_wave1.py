"""merge_phase10_wave1: merge f1a2b3c4d5e6 and f2b3c4d5e6a7

Revision ID: g3c4d5e6f7a8
Revises: f1a2b3c4d5e6, f2b3c4d5e6a7
Create Date: 2026-03-17

Merge migration for Phase 10 Wave 1 plans (10-01 and 10-02), both of which use
d4e5f6a7b8c9 as down_revision since they are independent parallel plans.
"""
from typing import Sequence, Union

revision: str = "g3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = ("f1a2b3c4d5e6", "f2b3c4d5e6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
