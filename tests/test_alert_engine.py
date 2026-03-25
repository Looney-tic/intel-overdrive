"""
Unit tests for alert engine and Slack delivery.

Requirements traced:
- ALERT-01: AlertRule model fields (keywords, cooldown_minutes, delivery_channels, is_active)
- ALERT-02: Keyword matching (case-insensitive, title/content/tags)
- ALERT-03: Redis cooldown (first call ready, second call blocked)
- ALERT-04: Slack delivery (Block Kit payload, success/failure)
- ALERT-05: Breaking change detection (keyword heuristic)
- ALERT-08: Urgency tiers (CRITICAL, IMPORTANT, INTERESTING)
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.services.alert_engine import (
    matches_keywords,
    compute_urgency,
    is_alert_on_cooldown,
    set_alert_cooldown,
    detect_breaking_change,
    UrgencyTier,
)
from src.services.slack_delivery import deliver_slack_alert
from src.models.models import AlertRule


# ---------------------------------------------------------------------------
# ALERT-01: AlertRule model fields
# ---------------------------------------------------------------------------


def test_alert_rule_fields():
    """ALERT-01: AlertRule model has required fields: keywords, cooldown_minutes, delivery_channels, is_active."""
    import uuid

    rule = AlertRule(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="Test Rule",
        keywords=["claude", "mcp"],
        delivery_channels={"slack_webhook": "https://hooks.slack.com/xxx"},
        cooldown_minutes=30,
        is_active=True,
    )
    assert rule.keywords == ["claude", "mcp"]
    assert rule.cooldown_minutes == 30
    assert rule.delivery_channels == {"slack_webhook": "https://hooks.slack.com/xxx"}
    assert rule.is_active is True


# ---------------------------------------------------------------------------
# ALERT-02: Keyword matching
# ---------------------------------------------------------------------------


def test_keyword_match_case_insensitive():
    """ALERT-02: Keyword match is case-insensitive."""
    assert matches_keywords(["Claude"], "New CLAUDE update", "", []) is True


def test_keyword_match_in_content():
    """ALERT-02: Keywords match against content field."""
    assert matches_keywords(["mcp"], "", "New MCP server released", []) is True


def test_keyword_match_in_tags():
    """ALERT-02: Keywords match against tags field."""
    assert matches_keywords(["breaking"], "", "", ["breaking"]) is True


def test_keyword_no_match():
    """ALERT-02: Non-matching keyword returns False."""
    assert matches_keywords(["nonexistent"], "Regular update", "", []) is False


def test_keyword_empty_list():
    """ALERT-02: Empty keyword list returns False."""
    assert matches_keywords([], "Any title", "", []) is False


# ---------------------------------------------------------------------------
# ALERT-03: Redis cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cooldown_first_call(redis_client):
    """ALERT-03: is_alert_on_cooldown returns False when no cooldown key exists."""
    result = await is_alert_on_cooldown(redis_client, "test-rule-1")
    assert result is False


@pytest.mark.asyncio
async def test_cooldown_after_set(redis_client):
    """ALERT-03: After set_alert_cooldown, is_alert_on_cooldown returns True."""
    rule_id = "test-rule-2"
    # Cooldown not yet set
    assert await is_alert_on_cooldown(redis_client, rule_id) is False
    # Simulate successful delivery: set cooldown
    await set_alert_cooldown(redis_client, rule_id, cooldown_minutes=5)
    # Now it should be on cooldown
    assert await is_alert_on_cooldown(redis_client, rule_id) is True


@pytest.mark.asyncio
async def test_cooldown_not_set_on_failed_delivery(redis_client):
    """ALERT-03: Cooldown is NOT set when delivery fails (check-only pattern).

    is_alert_on_cooldown no longer sets the key, so a failed delivery
    (where set_alert_cooldown is not called) leaves the rule ready to retry.
    """
    rule_id = "test-rule-3"
    # Simulate failed delivery: only check cooldown, never call set_alert_cooldown
    await is_alert_on_cooldown(redis_client, rule_id)
    # Rule should still be ready to fire
    result = await is_alert_on_cooldown(redis_client, rule_id)
    assert result is False


# ---------------------------------------------------------------------------
# ALERT-04: Slack delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slack_delivery_success():
    """ALERT-04: deliver_slack_alert returns True on 200 and sends Block Kit payload."""
    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("src.services.slack_delivery.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_response
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await deliver_slack_alert(
            webhook_url="https://hooks.slack.com/services/T00/B00/xxx",
            item_title="Test Alert",
            item_url="https://example.com/item",
            item_type="update",
            urgency="critical",
            tags=["claude", "mcp"],
        )

    assert result is True
    # Verify Block Kit payload shape
    call_kwargs = instance.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert "blocks" in payload
    assert len(payload["blocks"]) == 3  # header + section + context


@pytest.mark.asyncio
async def test_slack_delivery_failure():
    """ALERT-04: deliver_slack_alert returns False on non-200, no exception."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "internal error"

    with patch("src.services.slack_delivery.httpx.AsyncClient") as MockClient:
        instance = AsyncMock()
        instance.post.return_value = mock_response
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = instance

        result = await deliver_slack_alert(
            webhook_url="https://hooks.slack.com/services/T00/B00/xxx",
            item_title="Test Alert",
            item_url="https://example.com/item",
            item_type="update",
            urgency="important",
            tags=[],
        )

    assert result is False


