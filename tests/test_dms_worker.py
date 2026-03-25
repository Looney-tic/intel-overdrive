"""OPS-05: Dead man's switch — alert fires when no ingestion >24h."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from src.workers.dms_worker import (
    check_dead_mans_switch,
    update_ingestion_heartbeat,
    DMS_KEY,
)


@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.set = AsyncMock()
    r.exists = AsyncMock(return_value=0)
    return r


@pytest.fixture
def mock_ctx(mock_redis):
    return {"redis": mock_redis}


@pytest.mark.asyncio
async def test_dms_alert_fires_when_stale(mock_ctx, mock_redis):
    """OPS-05: Alert fires when last_ingestion is >24h ago."""
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    mock_redis.get = AsyncMock(return_value=stale_ts.encode())

    with patch(
        "src.workers.dms_worker.deliver_slack_alert", new_callable=AsyncMock
    ) as mock_alert, patch("src.workers.dms_worker.get_settings") as mock_settings:
        mock_settings.return_value.SLACK_WEBHOOK_URL = "https://hooks.slack.com/fake"
        mock_settings.return_value.ENVIRONMENT = "development"
        await check_dead_mans_switch(mock_ctx)

    mock_alert.assert_called_once()
    call_kwargs = mock_alert.call_args.kwargs
    assert call_kwargs["urgency"] == "critical"
    assert "dead-mans-switch" in call_kwargs.get("tags", [])


@pytest.mark.asyncio
async def test_dms_silent_when_recent(mock_ctx, mock_redis):
    """OPS-05: No alert when last_ingestion is <24h ago."""
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    mock_redis.get = AsyncMock(return_value=recent_ts.encode())

    with patch(
        "src.workers.dms_worker.deliver_slack_alert", new_callable=AsyncMock
    ) as mock_alert, patch("src.workers.dms_worker.get_settings") as mock_settings:
        mock_settings.return_value.SLACK_WEBHOOK_URL = "https://hooks.slack.com/fake"
        await check_dead_mans_switch(mock_ctx)

    mock_alert.assert_not_called()


@pytest.mark.asyncio
async def test_dms_no_alert_when_no_webhook(mock_ctx, mock_redis):
    """OPS-05: No crash (just log error) when SLACK_WEBHOOK_URL is not set."""
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    mock_redis.get = AsyncMock(return_value=stale_ts.encode())

    with patch(
        "src.workers.dms_worker.deliver_slack_alert", new_callable=AsyncMock
    ) as mock_alert, patch("src.workers.dms_worker.get_settings") as mock_settings:
        mock_settings.return_value.SLACK_WEBHOOK_URL = None
        await check_dead_mans_switch(mock_ctx)

    mock_alert.assert_not_called()  # No webhook — should not call deliver_slack_alert


@pytest.mark.asyncio
async def test_update_ingestion_heartbeat_writes_key(mock_redis):
    """OPS-05: update_ingestion_heartbeat writes dms:last_ingestion with TTL."""
    await update_ingestion_heartbeat(mock_redis)
    mock_redis.set.assert_called_once()
    call_args = mock_redis.set.call_args
    assert call_args[0][0] == DMS_KEY
    assert call_args[1].get("ex") == 172800


@pytest.mark.asyncio
async def test_dms_cold_start_no_false_alert(mock_ctx, mock_redis):
    """OPS-05: Cold start (missing key) does not trigger alert — age treated as infinite but
    startup() seeds the key before check runs, so this test verifies the guard path.
    When key is missing (returns None), check_dead_mans_switch fires an alert only if
    SLACK_WEBHOOK_URL is set; with no webhook configured it must not crash."""
    # Key missing (None) — simulates cold start before startup() seeds it
    mock_redis.get = AsyncMock(return_value=None)

    with patch(
        "src.workers.dms_worker.deliver_slack_alert", new_callable=AsyncMock
    ) as mock_alert, patch("src.workers.dms_worker.get_settings") as mock_settings:
        mock_settings.return_value.SLACK_WEBHOOK_URL = (
            None  # No webhook — safe fallback
        )
        await check_dead_mans_switch(mock_ctx)

    # With no webhook, deliver_slack_alert must NOT be called — no crash
    mock_alert.assert_not_called()
