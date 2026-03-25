import uuid
import sqlalchemy as sa
from sqlalchemy import (
    String,
    Float,
    JSON,
    Boolean,
    ForeignKey,
    Text,
    Index,
    DateTime,
    Integer,
)
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from typing import Optional, List
from .base import Base, TimestampMixin
from datetime import datetime

# Valid status values for IntelItem pipeline state machine
VALID_STATUSES = [
    "raw",
    "embedded",
    "queued",
    "filtered",
    "processing",
    "processed",
    "failed",
]


class Source(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(
        String, primary_key=True
    )  # e.g. "github:anthropic/claude-code"
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # rss, github, hn, reddit, etc.
    url: Mapped[str] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_poll: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    # Polling health tracking
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_successful_poll: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    tier: Mapped[str] = mapped_column(String, default="tier1")

    # Conditional GET support
    last_etag: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_modified_header: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class IntelItem(Base, TimestampMixin):
    __tablename__ = "intel_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(
        String, index=True
    )  # e.g. GitHub issue ID, RSS guid
    url: Mapped[str] = mapped_column(String, unique=True)
    url_hash: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )  # Layer 1 dedup
    title: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Classification
    primary_type: Mapped[str] = mapped_column(
        String, index=True
    )  # skill, tool, update, practice, docs
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Scoring
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_score_details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Vector — nullable: items start as raw without embeddings
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1024), nullable=True
    )
    embedding_model_version: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )

    status: Mapped[str] = mapped_column(String, default="raw")
    significance: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, default="informational"
    )
    content_hash: Mapped[Optional[str]] = mapped_column(
        String, index=True, nullable=True
    )

    # Phase 9 UX columns
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    cluster_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Phase 10 intelligence layer
    contrarian_signals: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index(
            "intel_items_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[Optional[str]] = mapped_column(
        String, unique=True, nullable=True
    )  # NULL for anonymous users; Postgres treats NULL as non-duplicate in UNIQUE
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    profile: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # tech stack, skill inventory

    # Billing-ready columns (M-10)
    tier: Mapped[str] = mapped_column(String, default="free")
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class APIKey(Base, TimestampMixin):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_hash: Mapped[str] = mapped_column(
        String, unique=True, index=True
    )  # SHA-256 of raw key
    key_prefix: Mapped[str] = mapped_column(
        String
    )  # "dti_v1_" prefix for identification
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    report_type: Mapped[str] = mapped_column(String)  # "miss" or "noise"
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("intel_items.id"), nullable=True
    )
    url: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # for miss reports without existing item
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"))
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AlertRule(Base, TimestampMixin):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String)
    keywords: Mapped[List[str]] = mapped_column(JSON, default=list)
    delivery_channels: Mapped[dict] = mapped_column(
        JSON, default=dict
    )  # slack_webhook, etc.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)


class AlertDelivery(Base, TimestampMixin):
    __tablename__ = "alert_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    alert_rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alert_rules.id"))
    intel_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intel_items.id"))
    urgency: Mapped[str] = mapped_column(String)  # critical, important, interesting
    status: Mapped[str] = mapped_column(
        String, default="pending"
    )  # pending, sent, failed
    channel: Mapped[str] = mapped_column(String)  # "slack"
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ItemSignal(Base, TimestampMixin):
    __tablename__ = "item_signals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("intel_items.id"), index=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"))
    action: Mapped[str] = mapped_column(String)  # "upvote", "bookmark", "dismiss"

    __table_args__ = (
        sa.UniqueConstraint("item_id", "api_key_id", name="uq_item_signal_per_key"),
    )


class QueryLog(Base, TimestampMixin):
    __tablename__ = "query_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), index=True)
    query_type: Mapped[str] = mapped_column(
        String
    )  # search, feed, similar, library, context-pack, breaking, briefing, status, action-items, signal
    query_text: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    concept: Mapped[str] = mapped_column(Text)  # natural language description
    concept_embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1024), nullable=True
    )
    similarity_threshold: Mapped[float] = mapped_column(Float, default=0.75)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ReferenceItem(Base, TimestampMixin):
    __tablename__ = "reference_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # nullable: items may be created before embedding is generated
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1024), nullable=True
    )
    embedding_model_version: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    label: Mapped[str] = mapped_column(String)  # positive, negative (for calibration)
    is_positive: Mapped[bool] = mapped_column(
        Boolean, default=True
    )  # True=relevant, False=noise


class LibraryItem(Base, TimestampMixin):
    """Synthesized knowledge library entry — durable, versioned, LLM-generated topic guide."""

    __tablename__ = "library_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)

    # Synthesized content — three-tier token representation for agent context budgets
    tldr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # ~20 tokens
    body: Mapped[str] = mapped_column(Text, nullable=False)  # ~500 tokens
    key_points: Mapped[list] = mapped_column(JSON, default=list)  # ~100 tokens
    gotchas: Mapped[list] = mapped_column(JSON, default=list)  # [{title, detail}]

    # Classification
    topic_path: Mapped[str] = mapped_column(
        String, nullable=False
    )  # e.g. "mcp/server-authoring"
    tags: Mapped[list] = mapped_column(JSON, default=list)
    entry_type: Mapped[str] = mapped_column(
        String, default="reference"
    )  # reference|conceptual|gotcha|catalog
    assumed_context: Mapped[str] = mapped_column(
        String, default="no-context"
    )  # no-context|domain-context|project-context
    role_relevance: Mapped[list] = mapped_column(
        JSON, default=list
    )  # ["agent-builder", "operator", "learner"]
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1024), nullable=True
    )

    # Lifecycle — candidate | active | review_needed | superseded | archived
    status: Mapped[str] = mapped_column(String, default="candidate")
    graduation_score: Mapped[float] = mapped_column(Float, default=0.0)
    graduation_method: Mapped[str] = mapped_column(
        String, default="signal"
    )  # signal|source_type|synthesis|admin
    graduated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("library_items.id"), nullable=True
    )

    # Source linkage — audit trail from synthesized knowledge back to intel_items
    source_item_ids: Mapped[list] = mapped_column(
        JSON, default=list
    )  # UUIDs of source intel_items
    source_item_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)  # distinct sources

    # Staleness
    valid_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    url_last_checked: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_url_alive: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Quality signals
    confidence: Mapped[str] = mapped_column(String, default="low")  # low|medium|high
    staleness_risk: Mapped[str] = mapped_column(
        String, default="medium"
    )  # low|medium|high
    helpful_count: Mapped[int] = mapped_column(Integer, default=0)
    flagged_outdated: Mapped[bool] = mapped_column(Boolean, default=False)
    human_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    # Versioning — insert new row with version+1, is_current=True; set old is_current=False
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    content_hash: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )  # SHA-256 for ETag/304

    # Curation
    curated_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_hint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "idx_library_topic_path",
            "topic_path",
        ),
        Index(
            "idx_library_status",
            "status",
            postgresql_where=sa.text("is_current = TRUE"),
        ),
        Index(
            "idx_library_tags",
            "tags",
            postgresql_using="gin",
        ),
        Index(
            "idx_library_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
