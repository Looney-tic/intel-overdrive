"""
Integration tests for alerts API endpoints and quality score in /info.

Requirements traced:
- ALERT-01: Alert rule creation via API (POST /v1/alerts/rules)
- ALERT-06: Slack webhook configuration (POST /v1/alerts/slack-webhook)
- ALERT-07: Alert status endpoint (GET /v1/alerts/status)
- QUAL-04: quality_score_details in GET /v1/info/{id}
"""

import pytest
import uuid
from datetime import datetime, timezone

from src.models.models import IntelItem, Source, AlertRule


# ---------------------------------------------------------------------------
# ALERT-01: Alert rule CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_alert_rule(client, api_key_header):
    """ALERT-01: POST /v1/alerts/rules creates a rule with correct fields."""
    payload = {
        "name": "Claude Updates",
        "keywords": ["claude", "claude-code"],
        "cooldown_minutes": 30,
    }
    response = await client.post(
        "/v1/alerts/rules",
        json=payload,
        headers=api_key_header["headers"],
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Claude Updates"
    assert data["keywords"] == ["claude", "claude-code"]
    assert data["cooldown_minutes"] == 30
    assert data["is_active"] is True
    assert "id" in data


@pytest.mark.asyncio
async def test_delete_alert_rule(client, api_key_header, session):
    """ALERT-01: DELETE /v1/alerts/rules/{id} removes rule owned by user."""
    # Create a rule first
    rule = AlertRule(
        user_id=api_key_header["user_id"],
        name="Delete Me",
        keywords=["test"],
        cooldown_minutes=60,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    rule_id = str(rule.id)

    # Delete it
    response = await client.delete(
        f"/v1/alerts/rules/{rule_id}",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# ALERT-06: Slack webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_slack_webhook(client, api_key_header, session):
    """ALERT-06: POST /v1/alerts/slack-webhook configures webhook for user's rules."""
    # Create a rule first so webhook has something to configure
    rule = AlertRule(
        user_id=api_key_header["user_id"],
        name="Webhook Test Rule",
        keywords=["test"],
        cooldown_minutes=60,
    )
    session.add(rule)
    await session.commit()

    response = await client.post(
        "/v1/alerts/slack-webhook",
        json={"webhook_url": "https://hooks.slack.com/services/T00/B00/xxx"},
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "configured" in data["message"].lower() or "1" in data["message"]


# ---------------------------------------------------------------------------
# ALERT-07: Alert status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alerts_status(client, api_key_header, session):
    """ALERT-07: GET /v1/alerts/status returns rules list."""
    # Create a rule so status has content
    rule = AlertRule(
        user_id=api_key_header["user_id"],
        name="Status Test Rule",
        keywords=["mcp"],
        cooldown_minutes=60,
    )
    session.add(rule)
    await session.commit()

    response = await client.get(
        "/v1/alerts/status",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "rules" in data
    assert len(data["rules"]) >= 1
    assert data["rules"][0]["name"] == "Status Test Rule"


# ---------------------------------------------------------------------------
# QUAL-04: quality_score_details in /info
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quality_score_in_info(client, api_key_header, session, source_factory):
    """QUAL-04: GET /v1/info/{id} includes quality_score_details field."""
    source = await source_factory(id="test:qual-info-source")
    item_id = uuid.uuid4()
    item = IntelItem(
        id=item_id,
        source_id=source.id,
        external_id="ext-qual",
        url="https://github.com/test/quality-item",
        title="Quality Test Item",
        content="Test content",
        primary_type="tool",
        tags=["test"],
        status="processed",
        relevance_score=0.85,
        quality_score=0.75,
        quality_score_details={
            "maintenance": 0.9,
            "security": 1.0,
            "compatibility": 0.8,
            "is_stale": False,
        },
    )
    session.add(item)
    await session.commit()

    response = await client.get(
        f"/v1/info/{item_id}",
        headers=api_key_header["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "quality_score_details" in data
    assert data["quality_score_details"]["maintenance"] == 0.9
    assert data["quality_score_details"]["is_stale"] is False
