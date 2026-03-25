from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import get_settings

engine: AsyncEngine | None = None
async_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """Initialize the async database engine and session factory."""
    global engine, async_session_factory
    settings = get_settings()

    connect_args: dict = {}
    use_ssl = settings.DATABASE_SSL or "neon.tech" in str(settings.DATABASE_URL)
    if use_ssl:
        connect_args = {
            "ssl": "require",
            "statement_cache_size": 0,  # Required for Neon PgBouncer transaction mode
        }

    engine = create_async_engine(
        str(settings.DATABASE_URL),
        echo=settings.ENVIRONMENT == "development",
        # Pool sizing: Neon free tier allows ~20 total connections.
        # 2 uvicorn workers × (2+2) = 8 connections for API
        # fast-worker (2+2) = 4 connections
        # slow-worker (2+2) = 4 connections
        # Total max = 16, leaving 4 headroom for health checks and admin connections.
        # Previous sizing (pool_size=2, max_overflow=3) gave exactly 20 = zero headroom.
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async generator that yields a database session. Use as FastAPI dependency."""
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with async_session_factory() as session:
        yield session


async def close_db() -> None:
    """Dispose the database engine. Call on application shutdown."""
    global engine
    if engine is not None:
        await engine.dispose()
        engine = None
