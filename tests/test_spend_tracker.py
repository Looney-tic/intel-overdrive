"""
FOUND-05: Spend tracker tests.

Proves SpendLimitExceeded is raised (not just returned) when the limit is hit.
All tests use the redis_client fixture (DB 1) from conftest.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from src.services.spend_tracker import SpendTracker, SpendLimitExceeded


@pytest_asyncio.fixture
async def spend_tracker(redis_client):
    """SpendTracker instance backed by test Redis (DB 1)."""
    # Mock settings to use a known limit
    mock_settings = MagicMock()
    mock_settings.DAILY_SPEND_LIMIT = 1.00  # $1.00 limit for testing

    tracker = SpendTracker(redis_client)
    tracker.settings = mock_settings
    return tracker


@pytest.mark.asyncio
async def test_get_current_spend_returns_zero_initially(spend_tracker):
    """FOUND-05: Fresh Redis DB has zero spend."""
    current = await spend_tracker.get_current_spend()
    assert current == 0.0


@pytest.mark.asyncio
async def test_get_remaining_spend_returns_full_limit_initially(spend_tracker):
    """FOUND-05: Remaining spend equals full limit when no spend recorded."""
    remaining = await spend_tracker.get_remaining_spend()
    assert remaining == 1.00


@pytest.mark.asyncio
async def test_track_spend_succeeds_under_limit(spend_tracker):
    """FOUND-05: track_spend returns new total when under limit."""
    new_total = await spend_tracker.track_spend(0.25)
    assert new_total == pytest.approx(0.25, abs=0.001)


@pytest.mark.asyncio
async def test_track_spend_accumulates_correctly(spend_tracker):
    """FOUND-05: Multiple track_spend calls accumulate correctly."""
    await spend_tracker.track_spend(0.10)
    await spend_tracker.track_spend(0.20)
    total = await spend_tracker.get_current_spend()
    assert total == pytest.approx(0.30, abs=0.001)


@pytest.mark.asyncio
async def test_track_spend_raises_spend_limit_exceeded_at_limit(spend_tracker):
    """FOUND-05: track_spend raises SpendLimitExceeded (not returns) when over limit."""
    # Spend up to $0.99 first
    await spend_tracker.track_spend(0.99)

    # This $0.02 addition would exceed the $1.00 limit
    with pytest.raises(SpendLimitExceeded) as exc_info:
        await spend_tracker.track_spend(0.02)

    # Verify exception carries the right attributes
    exc = exc_info.value
    assert hasattr(exc, "current")
    assert hasattr(exc, "limit")
    assert exc.limit == 1.00


@pytest.mark.asyncio
async def test_track_spend_zero_amount_is_noop(spend_tracker):
    """FOUND-05: track_spend with 0 amount returns current (no increment)."""
    await spend_tracker.track_spend(0.50)
    result = await spend_tracker.track_spend(0.0)
    assert result == pytest.approx(0.50, abs=0.001)


@pytest.mark.asyncio
async def test_check_spend_gate_passes_when_under_limit(spend_tracker):
    """FOUND-05: check_spend_gate does not raise when under limit."""
    await spend_tracker.track_spend(0.50)
    # Should not raise
    await spend_tracker.check_spend_gate()


@pytest.mark.asyncio
async def test_check_spend_gate_raises_when_at_limit(spend_tracker):
    """FOUND-05: check_spend_gate raises SpendLimitExceeded when at/over limit."""
    # Spend exactly the limit
    await spend_tracker.track_spend(1.00)

    with pytest.raises(SpendLimitExceeded) as exc_info:
        await spend_tracker.check_spend_gate()

    exc = exc_info.value
    assert exc.current >= exc.limit


@pytest.mark.asyncio
async def test_spend_limit_exceeded_exception_message(spend_tracker):
    """FOUND-05: SpendLimitExceeded has informative str representation."""
    await spend_tracker.track_spend(0.99)
    with pytest.raises(SpendLimitExceeded) as exc_info:
        await spend_tracker.track_spend(0.02)

    assert (
        "exceeded" in str(exc_info.value).lower()
        or "limit" in str(exc_info.value).lower()
    )


@pytest.mark.asyncio
async def test_remaining_spend_decreases_after_tracking(spend_tracker):
    """FOUND-05: get_remaining_spend decreases as spend is tracked."""
    await spend_tracker.track_spend(0.30)
    remaining = await spend_tracker.get_remaining_spend()
    assert remaining == pytest.approx(0.70, abs=0.001)


@pytest.mark.asyncio
async def test_dollars_to_cents_rounds_correctly(spend_tracker):
    """FOUND-05: Dollar-to-cents conversion handles sub-cent precision.

    After C-3 fix: any non-zero charge is at least 1 cent (max(1, round(...))).
    This prevents spend gate bypass through accumulation of sub-cent calls.
    """
    assert spend_tracker._dollars_to_cents(0.01) == 1
    assert spend_tracker._dollars_to_cents(0.99) == 99
    assert spend_tracker._dollars_to_cents(1.00) == 100
    assert (
        spend_tracker._dollars_to_cents(0.005) == 1
    )  # C-3: sub-cent rounds to minimum 1
    assert spend_tracker._dollars_to_cents(0.015) == 2  # rounds to 2 (banker's)
    assert spend_tracker._dollars_to_cents(0.0) == 0  # zero stays zero


@pytest.mark.asyncio
async def test_negative_amount_returns_current_spend(spend_tracker):
    """FOUND-05: track_spend with negative amount is a no-op (same as zero)."""
    await spend_tracker.track_spend(0.50)
    result = await spend_tracker.track_spend(-0.10)
    assert result == pytest.approx(0.50, abs=0.001)
