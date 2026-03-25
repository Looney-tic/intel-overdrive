"""
FOUND-01: Verify all 6 database tables exist with correct schema.

Tests run against a live test database (conftest engine fixture).
"""
import pytest
from sqlalchemy import inspect, Integer
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.mark.asyncio
async def test_all_six_tables_exist(engine: AsyncEngine):
    """FOUND-01: All 6 expected tables must be created by Base.metadata.create_all."""
    expected_tables = {
        "sources",
        "intel_items",
        "users",
        "api_keys",
        "alert_rules",
        "reference_items",
    }

    def _get_table_names(conn):
        inspector = inspect(conn)
        return set(inspector.get_table_names())

    async with engine.connect() as conn:
        table_names = await conn.run_sync(_get_table_names)

    assert expected_tables.issubset(
        table_names
    ), f"Missing tables: {expected_tables - table_names}"


@pytest.mark.asyncio
async def test_intel_item_embedding_columns_nullable(engine: AsyncEngine):
    """FOUND-01: IntelItem.embedding and embedding_model_version must be nullable."""

    def _check(conn):
        inspector = inspect(conn)
        columns = {col["name"]: col for col in inspector.get_columns("intel_items")}
        assert "embedding" in columns, "embedding column missing from intel_items"
        assert columns["embedding"]["nullable"], "embedding should be nullable"
        assert (
            "embedding_model_version" in columns
        ), "embedding_model_version column missing"
        assert columns["embedding_model_version"][
            "nullable"
        ], "embedding_model_version should be nullable"

    async with engine.connect() as conn:
        await conn.run_sync(_check)


@pytest.mark.asyncio
async def test_api_key_usage_count_and_key_prefix(engine: AsyncEngine):
    """FOUND-01: APIKey must have integer usage_count and key_prefix column."""

    def _check(conn):
        inspector = inspect(conn)
        columns = {col["name"]: col for col in inspector.get_columns("api_keys")}
        assert "usage_count" in columns, "usage_count column missing from api_keys"
        assert isinstance(
            columns["usage_count"]["type"], Integer
        ), "usage_count should be Integer"
        assert "key_prefix" in columns, "key_prefix column missing from api_keys"

    async with engine.connect() as conn:
        await conn.run_sync(_check)


@pytest.mark.asyncio
async def test_source_polling_columns(engine: AsyncEngine):
    """FOUND-01: Source must have consecutive_errors, last_successful_poll, poll_interval_seconds, tier."""

    def _check(conn):
        inspector = inspect(conn)
        columns = {col["name"]: col for col in inspector.get_columns("sources")}
        assert "consecutive_errors" in columns
        assert "last_successful_poll" in columns
        assert "poll_interval_seconds" in columns
        assert "tier" in columns

    async with engine.connect() as conn:
        await conn.run_sync(_check)


@pytest.mark.asyncio
async def test_intel_item_content_hash_url_hash(engine: AsyncEngine):
    """FOUND-01: IntelItem must have content_hash (Layer 2) and url_hash (Layer 1) columns."""

    def _check(conn):
        inspector = inspect(conn)
        columns = {col["name"]: col for col in inspector.get_columns("intel_items")}
        assert "content_hash" in columns
        assert "url_hash" in columns

    async with engine.connect() as conn:
        await conn.run_sync(_check)
