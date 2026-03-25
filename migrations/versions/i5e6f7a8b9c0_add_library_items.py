"""add_library_items: library_items table with synthesis, lifecycle, and versioning fields

Revision ID: i5e6f7a8b9c0
Revises: h4d5e6f7a8b9
Create Date: 2026-03-18

Creates the library_items table for the V2 knowledge library — synthesized topic guides
with lifecycle management, versioning, and multi-representation content (tldr/body/key_points/gotchas).

Indexes:
- idx_library_topic_path: B-tree on topic_path for prefix queries (WHERE topic_path LIKE 'mcp/%')
- idx_library_status: partial index on status WHERE is_current = TRUE
- idx_library_tags: GIN on tags for containment queries (@>)
- idx_library_embedding: HNSW on embedding for cosine similarity search
- slug UNIQUE constraint acts as idx_library_slug
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "i5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "h4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "library_items",
        sa.Column(
            "id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        # Synthesized content — three-tier token representation
        sa.Column("tldr", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("key_points", sa.JSON(), nullable=True, server_default="[]"),
        sa.Column("gotchas", sa.JSON(), nullable=True, server_default="[]"),
        # Classification
        sa.Column("topic_path", sa.String(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=True, server_default="[]"),
        sa.Column("entry_type", sa.String(), nullable=True, server_default="reference"),
        sa.Column(
            "assumed_context", sa.String(), nullable=True, server_default="no-context"
        ),
        sa.Column("role_relevance", sa.JSON(), nullable=True, server_default="[]"),
        sa.Column("embedding", Vector(1024), nullable=True),
        # Lifecycle
        sa.Column("status", sa.String(), nullable=True, server_default="candidate"),
        sa.Column("graduation_score", sa.Float(), nullable=True, server_default="0.0"),
        sa.Column(
            "graduation_method", sa.String(), nullable=True, server_default="signal"
        ),
        sa.Column("graduated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.UUID(), nullable=True),
        # Source linkage
        sa.Column("source_item_ids", sa.JSON(), nullable=True, server_default="[]"),
        sa.Column("source_item_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("source_count", sa.Integer(), nullable=True, server_default="0"),
        # Staleness
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("url_last_checked", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_url_alive", sa.Boolean(), nullable=True),
        # Quality signals
        sa.Column("confidence", sa.String(), nullable=True, server_default="low"),
        sa.Column(
            "staleness_risk", sa.String(), nullable=True, server_default="medium"
        ),
        sa.Column("helpful_count", sa.Integer(), nullable=True, server_default="0"),
        sa.Column(
            "flagged_outdated", sa.Boolean(), nullable=True, server_default="false"
        ),
        sa.Column(
            "human_reviewed", sa.Boolean(), nullable=True, server_default="false"
        ),
        # Versioning
        sa.Column("version", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column("content_hash", sa.String(), nullable=True),
        # Curation
        sa.Column("curated_by", sa.String(), nullable=True),
        sa.Column("agent_hint", sa.Text(), nullable=True),
        # Timestamps (TimestampMixin)
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
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_library_items_slug"),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["library_items.id"],
            ondelete="SET NULL",
        ),
    )

    # B-tree index on topic_path for prefix queries
    op.create_index("idx_library_topic_path", "library_items", ["topic_path"])

    # Partial index on status for current items only
    op.create_index(
        "idx_library_status",
        "library_items",
        ["status"],
        postgresql_where=sa.text("is_current = TRUE"),
    )

    # GIN index on tags for @> containment queries (CAST to jsonb for GIN support)
    op.execute(
        "CREATE INDEX idx_library_tags ON library_items USING gin (CAST(tags AS jsonb))"
    )

    # HNSW index on embedding for cosine similarity search
    op.create_index(
        "idx_library_embedding",
        "library_items",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_table("library_items")