# ---------------------------------------------------------------------------
# ALERT-05: Breaking change detection
# ---------------------------------------------------------------------------


def test_breaking_change_detected():
    """ALERT-05: 'Breaking change' phrase triggers detection."""
    assert detect_breaking_change("Breaking change in v2 API", "", []) is True


def test_deprecated_detected():
    """ALERT-05: 'deprecated' triggers detection."""
    assert detect_breaking_change("", "API deprecated as of v3", []) is True


def test_non_breaking_not_detected():
    """ALERT-05: Normal text does not trigger breaking change."""
    assert detect_breaking_change("Minor bug fix", "Small improvement", []) is False


# ---------------------------------------------------------------------------
# ALERT-08: Urgency tiers
# ---------------------------------------------------------------------------


def test_urgency_critical():
    """ALERT-08: is_breaking=True returns CRITICAL."""
    result = compute_urgency(primary_type="update", is_breaking=True, confidence=0.5)
    assert result == UrgencyTier.CRITICAL


def test_urgency_important():
    """ALERT-08: update + high confidence returns IMPORTANT."""
    result = compute_urgency(primary_type="update", is_breaking=False, confidence=0.9)
    assert result == UrgencyTier.IMPORTANT


def test_urgency_interesting():
    """ALERT-08: Default case returns INTERESTING."""
    result = compute_urgency(primary_type="skill", is_breaking=False, confidence=0.5)
    assert result == UrgencyTier.INTERESTING


