"""
INGEST-04, INGEST-05, INGEST-06: Source health service tests.

Tests for:
- INGEST-04: Circuit breaker (consecutive errors → source deactivation)
- INGEST-05: Cooldown (Redis SET NX prevents duplicate polls within interval)
- INGEST-06: Health field tracking (last_successful_poll, last_fetched_at, etag/LM storage)
"""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models.models import Source
from src.services.source_health import (
    MAX_CONSECUTIVE_ERRORS,
    MAX_RECOVERY_ATTEMPTS,
    check_source_recovery,
    handle_source_error,
    handle_source_success,
    is_source_on_cooldown,
)


# ===========================================================================
# INGEST-04: Circuit breaker tests
# ===========================================================================


@pytest.mark.asyncio
async def test_circuit_breaker_threshold_is_5():
    """MAX_CONSECUTIVE_ERRORS must be exactly 5."""
    assert MAX_CONSECUTIVE_ERRORS == 5


@pytest.mark.asyncio
async def test_circuit_breaker_increments_errors(session, source_factory):
    """Single error increments consecutive_errors by 1; source stays active."""
    source = await source_factory(id="test:cb-incr")

    await handle_source_error(session, source, Exception("timeout"))

    session.expire_all()
    result = await session.execute(select(Source).where(Source.id == "test:cb-incr"))
    refreshed = result.scalar_one()

    assert refreshed.consecutive_errors == 1
    assert refreshed.is_active is True


@pytest.mark.asyncio
async def test_circuit_breaker_marks_dead_at_threshold(session, source_factory):
    """At 5 consecutive errors the source must be marked inactive."""
    source = await source_factory(id="test:cb-dead")
    # Directly set consecutive_errors to 4 (one below threshold)
    source.consecutive_errors = 4
    await session.commit()

    await handle_source_error(session, source, Exception("fifth error"))

    session.expire_all()
    result = await session.execute(select(Source).where(Source.id == "test:cb-dead"))
    refreshed = result.scalar_one()

    assert refreshed.consecutive_errors == 5
    assert refreshed.is_active is False


@pytest.mark.asyncio
async def test_circuit_breaker_source_stays_active_below_threshold(
    session, source_factory
):
    """Source should remain active at consecutive_errors < MAX_CONSECUTIVE_ERRORS."""
    source = await source_factory(id="test:cb-below")
    source.consecutive_errors = 3
    await session.commit()

    await handle_source_error(session, source, Exception("fourth error"))

    session.expire_all()
    result = await session.execute(select(Source).where(Source.id == "test:cb-below"))
    refreshed = result.scalar_one()

    assert refreshed.consecutive_errors == 4
    assert refreshed.is_active is True


@pytest.mark.asyncio
async def test_success_resets_errors(session, source_factory):
    """Successful poll resets consecutive_errors to 0 regardless of prior count."""
    source = await source_factory(id="test:cb-reset")
    source.consecutive_errors = 3
    await session.commit()

    await handle_source_success(session, source)

    session.expire_all()
    result = await session.execute(select(Source).where(Source.id == "test:cb-reset"))
    refreshed = result.scalar_one()

    assert refreshed.consecutive_errors == 0
    assert refreshed.last_successful_poll is not None


# ===========================================================================
# INGEST-05: Cooldown tests
# ===========================================================================


@pytest.mark.asyncio
async def test_cooldown_first_call_returns_false(redis_client):
    """First call to is_source_on_cooldown sets the key and returns False (ready to poll)."""
    result = await is_source_on_cooldown(redis_client, "src-cooldown-1", 60)
    assert result is False


@pytest.mark.asyncio
async def test_cooldown_second_call_returns_true(redis_client):
    """Second call with same source_id returns True (on cooldown — key already exists)."""
    await is_source_on_cooldown(redis_client, "src-cooldown-2", 60)
    result = await is_source_on_cooldown(redis_client, "src-cooldown-2", 60)
    assert result is True


@pytest.mark.asyncio
async def test_cooldown_expires(redis_client):
    """Cooldown TTL expires after poll_interval_seconds; source becomes ready again."""
    # Set cooldown with 1-second TTL
    first = await is_source_on_cooldown(redis_client, "src-cooldown-expire", 1)
    assert first is False  # key just set

    # Wait for TTL to expire
    await asyncio.sleep(1.5)

    # Should be ready again (key expired)
    result = await is_source_on_cooldown(redis_client, "src-cooldown-expire", 1)
    assert result is False


