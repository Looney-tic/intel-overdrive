"""
Test configuration and fixtures for overdrive-intel.

Environment variables are set BEFORE importing anything project-related to ensure
Settings() picks up the test database and Redis URLs.
"""
import os
import sys
from pathlib import Path

# Add src/ to sys.path so CLI modules (cli.*) can be imported without the src. prefix.
# This matches the [project.scripts] entry point: "cli.main:app"
_src_dir = str(Path(__file__).resolve().parent.parent / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# Set env vars before any project imports
os.environ["ENVIRONMENT"] = "development"
os.environ[
    "DATABASE_URL"
] = "postgresql+asyncpg://postgres:password@localhost:5434/overdrive_intel_test"
os.environ["REDIS_URL"] = "redis://localhost:6381/1"  # DB 1 for tests (not 0)
os.environ["DAILY_SPEND_LIMIT"] = "10.0"  # Override .env to match test defaults
os.environ.setdefault("DATABASE_SSL", "false")
# Stub out API keys so Settings() doesn't fail
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("VOYAGE_API_KEY", "test-key")

# Clear lru_cache on get_settings so env vars take effect
from src.core.config import get_settings

get_settings.cache_clear()

import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

# Import all models so Base.metadata.create_all picks them up
from src.models.base import Base
from src.models.models import (  # noqa: F401
    Source,
    IntelItem,
    User,
    APIKey,
    Feedback,
    AlertRule,
    ReferenceItem,
    LibraryItem,
    ItemSignal,
    QueryLog,
)

TEST_DATABASE_URL = os.environ["DATABASE_URL"]
TEST_REDIS_URL = os.environ["REDIS_URL"]


# ---------------------------------------------------------------------------
# Docker connectivity guard — fails fast with a clear message
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def check_docker_services(request):
    """
    Session-scoped fixture that verifies Postgres and Redis are reachable.
    Uses pure socket-level checks to avoid creating an asyncio event loop
    that would conflict with pytest-asyncio's session-scoped loop.
    If either service is down, aborts the test run with a clear error message.

    Skipped when only CLI tests (test_cli_*) are collected — they mock all I/O.
    """
    collected_files = {item.fspath.basename for item in request.session.items}
    if collected_files and all(str(f).startswith("test_cli_") for f in collected_files):
        yield
        return

    import socket
    import redis as sync_redis

    def _check_tcp(host: str, port: int, service: str):
        try:
            sock = socket.create_connection((host, port), timeout=3)
            sock.close()
        except OSError as exc:
            pytest.exit(
                f"Docker Compose not running or {service} unreachable — "
                f"run 'docker compose up -d' first. Error: {exc}"
            )

    def _check_redis_ping():
        try:
            r = sync_redis.from_url("redis://localhost:6381/1")
            r.ping()
            r.close()
        except Exception as exc:
            pytest.exit(
                f"Docker Compose not running or Redis unreachable — "
                f"run 'docker compose up -d' first. Error: {exc}"
            )

    _check_tcp("localhost", 5434, "Postgres")
    _check_redis_ping()

    yield


# ---------------------------------------------------------------------------
# Async engine + session fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def engine(check_docker_services):
    """
    Function-scoped async engine against the test database.

    Creates all tables on setup, drops them on teardown.
    Function-scoped to avoid asyncio event loop conflicts with pytest-asyncio 0.24+.
    """
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with eng.begin() as conn:
        # Enable pgvector extension before create_all (idempotent)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # The idx_library_tags GIN index on library_items.tags (JSON column) fails
        # during create_all because JSON has no default GIN operator class.
        # Remove it from metadata temporarily so create_all succeeds, then recreate
        # with CAST(tags AS jsonb) — matching the Alembic migration.
        from src.models.models import LibraryItem as _LibraryItem
        import sqlalchemy as _sa

        _gin_idx = None
        for idx in list(_LibraryItem.__table__.indexes):
            if idx.name == "idx_library_tags":
                _gin_idx = idx
                _LibraryItem.__table__.indexes.discard(idx)
                break

        await conn.run_sync(Base.metadata.create_all)

        # Restore the index on metadata so that drop_all can reference it
        if _gin_idx is not None:
            _LibraryItem.__table__.indexes.add(_gin_idx)

        # Recreate idx_library_tags using CAST(tags AS jsonb) — correct for JSON column
        await conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS idx_library_tags
            ON library_items USING gin (CAST(tags AS jsonb))
        """
            )
        )

        # Add search_vector column for tests (metadata.create_all doesn't handle GENERATED)
        await conn.execute(
            text(
                """
            ALTER TABLE intel_items
            ADD COLUMN IF NOT EXISTS search_vector tsvector
            GENERATED ALWAYS AS (
                setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(excerpt, '')), 'B') ||
                setweight(to_tsvector('english', coalesce(content, '')), 'C')
            ) STORED
        """
            )
        )
        await conn.execute(
            text(
                """
            CREATE INDEX IF NOT EXISTS intel_items_search_idx ON intel_items USING gin(search_vector)
        """
            )
        )

    yield eng

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    """Function-scoped async session from the test engine."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        try:
            yield sess
        finally:
            # Safety net: rollback any uncommitted transaction to prevent cascade failures
            try:
                await sess.rollback()
            except Exception:
                pass
            await sess.close()


