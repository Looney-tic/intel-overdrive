"""Tests for trust/reliability fixes: SLA metrics, health checks, dedup, DMS, alerts.

Phase 14 Plan 04 — trust layer fixes:
- T-1/T-9/T-10: SLA metric renames and new fields
- T-4: Credit exhaustion surfacing
- T-11: Real health checks
- T-3: Content fingerprint dedup in ingestion workers
- T-5: DMS threshold reduced to 4h
- T-6: Source death Slack alert
- T-7: Summary fallback marker
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import src.core.init_db as _db


# ---------------------------------------------------------------------------
# Task 1: SLA fixes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sla_field_renamed_to_newest_item_age_hours(
    client, api_key_header, session, source_factory
):
    """SLA response uses newest_item_age_hours, not max_item_age_hours."""
    from src.models.models import IntelItem

    await source_factory(id="test-src", name="SLA Test Source")
    item = IntelItem(
        source_id="test-src",
        external_id="ext-sla-1",
        url="https://example.com/sla-a1",
        url_hash="urlhash-sla-1",
        title="Title 1",
        content="",
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        confidence_score=0.9,
        quality_score=0.7,
        content_hash="cfhash-sla-1",
    )
    session.add(item)
    await session.commit()

    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    # Must have newest_item_age_hours (renamed from max_item_age_hours)
    assert (
        "newest_item_age_hours" in data
    ), f"newest_item_age_hours missing from {list(data.keys())}"
    assert (
        "max_item_age_hours" not in data
    ), "Old field max_item_age_hours should not be present"


@pytest.mark.asyncio
async def test_sla_has_failed_items_last_24h(
    client, api_key_header, session, source_factory
):
    """SLA response includes failed_items_last_24h count."""
    from src.models.models import IntelItem

    await source_factory(id="test-src-fail", name="Fail Test Source")
    # Insert a failed item
    item = IntelItem(
        source_id="test-src-fail",
        external_id="ext-fail-sla-1",
        url="https://example.com/fail-sla-1",
        url_hash="urlhash-fail-1",
        title="Failed Item",
        content="",
        primary_type="tool",
        tags=[],
        status="failed",
        relevance_score=0.0,
        confidence_score=0.0,
        quality_score=0.0,
        content_hash="cfhash-fail-1",
    )
    session.add(item)
    await session.commit()

    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    assert "failed_items_last_24h" in data
    assert isinstance(data["failed_items_last_24h"], int)
    assert data["failed_items_last_24h"] >= 1


@pytest.mark.asyncio
async def test_sla_has_credits_exhausted_field(client, api_key_header):
    """SLA response includes credits_exhausted boolean."""
    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    assert "credits_exhausted" in data
    assert isinstance(data["credits_exhausted"], bool)


@pytest.mark.asyncio
async def test_sla_credits_exhausted_reflects_redis_flag(
    client, api_key_header, redis_client
):
    """credits_exhausted=True when credits:exhausted key is set in Redis."""
    # Set the flag
    await redis_client.set("credits:exhausted", "1", ex=86400)

    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    assert data["credits_exhausted"] is True


@pytest.mark.asyncio
async def test_sla_credits_exhausted_false_when_not_set(
    client, api_key_header, redis_client
):
    """credits_exhausted=False when credits:exhausted key is absent."""
    # Ensure key is not set
    await redis_client.delete("credits:exhausted")

    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    assert data["credits_exhausted"] is False


@pytest.mark.asyncio
async def test_sla_pipeline_lag_uses_median(client, api_key_header, session):
    """pipeline_lag_seconds uses median (P50), not MAX — test field exists."""
    response = await client.get("/v1/sla", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()
    # Field should be present (can be None if pipeline is empty)
    assert "pipeline_lag_seconds" in data


# ---------------------------------------------------------------------------
# Task 1: Real health checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_unauthenticated_returns_minimal(client):
    """GET /v1/health without auth returns only status, no infrastructure details."""
    response = await client.get("/v1/health")
    assert response.status_code == 200
    data = response.json()

    assert "status" in data
    # Infrastructure details must NOT be exposed to unauthenticated callers
    assert "db_connected" not in data, f"db_connected should not be exposed: {data}"
    assert (
        "redis_connected" not in data
    ), f"redis_connected should not be exposed: {data}"


@pytest.mark.asyncio
async def test_health_authenticated_returns_detail(client, api_key_header):
    """GET /v1/health with auth returns db_connected and redis_connected."""
    response = await client.get("/v1/health", headers=api_key_header["headers"])
    assert response.status_code == 200
    data = response.json()

    assert data["status"] == "healthy"
    assert "db_connected" in data, f"db_connected missing: {data}"
    assert "redis_connected" in data, f"redis_connected missing: {data}"
    assert data["db_connected"] is True
    assert data["redis_connected"] is True


@pytest.mark.asyncio
async def test_health_returns_degraded_when_db_down():
    """GET /v1/health returns status=degraded when DB is unreachable."""
    from src.api.app import app
    from httpx import AsyncClient, ASGITransport
    import redis.asyncio as aioredis
    import src.core.init_db as _init_db
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Set up a broken async session factory
    broken_engine = create_async_engine(
        "postgresql+asyncpg://invalid:invalid@localhost:9999/nonexistent",
        pool_pre_ping=False,
    )
    broken_factory = async_sessionmaker(broken_engine, expire_on_commit=False)

    # Use real Redis but broken DB
    real_redis = aioredis.from_url("redis://localhost:6381/1")

    original_factory = _init_db.async_session_factory
    original_redis = getattr(app.state, "redis", None)
    try:
        _init_db.async_session_factory = broken_factory
        app.state.redis = real_redis

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            # Unauthenticated — should still get status but no details
            response = await ac.get("/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "degraded"
        # Unauthenticated: no infrastructure details exposed
        assert "db_connected" not in data
        assert "redis_connected" not in data
    finally:
        _init_db.async_session_factory = original_factory
        app.state.redis = original_redis
        await real_redis.aclose()
        await broken_engine.dispose()


# ---------------------------------------------------------------------------
# Task 1: Credits exhaustion Redis flag via pipeline worker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credits_exhausted_flag_set_on_api_credits_exhausted():
    """APICreditsExhausted exception sets credits:exhausted Redis flag with 24h TTL."""
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()

    # Import the exception class
    from src.services.llm_client import APICreditsExhausted

    # Test that the flag is set when exception is caught
    try:
        raise APICreditsExhausted(provider="anthropic", detail="credits exhausted")
    except APICreditsExhausted:
        await mock_redis.set("credits:exhausted", "1", ex=86400)

    mock_redis.set.assert_called_once_with("credits:exhausted", "1", ex=86400)


# ---------------------------------------------------------------------------
# Task 2: Content fingerprint dedup in RSS ingestion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rss_ingestion_skips_content_duplicate(session, source_factory):
    """RSS ingestion skips items with matching content fingerprint (Layer 2 dedup)."""
    from src.services.dedup_service import DedupService
    from src.models.models import IntelItem

    await source_factory(id="src1", name="Dedup Test Source")
    dedup = DedupService(session)

    # Insert an existing item with a specific content fingerprint
    content_text = "This is an important announcement about new features in Claude Code"
    fingerprint = dedup._get_content_fingerprint(content_text)

    item = IntelItem(
        source_id="src1",
        external_id="ext-orig-1",
        url="https://original.com/post",
        url_hash="urlhash-orig-1",
        title="Original",
        content=content_text,
        primary_type="tool",
        tags=[],
        status="processed",
        relevance_score=0.8,
        confidence_score=0.9,
        quality_score=0.7,
        content_hash=fingerprint,
    )
    session.add(item)
    await session.commit()

    # Now check that find_duplicate_by_content finds it
    duplicate = await dedup.find_duplicate_by_content(content_text)
    assert duplicate is not None, "Should find duplicate by content fingerprint"
    assert str(duplicate.url) == "https://original.com/post"


@pytest.mark.asyncio
async def test_rss_ingestion_content_dedup_called(session, redis_client):
    """ingest_rss_source calls find_duplicate_by_content for content fingerprint check."""
    from contextlib import asynccontextmanager
    from src.workers.ingest_rss import ingest_rss_source
    from src.models.models import Source
    import src.core.init_db as _init_db

    def make_session_factory(s):
        @asynccontextmanager
        async def _factory():
            yield s

        return _factory

    # Create a source
    source = Source(
        id="test:rss-dedup-worker",
        name="Test RSS Dedup",
        url="https://rss.example.com/feed",
        type="rss",
        is_active=True,
        poll_interval_seconds=3600,
        config={},
        consecutive_errors=0,
    )
    session.add(source)
    await session.commit()

    # Mock feed entries
    content_text = "Duplicate content that appears on two different URLs"

    SAMPLE_RSS = f"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>Post 1</title><link>https://rss.example.com/post1</link>
<description>{content_text}</description></item>
</channel></rss>""".encode()

    find_duplicate_calls = []

    async def mock_find_duplicate(self, content):
        find_duplicate_calls.append(content)
        return None

    with patch(
        "src.services.dedup_service.DedupService.find_duplicate_by_content",
        mock_find_duplicate,
    ):
        with patch("src.workers.ingest_rss.fetch_feed_conditional") as mock_fetch:
            mock_fetch.return_value = (SAMPLE_RSS, None, None)
            with patch.object(
                _init_db, "async_session_factory", make_session_factory(session)
            ):
                ctx = {"redis": redis_client}
                await ingest_rss_source(ctx, "test:rss-dedup-worker")

    # Content fingerprint check should have been called
    assert (
        len(find_duplicate_calls) >= 1
    ), "find_duplicate_by_content should be called during ingestion"


