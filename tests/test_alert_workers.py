"""
Integration tests for alert_workers.check_alerts.

Tests the check_alerts worker end-to-end:
- Items matching a keyword rule trigger alert delivery creation
- Items already in alert_deliveries are not re-evaluated
- Items outside the 24h window are not evaluated
- No active rules = no alerts

Mocking strategy:
- Patch src.core.init_db.async_session_factory with the test session factory
- Patch src.services.slack_delivery to avoid real HTTP calls
- Use real DB for query logic verification
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import src.core.init_db as _init_db
from sqlalchemy import text

from src.models.models import AlertRule, IntelItem, Source


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_session_factory(session):
    """Return a callable that produces an async context manager yielding `session`."""

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


async def _create_source(session) -> Source:
    source = Source(
        id=f"test:{uuid.uuid4().hex[:12]}",
        name="Test Source",
        type="rss",
        url="https://example.com/feed.xml",
        tier="tier1",
        config={},
    )
    session.add(source)
    await session.commit()
    return source


async def _create_intel_item(
    session,
    source_id: str,
    status: str = "processed",
    title: str = "New MCP Tool Released",
    content: str = "A new MCP server tool has been released for Claude.",
    tags: list | None = None,
    primary_type: str = "tool",
    url: str = "https://example.com/item",
    confidence_score: float = 0.9,
    updated_at_offset_hours: int = 0,
    significance: str | None = None,
) -> IntelItem:
    item = IntelItem(
        source_id=source_id,
        external_id=str(uuid.uuid4()),
        url=f"{url}/{uuid.uuid4().hex}",
        title=title,
        content=content,
        tags=tags or [],
        primary_type=primary_type,
        status=status,
        confidence_score=confidence_score,
        significance=significance,
    )
    session.add(item)
    await session.flush()

    # Adjust updated_at if needed (for window tests)
    if updated_at_offset_hours != 0:
        new_updated_at = datetime.now(timezone.utc) - timedelta(
            hours=updated_at_offset_hours
        )
        await session.execute(
            text(
                "UPDATE intel_items SET updated_at = :ts WHERE id = CAST(:id AS uuid)"
            ),
            {"ts": new_updated_at, "id": str(item.id)},
        )

    await session.commit()
    return item


async def _create_alert_rule(
    session,
    keywords: list,
    slack_webhook: str = "https://hooks.slack.com/test",
    is_active: bool = True,
    cooldown_minutes: int = 60,
) -> AlertRule:
    user = await _get_or_create_user(session)
    rule = AlertRule(
        user_id=user.id,
        name="Test Rule",
        keywords=keywords,
        delivery_channels={"slack_webhook": slack_webhook},
        is_active=is_active,
        cooldown_minutes=cooldown_minutes,
    )
    session.add(rule)
    await session.commit()
    return rule


async def _get_or_create_user(session):
    from src.models.models import User

    user = User(
        email=f"test_{uuid.uuid4().hex[:8]}@example.com", is_active=True, profile={}
    )
    session.add(user)
    await session.flush()
    return user


async def _create_alert_delivery(session, rule_id: uuid.UUID, item_id: uuid.UUID):
    """Insert a pre-existing alert delivery to simulate already-alerted item."""
    delivery_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO alert_deliveries
                (id, alert_rule_id, intel_item_id, urgency, status, channel,
                 created_at, updated_at)
            VALUES
                (CAST(:id AS uuid), CAST(:rule_id AS uuid), CAST(:item_id AS uuid),
                 'interesting', 'sent', 'slack', NOW(), NOW())
            """
        ),
        {
            "id": str(delivery_id),
            "rule_id": str(rule_id),
            "item_id": str(item_id),
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_alerts_keyword_match_creates_delivery(session, redis_client):
    """Items matching a keyword rule trigger alert delivery creation in the DB."""
    source = await _create_source(session)
    await _create_intel_item(
        session,
        source.id,
        title="MCP Server New Release",
        content="A new MCP server is available for Claude code assistants.",
        tags=["mcp", "tool"],
        status="processed",
    )
    rule = await _create_alert_rule(session, keywords=["mcp"])

    # Mark first-run as done so check_alerts doesn't skip
    await redis_client.set("alert:first_run_done", "1")

    from src.workers.alert_workers import check_alerts

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch(
            "src.services.alert_engine.deliver_slack_alert",
            new_callable=AsyncMock,
            return_value=True,
        ):
            await check_alerts({"redis": redis_client})

    # Verify an alert_delivery row was created
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM alert_deliveries WHERE alert_rule_id = CAST(:rule_id AS uuid)"
        ),
        {"rule_id": str(rule.id)},
    )
    count = result.scalar()
    assert count >= 1, "Expected at least one alert_delivery row for matching item"


