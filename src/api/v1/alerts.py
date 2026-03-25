"""Alert management endpoints: rule CRUD, Slack webhook config, status."""

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from src.models.models import AlertRule, AlertDelivery, APIKey
from src.api.deps import get_session, require_api_key, get_redis
from src.core.logger import get_logger
from src.api.schemas import (
    AlertRuleCreate,
    AlertRuleResponse,
    AlertRuleStatusResponse,
    AlertStatusResponse,
    SlackWebhookRequest,
    WebhookUrlRequest,
)
from src.api.limiter import limiter

alerts_router = APIRouter(prefix="/alerts", tags=["alerts"])
logger = get_logger(__name__)


def _validate_webhook_url(url: str) -> str:
    """Validate webhook URL format (SSRF protection — format checks only).

    Checks HTTPS scheme, valid hostname, no IP literals, no path traversal.
    DNS resolution is intentionally NOT done here to prevent TOCTOU attacks
    (DNS rebinding). Use _resolve_and_validate_webhook_ip() at delivery time.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Webhook URL must use HTTPS")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid webhook URL")
    # Block direct IP literals — force DNS resolution at delivery time
    try:
        ipaddress.ip_address(hostname)
        raise ValueError("Webhook URL must use a hostname, not an IP address")
    except ValueError as e:
        if "not an IP address" in str(e):
            raise
        pass  # Not an IP literal — good
    return url


def _resolve_and_validate_webhook_ip(url: str) -> str:
    """Resolve DNS and validate resolved IPs are not private/internal (P1-9).

    Call this at delivery time (not validation time) to prevent DNS rebinding
    TOCTOU attacks. Raises ValueError if any resolved IP is private, loopback,
    link-local, or reserved.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid webhook URL — no hostname")
    try:
        addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {hostname}")
    for addr_info in addrs:
        ip = ipaddress.ip_address(addr_info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(
                "Webhook URL must not target private/internal IP addresses"
            )
    return url


@alerts_router.post("/rules", response_model=AlertRuleResponse, status_code=201)
@limiter.limit("30/minute")
async def create_alert_rule(
    request: Request,
    body: AlertRuleCreate,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """Create a new alert rule for the authenticated user."""
    delivery_channels: dict = {}
    if body.significance_trigger:
        delivery_channels["significance_trigger"] = body.significance_trigger

    rule = AlertRule(
        user_id=api_key.user_id,
        name=body.name,
        keywords=body.keywords,
        cooldown_minutes=body.cooldown_minutes,
        delivery_channels=delivery_channels,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    return JSONResponse(
        status_code=201,
        content=AlertRuleResponse.model_validate(rule).model_dump(mode="json"),
    )


@alerts_router.delete("/rules/{rule_id}", status_code=204)
@limiter.limit("30/minute")
async def delete_alert_rule(
    request: Request,
    rule_id: str,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """Delete an alert rule. Validates ownership."""
    import uuid as _uuid

    try:
        parsed_id = _uuid.UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid rule ID format")

    result = await session.execute(
        select(AlertRule).where(
            AlertRule.id == parsed_id,
            AlertRule.user_id == api_key.user_id,
        )
    )
    rule = result.scalar_one_or_none()

    if rule is None:
        raise HTTPException(status_code=404, detail="Alert rule not found")

    await session.execute(
        delete(AlertRule).where(
            AlertRule.id == parsed_id,
            AlertRule.user_id == api_key.user_id,
        )
    )
    await session.commit()
    return Response(status_code=204)


@alerts_router.post("/slack-webhook")
@limiter.limit("30/minute")
async def set_slack_webhook(
    request: Request,
    body: SlackWebhookRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """Set Slack webhook for all of the user's active alert rules."""
    try:
        _validate_webhook_url(body.webhook_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = await session.execute(
        select(AlertRule).where(
            AlertRule.user_id == api_key.user_id,
            AlertRule.is_active == True,  # noqa: E712
        )
    )
    rules = result.scalars().all()

    if not rules:
        raise HTTPException(
            status_code=404,
            detail="No alert rules found. Create a rule first with POST /alerts/rules",
        )

    count = 0
    for rule in rules:
        channels = dict(rule.delivery_channels) if rule.delivery_channels else {}
        channels["slack_webhook"] = body.webhook_url
        rule.delivery_channels = channels
        count += 1

    await session.commit()

    logger.info(
        "WEBHOOK_SET",
        user_id=str(api_key.user_id),
        rule_count=count,
    )

    return JSONResponse(
        content={"message": f"Slack webhook configured for {count} rules"}
    )


@alerts_router.post("/webhook")
@limiter.limit("10/minute")
async def set_webhook_url(
    request: Request,
    body: WebhookUrlRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """Set generic webhook URL delivery channel for all active alert rules."""
    try:
        _validate_webhook_url(body.webhook_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result = await session.execute(
        select(AlertRule).where(
            AlertRule.user_id == api_key.user_id,
            AlertRule.is_active == True,  # noqa: E712
        )
    )
    rules = result.scalars().all()

    if not rules:
        raise HTTPException(
            status_code=404,
            detail="No active alert rules. Create a rule first with POST /alerts/rules",
        )

    count = 0
    for rule in rules:
        rule.delivery_channels = {
            **dict(rule.delivery_channels or {}),
            "webhook_url": body.webhook_url,
        }
        count += 1

    await session.commit()

    logger.info(
        "WEBHOOK_URL_SET",
        user_id=str(api_key.user_id),
        rule_count=count,
    )

    return JSONResponse({"status": "ok", "rules_updated": count})


@alerts_router.get("/status", response_model=AlertStatusResponse)
@limiter.limit("30/minute")
async def get_alert_status(
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
    redis_client: aioredis.Redis = Depends(get_redis),
):
    """Get alert status: active rules with last_fired_at and cooldown state."""
    result = await session.execute(
        select(AlertRule).where(
            AlertRule.user_id == api_key.user_id,
            AlertRule.is_active == True,  # noqa: E712
        )
    )
    rules = result.scalars().all()

    rule_statuses = []
    for rule in rules:
        # Get last_fired_at from alert_deliveries
        delivery_result = await session.execute(
            select(func.max(AlertDelivery.delivered_at)).where(
                AlertDelivery.alert_rule_id == rule.id
            )
        )
        last_fired_at = delivery_result.scalar_one_or_none()

        # Check Redis cooldown
        cooldown_key = f"alert:cooldown:{rule.id}"
        is_on_cooldown = await redis_client.exists(cooldown_key) > 0

        rule_data = AlertRuleStatusResponse.model_validate(rule)
        rule_data.last_fired_at = last_fired_at
        rule_data.is_on_cooldown = is_on_cooldown
        rule_statuses.append(rule_data)

    response_obj = AlertStatusResponse(
        rules=rule_statuses,
        message=f"{len(rule_statuses)} active alert rules",
    )
    return JSONResponse(content=response_obj.model_dump(mode="json"))