# ---------------------------------------------------------------------------
# UX-07: Webhook delivery via alert engine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_delivers_to_webhook_url(session, redis_client, source_factory):
    """UX-07: Alert engine calls deliver_webhook_alert when webhook_url is configured."""
    import uuid as _uuid
    from src.services.alert_engine import check_and_deliver_alerts
    from src.models.models import AlertRule, User, IntelItem, AlertDelivery
    from sqlalchemy import select

    # Create user
    user = User(
        id=_uuid.uuid4(), email="webhook-test@example.com", is_active=True, profile={}
    )
    session.add(user)
    await session.flush()

    # Create source
    source = await source_factory(
        id="test:webhook-alert-source", name="Webhook Alert Source"
    )

    # Create intel item that matches the keyword
    item = IntelItem(
        id=_uuid.uuid4(),
        source_id=source.id,
        external_id="ext-webhook-alert-item",
        url="https://example.com/webhook-alert-item",
        title="Claude Code Webhook Alert",
        content="This is a claude update for webhook delivery",
        primary_type="update",
        tags=["claude"],
        status="processed",
        relevance_score=0.9,
        quality_score=0.9,
        confidence_score=0.9,
    )
    session.add(item)
    await session.flush()

    # Create AlertRule with webhook_url in delivery_channels
    rule = AlertRule(
        id=_uuid.uuid4(),
        user_id=user.id,
        name="Webhook Alert Rule",
        keywords=["claude"],
        delivery_channels={"webhook_url": "https://example.com/hook"},
        cooldown_minutes=60,
        is_active=True,
    )
    session.add(rule)
    await session.commit()

    items_payload = [
        {
            "id": str(item.id),
            "title": item.title,
            "content": item.content,
            "tags": item.tags,
            "primary_type": item.primary_type,
            "url": item.url,
            "confidence_score": item.confidence_score,
            "significance": "informational",
        }
    ]

    with patch(
        "src.services.webhook_delivery.deliver_webhook_alert",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_webhook:
        delivered = await check_and_deliver_alerts(
            session=session, redis_client=redis_client, items=items_payload
        )

    # Webhook delivery should have been called
    assert (
        mock_webhook.called
    ), "deliver_webhook_alert should be called when webhook_url is set"

    # Verify the payload contains the required keys
    call_kwargs = mock_webhook.call_args
    payload = call_kwargs.kwargs.get("payload") or call_kwargs[0][1]
    assert "item" in payload
    assert "urgency" in payload
    assert payload["item"]["title"] == item.title


@pytest.mark.asyncio
async def test_alert_webhook_channel_recorded(session, redis_client, source_factory):
    """UX-07: AlertDelivery row with channel='webhook' is created for webhook delivery."""
    import uuid as _uuid
    from src.services.alert_engine import check_and_deliver_alerts
    from src.models.models import AlertRule, User, IntelItem, AlertDelivery
    from sqlalchemy import select, text

    # Create user
    user = User(
        id=_uuid.uuid4(),
        email="webhook-channel@example.com",
        is_active=True,
        profile={},
    )
    session.add(user)
    await session.flush()

    # Create source
    source = await source_factory(
        id="test:webhook-channel-source", name="Webhook Channel Source"
    )

    # Create intel item
    item = IntelItem(
        id=_uuid.uuid4(),
        source_id=source.id,
        external_id="ext-webhook-channel-item",
        url="https://example.com/webhook-channel-item",
        title="MCP Tool Webhook Channel Test",
        content="This is an mcp tool update for channel tracking",
        primary_type="update",
        tags=["mcp"],
        status="processed",
        relevance_score=0.85,
        quality_score=0.85,
        confidence_score=0.85,
    )
    session.add(item)
    await session.flush()

    # Alert rule with ONLY webhook_url (no slack) so Slack branch is skipped
    rule = AlertRule(
        id=_uuid.uuid4(),
        user_id=user.id,
        name="Webhook Channel Rule",
        keywords=["mcp"],
        delivery_channels={"webhook_url": "https://example.com/channel-hook"},
        cooldown_minutes=60,
        is_active=True,
    )
    session.add(rule)
    await session.commit()

    items_payload = [
        {
            "id": str(item.id),
            "title": item.title,
            "content": item.content,
            "tags": item.tags,
            "primary_type": item.primary_type,
            "url": item.url,
            "confidence_score": item.confidence_score,
            "significance": "informational",
        }
    ]

    with patch(
        "src.services.webhook_delivery.deliver_webhook_alert",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await check_and_deliver_alerts(
            session=session, redis_client=redis_client, items=items_payload
        )

    # Verify an AlertDelivery row with channel='webhook' was created
    result = await session.execute(
        text(
            "SELECT channel FROM alert_deliveries "
            "WHERE intel_item_id = CAST(:item_id AS uuid)"
        ),
        {"item_id": str(item.id)},
    )
    channels = [row[0] for row in result.fetchall()]
    assert (
        "webhook" in channels
    ), f"Expected 'webhook' channel in AlertDelivery rows, got: {channels}"
