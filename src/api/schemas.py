import re
import uuid
from datetime import datetime
from typing import List, Optional, Literal, Dict
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IntelItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: str
    url: str
    title: str
    excerpt: Optional[str] = None
    summary: Optional[str] = None
    primary_type: str
    tags: List[str] = Field(default_factory=list)
    significance: Optional[str] = "informational"
    relevance_score: float
    quality_score: float
    quality_score_details: Optional[Dict] = None
    confidence_score: float
    status: str
    created_at: datetime
    published_at: Optional[datetime] = None
    source_name: Optional[str] = None
    cluster_id: Optional[str] = None
    contrarian_signals: Optional[List[str]] = None


class SignalRequest(BaseModel):
    action: Literal["upvote", "bookmark", "dismiss", "read", "acted_on"]


class ItemSignalsResponse(BaseModel):
    item_id: uuid.UUID
    upvotes: int
    bookmarks: int
    dismissals: int
    total: int
    contrarian_signals: Optional[List[str]] = None


class FeedResponse(BaseModel):
    items: List[IntelItemResponse]
    total: int
    offset: int
    limit: int
    cursor_updated: bool = False


class SLAResponse(BaseModel):
    newest_item_age_hours: Optional[
        float
    ]  # None if no processed items yet; age of newest processed item
    pipeline_lag_seconds: Optional[
        float
    ]  # None if pipeline queue is empty; P50 (median) not MAX
    items_last_24h: int
    items_last_7d: int
    failed_items_last_24h: int  # Items that failed classification in last 24h
    credits_exhausted: bool  # True when API credits are exhausted (set by pipeline worker)
    coverage_score: float  # 0.0-1.0, healthy sources / total
    source_health_summary: dict  # {healthy: N, degraded: N, dead: N, total: N}
    freshness_guarantee: str  # "24h" (static contract)
    checked_at: datetime


class DigestGroupResponse(BaseModel):
    primary_type: str
    count: int
    items: List["IntelItemResponse"]


class DigestResponse(BaseModel):
    days: int
    groups: List[DigestGroupResponse]
    total: int


class SimilarItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    url: str
    excerpt: Optional[str] = None
    summary: Optional[str] = None
    primary_type: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    relevance_score: Optional[float] = None
    significance: Optional[str] = None
    created_at: datetime
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    similarity: float


class SimilarResponse(BaseModel):
    items: List[SimilarItemResponse]
    total: int


class SearchResultResponse(BaseModel):
    id: uuid.UUID
    title: str
    excerpt: Optional[str] = None
    summary: Optional[str] = None
    primary_type: str
    tags: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    relevance_score: float
    quality_score: Optional[float] = None
    quality_score_details: Optional[dict] = None
    confidence_score: Optional[float] = None
    significance: Optional[str] = None
    rank: float
    created_at: datetime


class SearchResponse(BaseModel):
    items: List[SearchResultResponse]
    total: int
    offset: int = 0
    limit: int = 20
    warning: Optional[str] = None


class SourceStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: str
    is_active: bool
    last_successful_poll: Optional[datetime] = None
    consecutive_errors: int
    poll_interval_seconds: int


class StatusSummaryResponse(BaseModel):
    """Summary status response — counts instead of full source list (<5KB vs 114KB)."""

    total_sources: int
    active_sources: int
    erroring_sources: int
    source_type_counts: Dict[str, int]
    daily_spend_remaining: float
    pipeline_health: str


class HealthResponse(BaseModel):
    status: str
    last_ingestion: Optional[datetime] = None
    db_connected: bool = False
    redis_connected: bool = False


class ProfileRequest(BaseModel):
    tech_stack: List[str] = Field(..., max_length=100)
    skills: Optional[List[str]] = Field(default=None, max_length=100)
    tools: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="IDE/agents: claude-code, cursor, cline, aider, copilot, windsurf",
    )
    providers: List[str] = Field(
        default_factory=list,
        max_length=50,
        description="LLM providers: anthropic, openai, google, mistral, meta",
    )
    role: Optional[Literal["builder", "operator", "researcher"]] = Field(
        default=None,
        description="Role: builder (boost tools/practices), operator (boost updates/breaking), researcher (boost docs/benchmarks)",
    )


class ProfileResponse(BaseModel):
    message: str
    profile: Dict


class FeedbackRequest(BaseModel):
    report_type: Literal["miss", "noise", "bug", "incorrect", "auto_miss"]
    item_id: Optional[uuid.UUID] = None
    url: Optional[str] = Field(default=None, max_length=2048)
    notes: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: str | None) -> str | None:
        if v is not None and not re.match(r"^https?://", v):
            raise ValueError("url must start with http:// or https://")
        return v

    @model_validator(mode="after")
    def validate_id_or_url(self) -> "FeedbackRequest":
        if not self.item_id and not self.url:
            raise ValueError("At least one of item_id or url must be provided.")
        return self