@pytest.mark.asyncio
async def test_cooldown_different_sources_independent(redis_client):
    """Cooldown for one source does not affect a different source."""
    # Set cooldown for src1
    await is_source_on_cooldown(redis_client, "src-a", 60)

    # src2 should NOT be on cooldown
    result = await is_source_on_cooldown(redis_client, "src-b", 60)
    assert result is False


@pytest.mark.asyncio
async def test_cooldown_key_has_correct_ttl(redis_client):
    """Redis key TTL matches the requested poll_interval_seconds (within tolerance)."""
    await is_source_on_cooldown(redis_client, "src-ttl-check", 120)
    ttl = await redis_client.ttl("source:cooldown:src-ttl-check")
    # TTL should be between 118 and 120 (small processing time margin)
    assert 118 <= ttl <= 120


# ===========================================================================
# INGEST-06: Health field tracking tests
# ===========================================================================


@pytest.mark.asyncio
async def test_success_updates_last_successful_poll(session, source_factory):
    """handle_source_success must set last_successful_poll to a recent timestamp."""
    source = await source_factory(id="test:health-poll")
    before = datetime.now(timezone.utc)

    await handle_source_success(session, source)

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:health-poll")
    )
    refreshed = result.scalar_one()

    assert refreshed.last_successful_poll is not None
    assert refreshed.last_successful_poll >= before
    assert refreshed.last_successful_poll <= datetime.now(timezone.utc) + timedelta(
        seconds=1
    )


@pytest.mark.asyncio
async def test_success_updates_last_fetched_at(session, source_factory):
    """handle_source_success must update last_fetched_at."""
    source = await source_factory(id="test:health-fetched-success")
    before = datetime.now(timezone.utc)

    await handle_source_success(session, source)

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:health-fetched-success")
    )
    refreshed = result.scalar_one()

    assert refreshed.last_fetched_at is not None
    assert refreshed.last_fetched_at >= before


@pytest.mark.asyncio
async def test_success_stores_etag(session, source_factory):
    """handle_source_success must persist new_etag to source.last_etag."""
    source = await source_factory(id="test:health-etag")

    await handle_source_success(session, source, new_etag="abc123")

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:health-etag")
    )
    refreshed = result.scalar_one()

    assert refreshed.last_etag == "abc123"


@pytest.mark.asyncio
async def test_success_stores_last_modified(session, source_factory):
    """handle_source_success must persist new_last_modified to source.last_modified_header."""
    source = await source_factory(id="test:health-lm")
    lm_value = "Mon, 01 Jan 2024 00:00:00 GMT"

    await handle_source_success(session, source, new_last_modified=lm_value)

    session.expire_all()
    result = await session.execute(select(Source).where(Source.id == "test:health-lm"))
    refreshed = result.scalar_one()

    assert refreshed.last_modified_header == lm_value


@pytest.mark.asyncio
async def test_success_does_not_overwrite_etag_when_none(session, source_factory):
    """handle_source_success with new_etag=None must not clear an existing etag."""
    source = await source_factory(id="test:health-etag-keep")
    source.last_etag = "existing-etag"
    await session.commit()

    await handle_source_success(session, source, new_etag=None)

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:health-etag-keep")
    )
    refreshed = result.scalar_one()

    assert refreshed.last_etag == "existing-etag"


@pytest.mark.asyncio
async def test_error_updates_last_fetched_at(session, source_factory):
    """handle_source_error must update last_fetched_at even on failure."""
    source = await source_factory(id="test:health-fetched-error")
    before = datetime.now(timezone.utc)

    await handle_source_error(session, source, Exception("network error"))

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:health-fetched-error")
    )
    refreshed = result.scalar_one()

    assert refreshed.last_fetched_at is not None
    assert refreshed.last_fetched_at >= before


@pytest.mark.asyncio
async def test_error_does_not_update_last_successful_poll(session, source_factory):
    """handle_source_error must NOT update last_successful_poll."""
    source = await source_factory(id="test:health-no-poll-on-error")
    # last_successful_poll is None by default

    await handle_source_error(session, source, Exception("error"))

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:health-no-poll-on-error")
    )
    refreshed = result.scalar_one()

    assert refreshed.last_successful_poll is None


# ===========================================================================
# Recovery with permanent death logic tests
# ===========================================================================