@pytest_asyncio.fixture
async def client(engine, session, redis_client):
    """
    Async HTTP client for testing the FastAPI app.
    Overrides dependencies to use test session and redis.
    Sets app.state.redis and _init_db module-level state so that the root
    /health endpoint can perform real connectivity checks in tests.
    """
    from src.api.app import app
    from src.api.deps import get_session as _get_session, get_redis as _get_redis
    import src.core.init_db as _init_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async def override_get_session():
        yield session

    async def override_get_redis():
        return redis_client

    app.dependency_overrides[_get_session] = override_get_session
    app.dependency_overrides[_get_redis] = override_get_redis

    # Wire up module-level state for endpoints that bypass DI (e.g. /health)
    app.state.redis = redis_client
    _init_db.async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from httpx import AsyncClient, ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    app.state.redis = None
    _init_db.async_session_factory = None


@pytest_asyncio.fixture
async def api_key_header(session):
    """
    Creates a User + APIKey in the test DB and returns raw key + headers.
    """
    from src.services.auth_service import AuthService

    auth = AuthService()
    raw_key, key_hash = auth.generate_api_key()

    user = User(email="test@example.com", is_active=True, profile={})
    session.add(user)
    await session.flush()

    api_key = APIKey(
        key_hash=key_hash, key_prefix="dti_v1_", user_id=user.id, is_active=True
    )
    session.add(api_key)
    await session.commit()

    return {
        "raw_key": raw_key,
        "headers": {"X-API-Key": raw_key},
        "user_id": user.id,
        "api_key_id": api_key.id,
    }


# ---------------------------------------------------------------------------
# Redis fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def redis_client(check_docker_services):
    """Function-scoped async Redis client on DB 1; flushed after each test."""
    client = aioredis.from_url(TEST_REDIS_URL)
    yield client
    await client.flushdb()
    await client.aclose()


# ---------------------------------------------------------------------------
# Source factory fixture for ingestion tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def source_factory(session):
    """Factory fixture that creates Source rows for ingest tests."""
    created = []

    async def _create(
        id: str = "test:rss-source",
        name: str = "Test Source",
        type: str = "rss",
        url: str = "https://example.com/feed.xml",
        is_active: bool = True,
        poll_interval_seconds: int = 1800,
        tier: str = "tier1",
        config: dict | None = None,
    ) -> Source:
        source = Source(
            id=id,
            name=name,
            type=type,
            url=url,
            is_active=is_active,
            poll_interval_seconds=poll_interval_seconds,
            tier=tier,
            config=config or {},
        )
        session.add(source)
        await session.commit()
        created.append(source)
        return source

    yield _create