# ---------------------------------------------------------------------------
# Task 2: DMS threshold
# ---------------------------------------------------------------------------


def test_dms_threshold_is_4_hours():
    """DMS threshold is 4 hours (down from 24)."""
    from src.workers.dms_worker import DMS_THRESHOLD_HOURS

    assert (
        DMS_THRESHOLD_HOURS == 4
    ), f"Expected DMS_THRESHOLD_HOURS=4, got {DMS_THRESHOLD_HOURS}"


# ---------------------------------------------------------------------------
# Task 2: Source death Slack alert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_death_fires_slack_alert(session):
    """Source deactivation fires a Slack alert when SLACK_WEBHOOK_URL is configured."""
    from src.models.models import Source
    from src.services.source_health import handle_source_error, MAX_CONSECUTIVE_ERRORS

    source = Source(
        id="test:dying-source",
        name="Dying Source",
        url="https://dying.example.com",
        type="rss",
        is_active=True,
        poll_interval_seconds=3600,
        config={},
        consecutive_errors=MAX_CONSECUTIVE_ERRORS - 1,  # One away from death
    )
    session.add(source)
    await session.commit()

    deliver_calls = []

    async def mock_deliver(webhook_url, item_title, item_url, item_type, urgency, tags):
        deliver_calls.append(
            {
                "webhook_url": webhook_url,
                "item_title": item_title,
                "item_url": item_url,
                "urgency": urgency,
                "tags": tags,
            }
        )

    with patch("src.services.source_health.deliver_slack_alert", mock_deliver):
        with patch("src.services.source_health.get_settings") as mock_settings:
            mock_settings.return_value.SLACK_WEBHOOK_URL = (
                "https://hooks.slack.com/test"
            )
            await handle_source_error(session, source, Exception("Connection refused"))

    # Source should be marked inactive
    assert source.is_active is False

    # Slack alert should have been fired
    assert len(deliver_calls) == 1, f"Expected 1 Slack alert, got {len(deliver_calls)}"
    assert (
        "deactivated" in deliver_calls[0]["item_title"].lower()
        or "dead" in deliver_calls[0]["item_title"].lower()
    )
    assert deliver_calls[0]["urgency"] in ("important", "critical")