@pytest.mark.asyncio
async def test_max_recovery_attempts_is_3():
    """MAX_RECOVERY_ATTEMPTS must be exactly 3."""
    assert MAX_RECOVERY_ATTEMPTS == 3


@pytest.mark.asyncio
async def test_recovery_increments_recovery_attempts(session, source_factory):
    """check_source_recovery increments recovery_attempts on each recovery cycle."""
    source = await source_factory(id="test:recovery-incr")
    source.is_active = False
    source.last_fetched_at = datetime.now(timezone.utc) - timedelta(hours=49)
    source.consecutive_errors = 5
    source.recovery_attempts = 0
    await session.commit()

    recovered = await check_source_recovery(session)

    assert recovered == 1
    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:recovery-incr")
    )
    refreshed = result.scalar_one()
    assert refreshed.is_active is True
    assert refreshed.consecutive_errors == 0
    assert refreshed.recovery_attempts == 1


@pytest.mark.asyncio
async def test_recovery_skips_permanently_dead_sources(session, source_factory):
    """Sources with recovery_attempts >= 3 are NOT recovered."""
    source = await source_factory(id="test:recovery-dead")
    source.is_active = False
    source.last_fetched_at = datetime.now(timezone.utc) - timedelta(hours=49)
    source.consecutive_errors = 5
    source.recovery_attempts = 3
    await session.commit()

    recovered = await check_source_recovery(session)

    assert recovered == 0
    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:recovery-dead")
    )
    refreshed = result.scalar_one()
    assert refreshed.is_active is False  # Still dead
    assert refreshed.recovery_attempts == 3  # Not incremented


@pytest.mark.asyncio
async def test_recovery_stops_after_3_cycles(session, source_factory):
    """Source that fails after each recovery eventually hits permanent death."""
    source = await source_factory(id="test:recovery-cycle")
    source.is_active = False
    source.last_fetched_at = datetime.now(timezone.utc) - timedelta(hours=49)
    source.consecutive_errors = 5
    source.recovery_attempts = 2  # Already recovered twice
    await session.commit()

    # Third recovery — should succeed (recovery_attempts goes to 3)
    recovered = await check_source_recovery(session)
    assert recovered == 1

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:recovery-cycle")
    )
    refreshed = result.scalar_one()
    assert refreshed.recovery_attempts == 3

    # Simulate source failing again and being deactivated
    refreshed.is_active = False
    refreshed.consecutive_errors = 5
    refreshed.last_fetched_at = datetime.now(timezone.utc) - timedelta(hours=49)
    await session.commit()

    # Fourth recovery attempt — should NOT recover (at limit)
    recovered = await check_source_recovery(session)
    assert recovered == 0


@pytest.mark.asyncio
async def test_recovery_only_recovers_eligible_sources(session, source_factory):
    """Only deactivated sources past cooldown and under recovery limit are recovered."""
    # Eligible source
    eligible = await source_factory(id="test:recovery-eligible")
    eligible.is_active = False
    eligible.last_fetched_at = datetime.now(timezone.utc) - timedelta(hours=49)
    eligible.recovery_attempts = 1

    # Permanently dead source
    dead = await source_factory(id="test:recovery-perm-dead")
    dead.is_active = False
    dead.last_fetched_at = datetime.now(timezone.utc) - timedelta(hours=49)
    dead.recovery_attempts = 3

    # Active source (should not be touched)
    active = await source_factory(id="test:recovery-active")
    active.is_active = True
    active.recovery_attempts = 0

    # Recently deactivated source (within 48h cooldown)
    recent = await source_factory(id="test:recovery-recent")
    recent.is_active = False
    recent.last_fetched_at = datetime.now(timezone.utc) - timedelta(hours=1)
    recent.recovery_attempts = 0

    await session.commit()

    recovered = await check_source_recovery(session)
    assert recovered == 1  # Only the eligible one

    session.expire_all()
    result = await session.execute(
        select(Source).where(Source.id == "test:recovery-eligible")
    )
    assert result.scalar_one().is_active is True

    result = await session.execute(
        select(Source).where(Source.id == "test:recovery-perm-dead")
    )
    assert result.scalar_one().is_active is False

    result = await session.execute(
        select(Source).where(Source.id == "test:recovery-active")
    )
    assert result.scalar_one().is_active is True

    result = await session.execute(
        select(Source).where(Source.id == "test:recovery-recent")
    )
    assert result.scalar_one().is_active is False
