"""
FOUND-02: Prove the Alembic migration path works independently of SQLAlchemy metadata.create_all.

This test:
1. Sets up a separate migration test database
2. Runs alembic upgrade head from an empty state
3. Verifies all tables and pgvector extension exist
4. Runs alembic downgrade base and verifies tables are gone

Uses a separate DB name (overdrive_intel_migration_test) to avoid conflicting
with the main test session (overdrive_intel_test).
"""
import os
import subprocess
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text, inspect


MIGRATION_TEST_DB = "overdrive_intel_migration_test"
MIGRATION_ASYNC_URL = (
    f"postgresql+asyncpg://postgres:password@localhost:5434/{MIGRATION_TEST_DB}"
)


def _ensure_test_db_exists():
    """
    Create the migration test database if it doesn't exist.
    Uses docker exec since psycopg2 is not installed for Python 3.12.
    """
    result = subprocess.run(
        [
            "docker",
            "exec",
            "overdrive-intel-db-1",
            "psql",
            "-U",
            "postgres",
            "-c",
            f"SELECT 1 FROM pg_database WHERE datname = '{MIGRATION_TEST_DB}'",
        ],
        capture_output=True,
        text=True,
    )
    if "(1 row)" not in result.stdout:
        subprocess.run(
            [
                "docker",
                "exec",
                "overdrive-intel-db-1",
                "psql",
                "-U",
                "postgres",
                "-c",
                f"CREATE DATABASE {MIGRATION_TEST_DB}",
            ],
            check=True,
        )


def _drop_all_public_tables():
    """
    Drops and recreates the public schema to get a clean state.
    Uses docker exec.
    """
    subprocess.run(
        [
            "docker",
            "exec",
            "overdrive-intel-db-1",
            "psql",
            "-U",
            "postgres",
            "-d",
            MIGRATION_TEST_DB,
            "-c",
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public;",
        ],
        check=True,
    )


def _get_alembic_cfg() -> Config:
    """Build Alembic config pointing to migration test database."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(project_root, "alembic.ini"))
    # Override URL in Settings-based env.py via environment variable
    # (env.py reads from get_settings() which reads from os.environ)
    return cfg


@pytest.fixture(scope="module", autouse=True)
def migration_test_db():
    """
    Module-scoped fixture: ensures migration test DB exists and is clean.
    Temporarily overrides DATABASE_URL env var for the duration of migration tests
    so Alembic's env.py (which reads from get_settings()) uses the migration DB.
    Teardown: restores original DATABASE_URL and drops schema clean.
    """
    _ensure_test_db_exists()
    _drop_all_public_tables()

    # Override DATABASE_URL + clear Settings cache so Alembic uses migration DB
    original_db_url = os.environ.get("DATABASE_URL", "")
    os.environ["DATABASE_URL"] = MIGRATION_ASYNC_URL

    # Bust the lru_cache on get_settings() so it re-reads the new URL
    from src.core.config import get_settings

    get_settings.cache_clear()

    yield

    # Restore original DATABASE_URL and re-clear cache
    os.environ["DATABASE_URL"] = original_db_url
    get_settings.cache_clear()

    # Final cleanup for next run
    _drop_all_public_tables()


def test_alembic_upgrade_head_creates_all_tables(migration_test_db):
    """FOUND-02: alembic upgrade head from empty DB creates all 6 tables."""
    cfg = _get_alembic_cfg()
    command.upgrade(cfg, "head")

    import asyncio

    async def _verify():
        eng = create_async_engine(MIGRATION_ASYNC_URL)
        async with eng.connect() as conn:

            def _get_tables(sync_conn):
                inspector = inspect(sync_conn)
                return set(inspector.get_table_names())

            table_names = await conn.run_sync(_get_tables)
        await eng.dispose()
        return table_names

    table_names = asyncio.run(_verify())

    expected_tables = {
        "sources",
        "intel_items",
        "users",
        "api_keys",
        "alert_rules",
        "reference_items",
    }
    assert expected_tables.issubset(
        table_names
    ), f"Missing tables after upgrade head: {expected_tables - table_names}"


def test_alembic_upgrade_head_creates_pgvector_extension(migration_test_db):
    """FOUND-02: alembic upgrade head applies CREATE EXTENSION vector."""
    import asyncio

    async def _verify():
        eng = create_async_engine(MIGRATION_ASYNC_URL)
        async with eng.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            )
            row = result.fetchone()
        await eng.dispose()
        return row

    row = asyncio.run(_verify())
    assert row is not None, "pgvector extension not installed after upgrade head"


def test_alembic_upgrade_head_intel_items_embedding_nullable(migration_test_db):
    """FOUND-02: intel_items.embedding column should be nullable after upgrade."""
    import asyncio

    async def _verify():
        eng = create_async_engine(MIGRATION_ASYNC_URL)
        async with eng.connect() as conn:

            def _check(sync_conn):
                inspector = inspect(sync_conn)
                columns = {
                    col["name"]: col for col in inspector.get_columns("intel_items")
                }
                return columns.get("embedding")

            col = await conn.run_sync(_check)
        await eng.dispose()
        return col

    col = asyncio.run(_verify())
    assert col is not None, "embedding column missing from intel_items"
    assert col["nullable"], "embedding should be nullable"


def test_alembic_upgrade_head_api_keys_usage_count_integer(migration_test_db):
    """FOUND-02: api_keys.usage_count should be Integer after upgrade."""
    import asyncio
    from sqlalchemy import Integer

    async def _verify():
        eng = create_async_engine(MIGRATION_ASYNC_URL)
        async with eng.connect() as conn:

            def _check(sync_conn):
                inspector = inspect(sync_conn)
                columns = {
                    col["name"]: col for col in inspector.get_columns("api_keys")
                }
                return columns.get("usage_count")

            col = await conn.run_sync(_check)
        await eng.dispose()
        return col

    col = asyncio.run(_verify())
    assert col is not None, "usage_count column missing from api_keys"
    assert isinstance(col["type"], Integer)


def test_alembic_downgrade_base_drops_all_tables(migration_test_db):
    """FOUND-02: alembic downgrade base removes all application tables."""
    import asyncio

    cfg = _get_alembic_cfg()
    command.downgrade(cfg, "base")

    async def _verify():
        eng = create_async_engine(MIGRATION_ASYNC_URL)
        async with eng.connect() as conn:

            def _get_tables(sync_conn):
                inspector = inspect(sync_conn)
                return set(inspector.get_table_names())

            table_names = await conn.run_sync(_get_tables)
        await eng.dispose()
        return table_names

    table_names = asyncio.run(_verify())

    app_tables = {
        "sources",
        "intel_items",
        "users",
        "api_keys",
        "alert_rules",
        "reference_items",
    }
    remaining = app_tables.intersection(table_names)
    assert not remaining, f"Tables still present after downgrade base: {remaining}"