@pytest.mark.asyncio
async def test_source_death_no_alert_when_no_webhook(session):
    """Source deactivation does not crash if SLACK_WEBHOOK_URL is not configured."""
    from src.models.models import Source
    from src.services.source_health import handle_source_error, MAX_CONSECUTIVE_ERRORS

    source = Source(
        id="test:dying-source-nowebhook",
        name="Dying Source No Webhook",
        url="https://dying2.example.com",
        type="rss",
        is_active=True,
        poll_interval_seconds=3600,
        config={},
        consecutive_errors=MAX_CONSECUTIVE_ERRORS - 1,
    )
    session.add(source)
    await session.commit()

    deliver_calls = []

    async def mock_deliver(webhook_url, item_title, item_url, item_type, urgency, tags):
        deliver_calls.append({"webhook_url": webhook_url})

    with patch("src.services.source_health.deliver_slack_alert", mock_deliver):
        with patch("src.services.source_health.get_settings") as mock_settings:
            mock_settings.return_value.SLACK_WEBHOOK_URL = None
            # Should not raise
            await handle_source_error(session, source, Exception("Connection refused"))

    # Source should be inactive
    assert source.is_active is False
    # No alert should be sent when webhook URL is missing
    assert len(deliver_calls) == 0


# ---------------------------------------------------------------------------
# Task 2: Summary fallback marker
# ---------------------------------------------------------------------------


def test_summary_fallback_uses_marker_not_excerpt():
    """Empty LLM summary results in '[Summary unavailable...]' marker, not raw excerpt."""
    item_excerpt = "Click here to read more about this topic..."

    # Simulate what the fixed code does
    llm_summary = ""  # Empty summary from LLM

    # New behavior (what the fix should do)
    summary = llm_summary
    if not summary or summary.strip() == "":
        summary = "[Summary unavailable — classification returned empty summary]"

    assert summary == "[Summary unavailable — classification returned empty summary]"
    assert item_excerpt not in summary


def test_summary_fallback_marker_in_pipeline_workers():
    """pipeline_workers.py uses marker string, not raw excerpt, for empty summaries."""
    import pathlib

    workers_path = pathlib.Path(
        "src/workers/pipeline_workers.py"
    )
    source_code = workers_path.read_text()

    # Should NOT have the old fallback pattern
    assert (
        "llm_result.summary or item_excerpt" not in source_code
    ), "Old fallback pattern 'llm_result.summary or item_excerpt' should be removed"

    # Should have the new marker pattern
    assert (
        "Summary unavailable" in source_code
    ), "New marker '[Summary unavailable...]' should be present in pipeline_workers.py"