@pytest.mark.asyncio
async def test_check_alerts_already_in_deliveries_not_re_evaluated(
    session, redis_client
):
    """Items already in alert_deliveries are excluded from re-evaluation (NOT EXISTS guard)."""
    source = await _create_source(session)
    item = await _create_intel_item(
        session,
        source.id,
        title="MCP Tool Update",
        content="An update to the MCP tool was released.",
        tags=["mcp"],
        status="processed",
    )
    rule = await _create_alert_rule(session, keywords=["mcp"])

    # Pre-create an existing delivery for this item
    await _create_alert_delivery(session, rule.id, item.id)

    # Mark first-run as done
    await redis_client.set("alert:first_run_done", "1")

    from src.workers.alert_workers import check_alerts

    mock_deliver = AsyncMock(return_value=True)
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.services.alert_engine.deliver_slack_alert", mock_deliver):
            await check_alerts({"redis": redis_client})

    # The item had an existing delivery, so it should NOT have been fetched by check_alerts.
    # Total delivery count should remain 1 (the pre-existing one).
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM alert_deliveries WHERE intel_item_id = CAST(:item_id AS uuid)"
        ),
        {"item_id": str(item.id)},
    )
    count = result.scalar()
    assert count == 1, "Item already in alert_deliveries must not be re-alerted"


@pytest.mark.asyncio
async def test_check_alerts_outside_24h_window_not_evaluated(session, redis_client):
    """Items with updated_at older than 24h are not fetched by check_alerts."""
    source = await _create_source(session)
    await _create_intel_item(
        session,
        source.id,
        title="Old MCP Item",
        content="This item is older than 24 hours.",
        tags=["mcp"],
        status="processed",
        updated_at_offset_hours=25,  # 25 hours ago — outside window
    )
    rule = await _create_alert_rule(session, keywords=["mcp"])

    await redis_client.set("alert:first_run_done", "1")

    from src.workers.alert_workers import check_alerts

    mock_deliver = AsyncMock(return_value=True)
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.services.alert_engine.deliver_slack_alert", mock_deliver):
            await check_alerts({"redis": redis_client})

    # No deliveries should have been created — item is outside the 24h window
    result = await session.execute(
        text(
            "SELECT COUNT(*) FROM alert_deliveries WHERE alert_rule_id = CAST(:rule_id AS uuid)"
        ),
        {"rule_id": str(rule.id)},
    )
    count = result.scalar()
    assert count == 0, "Items outside 24h window must not trigger alert deliveries"


@pytest.mark.asyncio
async def test_check_alerts_no_active_rules_no_alerts(session, redis_client):
    """When no active alert rules exist, no deliveries are created."""
    source = await _create_source(session)
    await _create_intel_item(
        session,
        source.id,
        title="MCP Server Update",
        content="A new mcp update is here.",
        tags=["mcp"],
        status="processed",
    )
    # Create an inactive rule — should not match
    await _create_alert_rule(session, keywords=["mcp"], is_active=False)

    await redis_client.set("alert:first_run_done", "1")

    from src.workers.alert_workers import check_alerts

    mock_deliver = AsyncMock(return_value=True)
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.services.alert_engine.deliver_slack_alert", mock_deliver):
            await check_alerts({"redis": redis_client})

    result = await session.execute(text("SELECT COUNT(*) FROM alert_deliveries"))
    count = result.scalar()
    assert count == 0, "Inactive rules must not trigger any alert deliveries"