class FeedbackResponse(BaseModel):
    message: str
    id: uuid.UUID


class AutoFeedbackRequest(BaseModel):
    """Auto-feedback from MCP fire-and-forget: auto_miss or query_refinement signals."""

    report_type: Literal["auto_miss", "query_refinement"]
    query: str = Field(..., max_length=500)
    original_query: Optional[str] = Field(
        default=None, max_length=500
    )  # for query_refinement
    result_count: int = 0


class AlertRuleCreate(BaseModel):
    name: str
    keywords: List[str] = Field(..., max_length=50)
    cooldown_minutes: int = 60
    significance_trigger: Optional[List[str]] = Field(
        default=None,
        description=(
            "Fire when items match any of these significance levels: "
            "breaking, major, minor, informational"
        ),
    )

    @field_validator("keywords", mode="before")
    @classmethod
    def validate_keyword_lengths(cls, v: list) -> list:
        for kw in v:
            if isinstance(kw, str) and len(kw) > 100:
                raise ValueError(
                    f"Each keyword must be at most 100 characters, got {len(kw)}"
                )
        return v

    @field_validator("significance_trigger", mode="before")
    @classmethod
    def validate_significance_trigger(cls, v: list | None) -> list | None:
        if v is None:
            return v
        valid = {"breaking", "major", "minor", "informational"}
        for sig in v:
            if sig not in valid:
                raise ValueError(
                    f"Invalid significance level '{sig}'. Must be one of: {', '.join(sorted(valid))}"
                )
        return v


class AlertRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    keywords: List[str]
    delivery_channels: dict
    is_active: bool
    cooldown_minutes: int
    created_at: datetime


class SlackWebhookRequest(BaseModel):
    webhook_url: str  # Slack incoming webhook URL

    @field_validator("webhook_url")
    @classmethod
    def validate_slack_webhook_url(cls, v: str) -> str:
        if not v.startswith("https://hooks.slack.com/"):
            raise ValueError(
                "webhook_url must be a valid Slack incoming webhook URL "
                "(must start with https://hooks.slack.com/)"
            )
        return v


class WebhookUrlRequest(BaseModel):
    """Request body for POST /alerts/webhook — generic webhook URL delivery channel."""

    webhook_url: str  # HTTPS URL for generic webhook delivery

    @field_validator("webhook_url")
    @classmethod
    def validate_https_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("webhook_url must be an HTTPS URL")
        return v


class AlertRuleStatusResponse(AlertRuleResponse):
    """Extended response with firing/cooldown state."""

    last_fired_at: Optional[datetime] = None
    is_on_cooldown: bool = False


class AlertStatusResponse(BaseModel):
    rules: List[AlertRuleStatusResponse]
    message: str


class TrendItem(BaseModel):
    tag: str
    window_1_count: int
    window_2_count: int
    velocity_ratio: Optional[float]
    velocity_label: str  # accelerating / plateauing / declining / emerging
    total_count: int
    source_count: int = 1  # number of distinct sources contributing to this tag


class TrendsResponse(BaseModel):
    window_days: int
    window_1_label: str  # e.g. "last 7 days"
    window_2_label: str  # e.g. "days 8-14"
    trends: List[TrendItem]
    total: int


class LandscapeItem(BaseModel):
    title: str
    url: str
    primary_type: str
    relevance_score: float
    significance: Optional[str]
    source_name: Optional[str]
    item_count: int


class LandscapeResponse(BaseModel):
    domain: str
    total_items: int
    momentum_leaders: List[LandscapeItem]
    positioning: Dict[str, int]  # primary_type -> count
    gaps: List[str]  # types absent from this domain
    window_days: int


class ContextPackMeta(BaseModel):
    topic: str
    budget_tokens: int
    items_included: int
    chars_used: int
    tokens_estimated: int
    days: int
    generated_at: datetime


class DiffItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    url: str
    excerpt: Optional[str] = None
    summary: Optional[str] = None
    primary_type: str
    tags: List[str] = Field(default_factory=list)
    relevance_score: float
    significance: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    impact_description: str


class DiffResponse(BaseModel):
    items: List[DiffItemResponse]
    total: int
    offset: int
    limit: int
    profile_stack_size: int  # how many tags were in the profile
    message: Optional[str] = None


class WatchlistCreate(BaseModel):
    name: str
    concept: str
    similarity_threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class WatchlistResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    concept: str
    similarity_threshold: float
    is_active: bool
    created_at: datetime
    has_embedding: bool  # True if concept_embedding is not NULL


class WatchlistMatchResponse(BaseModel):
    id: uuid.UUID
    title: str
    url: str
    summary: Optional[str] = None
    primary_type: str
    significance: Optional[str] = None
    match_score: float


class ThreadTopItem(BaseModel):
    id: uuid.UUID
    title: str
    url: str
    summary: Optional[str] = None
    primary_type: str
    significance: Optional[str] = None
    source_name: Optional[str] = None
    created_at: datetime


