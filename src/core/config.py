from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import Optional
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Core
    APP_NAME: str = "Overdrive Intel"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:password@localhost:5434/overdrive_intel"
    )
    DATABASE_SSL: bool = False

    # Cache / Queue
    REDIS_URL: str = "redis://localhost:6381/0"

    # LLM & Embeddings
    ANTHROPIC_API_KEY: Optional[str] = None
    VOYAGE_API_KEY: Optional[str] = None

    # External API keys (optional)
    GITHUB_TOKEN: Optional[str] = None
    SLACK_WEBHOOK_URL: Optional[str] = None  # System-level alerts (dead man's switch)
    SLACK_DIGEST_WEBHOOK_URL: Optional[str] = None  # Team daily digest
    LOG_FORMAT: Optional[
        str
    ] = None  # Future override; production JSON via ENVIRONMENT check

    # Phase 12: Extended ingestion adapters
    BLUESKY_HANDLE: Optional[str] = None
    BLUESKY_APP_PASSWORD: Optional[str] = None
    MAILGUN_WEBHOOK_SIGNING_KEY: Optional[str] = None

    # LLM model selection
    LLM_MODEL: str = "claude-haiku-4-5"  # alias — always resolves to latest Haiku

    # Embedding config
    EMBEDDING_MODEL: str = "voyage-3.5-lite"
    EMBEDDING_DIM: int = 1024

    # Spend Management
    DAILY_SPEND_LIMIT: float = 10.0
    LLM_DAILY_ALERT_THRESHOLD: float = 7.0

    # Rate Limits — configurable per endpoint tier (H-14)
    # Values are slowapi format strings (e.g. "100/minute")
    # Operator-tunable via env vars: RATE_LIMIT_FEED, RATE_LIMIT_SEARCH, etc.
    RATE_LIMIT_FEED: str = "100/minute"
    RATE_LIMIT_SEARCH: str = "60/minute"
    RATE_LIMIT_CONTEXT_PACK: str = "60/minute"
    RATE_LIMIT_ADMIN: str = "10/minute"
    RATE_LIMIT_DEFAULT: str = "100/minute"

    # Reverse proxy — trusted CIDRs for X-Forwarded-For extraction
    # Default covers Docker bridge networks and common private subnets.
    # In non-proxy deployments, direct client IPs won't match these ranges.
    TRUSTED_PROXY_CIDRS: str = "172.16.0.0/12,10.0.0.0/8,192.168.0.0/16"

    # Self-service registration
    SIGNUP_ENABLED: bool = True

    # Relevance gate threshold — items with gate score >= this pass to queued
    # 0.65 balances signal vs noise: recovers agent framework releases, SDK updates,
    # and competing tool content that was wrongly filtered at 0.8
    RELEVANCE_THRESHOLD: float = 0.65

    # Clustering — cosine distance threshold for grouping similar items
    CLUSTER_DISTANCE_THRESHOLD: float = 0.15

    # Storage cleanup — null embeddings on filtered items to reclaim pgvector storage
    STORAGE_CLEANUP_ENABLED: bool = True
    STORAGE_CLEANUP_MIN_AGE_HOURS: int = 24

    # Storage monitoring — alert thresholds for DB size (Neon Launch = usage-based, no hard cap)
    STORAGE_LIMIT_MB: int = 10240  # 10GB soft budget — alerts fire relative to this
    STORAGE_WARN_PCT: int = 80
    STORAGE_CRITICAL_PCT: int = 90

    # Response cache — Redis-backed caching for high-traffic API endpoints
    CACHE_ENABLED: bool = True
    CACHE_TTL_SECONDS: int = 300  # 5 minutes

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Reject missing API keys in production to prevent silent failures."""
        if self.ENVIRONMENT != "production":
            return self
        if not self.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY required in production")
        if not self.VOYAGE_API_KEY:
            raise ValueError("VOYAGE_API_KEY required in production")
        # C-4: Missing signing key means webhook accepts unauthenticated requests.
        # Fail hard at startup rather than silently accepting arbitrary content.
        if not self.MAILGUN_WEBHOOK_SIGNING_KEY:
            raise ValueError("MAILGUN_WEBHOOK_SIGNING_KEY required in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