@pytest.mark.asyncio
async def test_check_alerts_first_run_skips(session, redis_client):
    """First-run backfill protection: check_alerts skips processing when marker is absent."""
    source = await _create_source(session)
    await _create_intel_item(
        session,
        source.id,
        title="Claude MCP Release",
        content="New MCP tool from Anthropic.",
        tags=["claude", "mcp"],
        status="processed",
    )
    await _create_alert_rule(session, keywords=["claude"])

    # Do NOT set the first_run_done marker — simulates first ever run

    from src.workers.alert_workers import check_alerts

    mock_deliver = AsyncMock(return_value=True)
    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch("src.services.alert_engine.deliver_slack_alert", mock_deliver):
            await check_alerts({"redis": redis_client})

    # First run should set the marker and skip — no deliveries created
    result = await session.execute(text("SELECT COUNT(*) FROM alert_deliveries"))
    count = result.scalar()
    assert count == 0, "First-run backfill protection must skip all items"

    # Verify the marker was set
    marker = await redis_client.get("alert:first_run_done")
    assert marker is not None, "First-run marker must be set after first run"


@pytest.mark.asyncio
async def test_significance_included_in_alert_items(session, redis_client):
    """Alert worker must include significance in items dict for urgency routing."""
    source = await _create_source(session)
    item = await _create_intel_item(
        session,
        source.id,
        title="Breaking API Change in Claude SDK",
        content="The Claude SDK v3 removes the completions endpoint.",
        tags=["claude", "sdk"],
        status="processed",
        significance="major",
    )

    # Mark first-run as done
    await redis_client.set("alert:first_run_done", "1")

    # Create a rule that matches the item
    rule = await _create_alert_rule(session, keywords=["claude"])

    from src.workers.alert_workers import check_alerts

    captured_items = []

    async def _capture_alerts(session, redis_client, items):
        captured_items.extend(items)
        return 0

    with patch.object(_init_db, "async_session_factory", make_session_factory(session)):
        with patch(
            "src.workers.alert_workers.check_and_deliver_alerts",
            side_effect=_capture_alerts,
        ):
            await check_alerts({"redis": redis_client})

    assert len(captured_items) >= 1, "Expected at least one item to be checked"
    assert (
        "significance" in captured_items[0]
    ), "Items dict must include 'significance' key"
    assert (
        captured_items[0]["significance"] == "major"
    ), "Significance value must match the DB value"


def test_classification_prompt_no_subtypes():
    """Classification prompt must not reference non-existent sub-types."""
    from src.workers.pipeline_workers import CLASSIFICATION_SYSTEM_PROMPT

    assert "sub-type" not in CLASSIFICATION_SYSTEM_PROMPT
    assert "'outage'" not in CLASSIFICATION_SYSTEM_PROMPT
    assert "'deprecation'" not in CLASSIFICATION_SYSTEM_PROMPT
    # 'breaking-change' should not appear as a sub-type instruction in the prompt
    # (it may appear in code comments elsewhere, but not in the prompt text)
    prompt_lines = CLASSIFICATION_SYSTEM_PROMPT.split("\n")
    for line in prompt_lines:
        assert (
            "sub-type" not in line.lower()
        ), f"Prompt line references sub-type: {line}"


def test_classification_prompt_strict_breaking_guidance():
    """Classification prompt must contain strict NEVER guidance for breaking."""
    from src.workers.pipeline_workers import CLASSIFICATION_SYSTEM_PROMPT

    assert (
        "NEVER" in CLASSIFICATION_SYSTEM_PROMPT
    ), "Prompt must use NEVER for non-breaking items"
    assert (
        "Service outages" in CLASSIFICATION_SYSTEM_PROMPT
    ), "Prompt must explicitly mention service outages as non-breaking"