class ThreadResponse(BaseModel):
    thread_id: str  # cluster_id
    item_count: int
    first_seen: datetime
    last_seen: datetime
    momentum_score: float  # normalized 0-1
    total_upvotes: int
    dominant_significance: Optional[str] = None
    narrative_summary: str
    top_items: List[ThreadTopItem]


class ThreadsListResponse(BaseModel):
    threads: List[ThreadResponse]
    total: int
    offset: int
    limit: int


class ThreadDetailResponse(BaseModel):
    thread_id: str
    narrative_summary: str
    momentum_score: float
    total_upvotes: int
    items: List[IntelItemResponse]  # full items


class ErrorResponse(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Admin key management schemas
# ---------------------------------------------------------------------------


class AdminKeyCreate(BaseModel):
    name: Optional[str] = None


class AdminKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    key_prefix: str
    name: Optional[str] = None
    is_active: bool
    usage_count: int
    last_used_at: Optional[datetime] = None
    created_at: datetime


class AdminKeyCreatedResponse(BaseModel):
    """Response from POST /admin/keys — includes raw key (shown only once)."""

    key: str  # raw key — shown ONLY in this response
    key_prefix: str
    id: int
    name: Optional[str] = None
    message: str


# ---------------------------------------------------------------------------
# Library schemas (Phase 15 Knowledge Library)
# ---------------------------------------------------------------------------


class LibraryTopicSummary(BaseModel):
    """Topic entry in the library index — metadata only, no items."""

    topic: str
    label: str
    description: Optional[str] = None
    item_count: int
    avg_quality: float
    last_updated: Optional[datetime] = None
    subtopics: List[str] = Field(default_factory=list)


class LibraryItemSummary(BaseModel):
    """Single item in a library topic response — ranked by evergreen_score."""

    id: str
    title: str
    url: str
    summary: Optional[str] = None
    primary_type: str
    tags: List[str] = Field(default_factory=list)
    significance: Optional[str] = None
    relevance_score: Optional[float] = None
    quality_score: Optional[float] = None
    evergreen_score: float
    upvote_count: int
    bookmark_count: int
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class LibraryTopicResponse(BaseModel):
    """Response for GET /v1/library/topic/{topic} — ranked items with evergreen scores."""

    topic: str
    description: Optional[str] = None
    item_count: int
    items: List[LibraryItemSummary]
    generated_at: datetime


class LibraryIndexResponse(BaseModel):
    """Response for GET /v1/library — topic index ranked by avg composite quality."""

    topics: List[LibraryTopicSummary]
    total: int
    generated_at: datetime


# ---------------------------------------------------------------------------
# Library entry detail schemas (Phase 15-03)
# ---------------------------------------------------------------------------


class LibraryEntryResponse(BaseModel):
    """Full library entry response — returned by GET /v1/library/{slug}."""

    slug: str
    title: str
    tldr: Optional[str] = None
    body: str
    key_points: List[str] = Field(default_factory=list)
    gotchas: List[Dict] = Field(default_factory=list)  # [{title, detail}]
    topic_path: str
    entry_type: str
    assumed_context: str
    role_relevance: List[str] = Field(default_factory=list)
    related_entries: List[Dict] = Field(
        default_factory=list
    )  # [{slug, title, relevance}]
    source_items: List[Dict] = Field(
        default_factory=list
    )  # [{id, title, url, published_at}]
    meta: Dict = Field(default_factory=dict)  # quality/lifecycle signals
    agent_hint: Optional[str] = None


class LibrarySearchResult(BaseModel):
    """Single result in a library search or recommend response."""

    slug: str
    title: str
    tldr: Optional[str] = None
    entry_type: str
    confidence: str
    staleness_risk: str
    topic_path: str
    match_score: float


class LibrarySearchResponse(BaseModel):
    """Response for GET /v1/library/search."""

    items: List[LibrarySearchResult]
    total: int
    query_understood: bool


class LibraryRecommendResponse(BaseModel):
    """Response for GET /v1/library/recommend."""

    entries: List[LibrarySearchResult]
    profile_tags_matched: List[str]


class LibrarySignalRequest(BaseModel):
    """Request body for POST /v1/library/{slug}/signals."""

    action: Literal["helpful", "outdated"]
    note: Optional[str] = None


class LibrarySuggestRequest(BaseModel):
    """Request body for POST /v1/library/suggest."""

    topic: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=2000)


# ---------------------------------------------------------------------------
# Self-service registration schemas (Phase 17)
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    """Request body for POST /v1/auth/register.

    Email is optional — omit for anonymous registration (free-anon tier).
    """

    email: Optional[str] = None
    invite_code: Optional[str] = None  # kept for backward compat, ignored


class RegisterResponse(BaseModel):
    """Response from POST /v1/auth/register -- includes raw API key (shown only once)."""

    api_key: str
    user_id: str
    tier: str
    message: str
