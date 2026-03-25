"""Tests for src/api/query_logger.py — fire-and-forget query logging with rollback."""

import pytest
from sqlalchemy import text

from src.api.query_logger import log_query
from src.models.models import User, APIKey
from src.services.auth_service import AuthService


@pytest.mark.asyncio
async def test_log_query_success(session):
    """log_query inserts a row into query_logs when given valid data."""
    # Create prerequisite User + APIKey
    auth = AuthService()
    raw_key, key_hash = auth.generate_api_key()

    user = User(email="logger-test@example.com", is_active=True, profile={})
    session.add(user)
    await session.flush()

    api_key = APIKey(
        key_hash=key_hash, key_prefix="dti_v1_", user_id=user.id, is_active=True
    )
    session.add(api_key)
    await session.commit()

    # Log a query
    await log_query(
        session=session,
        api_key_id=api_key.id,
        query_type="search",
        query_text="test query",
        result_count=5,
    )

    # Verify the row was inserted
    result = await session.execute(
        text(
            "SELECT query_type, query_text, result_count FROM query_logs WHERE api_key_id = :kid"
        ),
        {"kid": api_key.id},
    )
    row = result.fetchone()
    assert row is not None, "Expected a query_log row to be inserted"
    assert row.query_type == "search"
    assert row.query_text == "test query"
    assert row.result_count == 5


@pytest.mark.asyncio
async def test_log_query_rollback_on_failure(session):
    """After a failed log_query (FK violation), the session remains usable.

    This is the critical regression test: log_query must rollback on failure
    so the session isn't left in an aborted transaction state.
    """
    # Call log_query with a non-existent api_key_id to trigger FK violation
    # log_query swallows the exception and rolls back internally
    await log_query(
        session=session,
        api_key_id=999999,  # does not exist — FK constraint violation
        query_type="search",
        query_text="should fail",
        result_count=0,
    )

    # The critical assertion: session is still usable after the failed INSERT
    # If log_query didn't rollback, this SELECT would raise
    # "InFailedSqlTransaction" or similar
    result = await session.execute(text("SELECT 1 AS alive"))
    row = result.fetchone()
    assert row is not None
    assert row.alive == 1


@pytest.mark.asyncio
async def test_log_query_rollback_preserves_prior_data(session):
    """After a failed log_query, previously committed data is still accessible."""
    # Create a user first
    user = User(email="persist-test@example.com", is_active=True, profile={})
    session.add(user)
    await session.commit()

    # Trigger a failed log_query
    await log_query(
        session=session,
        api_key_id=999999,
        query_type="feed",
        query_text="should fail",
        result_count=0,
    )

    # Prior data is still accessible — session wasn't corrupted
    result = await session.execute(
        text("SELECT email FROM users WHERE email = :email"),
        {"email": "persist-test@example.com"},
    )
    row = result.fetchone()
    assert row is not None
    assert row.email == "persist-test@example.com"
