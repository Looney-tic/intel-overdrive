"""Alert engine: keyword matching, urgency tiers, cooldown, and delivery orchestration.

Matches newly-processed items against active alert rules. Uses Redis SET NX
for per-rule cooldown to prevent duplicate alerts within the cooldown window.
Delivers alerts via configured channels (Slack) and tracks deliveries in the
AlertDelivery outbox table (pending -> sent / failed state machine).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logger import get_logger
from src.services.slack_delivery import deliver_slack_alert
from src.api.v1.alerts import _validate_webhook_url

logger = get_logger(__name__)

BREAKING_KEYWORDS = [
    "breaking change",
    "deprecated",
    "removed",
    "migration required",
    "incompatible",
    "no longer supported",
    "end of life",
    "eol",
]


def detect_breaking_change(title: str, content: str, tags: list[str]) -> bool:
    """Keyword heuristic for breaking change detection. No LLM cost.

    Only meaningful for items with primary_type == 'update'.
    """
    searchable = f"{title} {content} {' '.join(tags)}".lower()
    return any(kw in searchable for kw in BREAKING_KEYWORDS)


class UrgencyTier(str, Enum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    INTERESTING = "interesting"


def matches_keywords(
    keywords: list[str], title: str, content: str, tags: list[str]
) -> bool:
    """Case-insensitive substring match across title + content + tags.

    Returns True if any keyword appears as a substring in the concatenated
    searchable string.
    """
    searchable = f"{title} {content} {' '.join(tags)}".lower()
    return any(kw.lower() in searchable for kw in keywords)


def compute_urgency(
    primary_type: str, is_breaking: bool, confidence: float
) -> UrgencyTier:
    """Compute urgency tier from item signals.

    - Breaking items -> CRITICAL
    - High-confidence updates -> IMPORTANT
    - Everything else -> INTERESTING
    """
    if is_breaking:
        return UrgencyTier.CRITICAL
    if primary_type == "update" and confidence >= 0.8:
        return UrgencyTier.IMPORTANT
    return UrgencyTier.INTERESTING


async def is_alert_on_cooldown(redis_client: object, rule_id: str) -> bool:
    """Check (without setting) whether an alert rule is currently on cooldown.

    Returns True if the cooldown key exists (rule is on cooldown).
    Returns False if the key is absent (rule is ready to fire).

    The cooldown key is set only after successful delivery via
    set_alert_cooldown(), ensuring failed deliveries do not suppress retries.
    """
    result = await redis_client.exists(  # type: ignore[attr-defined]
        f"alert:cooldown:{rule_id}"
    )
    return bool(result)


async def set_alert_cooldown(
    redis_client: object, rule_id: str, cooldown_minutes: int
) -> None:
    """Set the cooldown key for an alert rule after successful delivery.

    Uses SET NX so that a race between concurrent workers does not extend
    an already-running cooldown window.
    """
    await redis_client.set(  # type: ignore[attr-defined]
        f"alert:cooldown:{rule_id}",
        "1",
        ex=cooldown_minutes * 60,
        nx=True,
    )


async def check_and_deliver_alerts(
    session: AsyncSession,
    redis_client: object,
    items: list[dict],
) -> int:
    """Main orchestration: match items against active rules and deliver alerts.

    For each item x rule combination:
      1. Check keyword match
      2. Check cooldown
      3. Compute urgency
      4. Create AlertDelivery row (pending)
      5. Attempt Slack delivery
      6. Update status (sent / failed)

    Commits in batches of ALERT_BATCH_SIZE (50) instead of per-item to reduce
    database round-trips.

    Returns count of alerts successfully delivered.
    """
    ALERT_BATCH_SIZE = 50

    # Fetch all active alert rules
    result = await session.execute(
        text(
            "SELECT id, keywords, delivery_channels, cooldown_minutes "
            "FROM alert_rules WHERE is_active = true"
        )
    )
    rules = result.fetchall()

    if not rules:
        return 0

    delivered_count = 0
    pending_ops = 0  # track uncommitted operations for batch commit

    for item in items:
        item_id = item["id"]
        item_title = item.get("title", "")
        item_content = item.get("content", "")
        item_tags = item.get("tags", [])
        item_primary_type = item.get("primary_type", "")
        item_url = item.get("url", "")
        item_confidence = float(item.get("confidence_score", 0.0))

        for rule in rules:
            rule_id = str(rule[0])
            rule_keywords = rule[1] or []
            rule_channels = rule[2] or {}
            rule_cooldown = rule[3] or 60

            # 1. Keyword matching OR significance trigger
            sig_trigger = (
                rule_channels.get("significance_trigger") if rule_channels else None
            )
            item_significance = item.get("significance", "")
            matched_by_significance = bool(
                sig_trigger and item_significance in sig_trigger
            )
            matched_by_keywords = matches_keywords(
                rule_keywords, item_title, item_content, item_tags
            )
            if not matched_by_keywords and not matched_by_significance:
                continue

            # 2. Cooldown check (read-only — cooldown is set after successful delivery)
            on_cooldown = await is_alert_on_cooldown(redis_client, rule_id)
            if on_cooldown:
                logger.debug(
                    "alert_rule_on_cooldown",
                    rule_id=rule_id,
                    item_id=item_id,
                )
                continue

            # 3. Compute urgency with breaking change detection
            is_breaking = item_primary_type == "update" and detect_breaking_change(
                item_title, item_content, item_tags
            )
            urgency = compute_urgency(item_primary_type, is_breaking, item_confidence)

            # 4. Create AlertDelivery row (pending)
            delivery_id = str(uuid.uuid4())
            await session.execute(
                text(
                    """
                    INSERT INTO alert_deliveries
                        (id, alert_rule_id, intel_item_id, urgency, status, channel,
                         created_at, updated_at)
                    VALUES
                        (CAST(:id AS uuid), CAST(:rule_id AS uuid),
                         CAST(:item_id AS uuid), :urgency, 'pending', :channel,
                         NOW(), NOW())
                    """
                ),
                {
                    "id": delivery_id,
                    "rule_id": rule_id,
                    "item_id": item_id,
                    "urgency": urgency.value,
                    "channel": "slack",
                },
            )
            pending_ops += 1

            # 5. Attempt Slack delivery (re-validate URL at delivery time for SSRF/DNS rebinding)
            slack_webhook = rule_channels.get("slack_webhook")
            success = False
            error_msg: Optional[str] = None

            if slack_webhook:
                try:
                    _validate_webhook_url(slack_webhook)
                except ValueError as e:
                    logger.warning(
                        "webhook_ssrf_blocked_at_delivery",
                        url=slack_webhook,
                        reason=str(e),
                        channel="slack",
                    )
                    slack_webhook = None
                    error_msg = f"SSRF blocked at delivery time: {e}"

            if slack_webhook:
                success = await deliver_slack_alert(
                    webhook_url=slack_webhook,
                    item_title=item_title,
                    item_url=item_url,
                    item_type=item_primary_type,
                    urgency=urgency.value,
                    tags=item_tags,
                )
                if not success:
                    error_msg = "Slack delivery failed (non-200 response or timeout)"
            else:
                error_msg = "No slack_webhook configured in delivery_channels"

            # 6. Update delivery status
            new_status = "sent" if success else "failed"
            delivered_at_clause = ", delivered_at = NOW()" if success else ""

            await session.execute(
                text(
                    f"""
                    UPDATE alert_deliveries
                    SET status = :status,
                        error_message = :error_msg,
                        updated_at = NOW()
                        {delivered_at_clause}
                    WHERE id = CAST(:id AS uuid)
                    """
                ),
                {
                    "status": new_status,
                    "error_msg": error_msg,
                    "id": delivery_id,
                },
            )
            pending_ops += 1

            if success:
                # Set cooldown AFTER successful delivery so that failures
                # do not suppress retries on the next run.
                await set_alert_cooldown(redis_client, rule_id, rule_cooldown)
                delivered_count += 1
                logger.info(
                    "alert_delivered",
                    rule_id=rule_id,
                    item_id=item_id,
                    urgency=urgency.value,
                )

            # 7. Attempt webhook delivery (separate outbox row, separate channel)
            # Re-validate URL at delivery time to prevent DNS rebinding SSRF
            webhook_url = rule_channels.get("webhook_url")
            if webhook_url:
                try:
                    _validate_webhook_url(webhook_url)
                except ValueError as e:
                    logger.warning(
                        "webhook_ssrf_blocked_at_delivery",
                        url=webhook_url,
                        reason=str(e),
                        channel="webhook",
                    )
                    webhook_url = None

            if webhook_url:
                from src.services.webhook_delivery import deliver_webhook_alert

                webhook_delivery_id = str(uuid.uuid4())
                await session.execute(
                    text(
                        """
                        INSERT INTO alert_deliveries
                            (id, alert_rule_id, intel_item_id, urgency, status, channel,
                             created_at, updated_at)
                        VALUES
                            (CAST(:id AS uuid), CAST(:rule_id AS uuid),
                             CAST(:item_id AS uuid), :urgency, 'pending', :channel,
                             NOW(), NOW())
                        """
                    ),
                    {
                        "id": webhook_delivery_id,
                        "rule_id": rule_id,
                        "item_id": item_id,
                        "urgency": urgency.value,
                        "channel": "webhook",
                    },
                )
                pending_ops += 1

                matched_keyword = next(
                    (
                        kw
                        for kw in rule_keywords
                        if kw.lower()
                        in f"{item_title} {item_content} {' '.join(item_tags)}".lower()
                    ),
                    rule_keywords[0] if rule_keywords else "",
                )
                webhook_success = await deliver_webhook_alert(
                    webhook_url=webhook_url,
                    payload={
                        "rule_id": rule_id,
                        "keyword": matched_keyword,
                        "item": {
                            "id": item_id,
                            "title": item_title,
                            "url": item_url,
                            "significance": item.get("significance"),
                            "primary_type": item_primary_type,
                        },
                        "urgency": urgency.value,
                    },
                    secret=rule_channels.get("webhook_secret"),
                )
                webhook_error_msg: Optional[str] = None
                if not webhook_success:
                    webhook_error_msg = (
                        "Webhook delivery failed (non-2xx response or timeout)"
                    )

                webhook_new_status = "sent" if webhook_success else "failed"
                webhook_delivered_at_clause = (
                    ", delivered_at = NOW()" if webhook_success else ""
                )

                await session.execute(
                    text(
                        f"""
                        UPDATE alert_deliveries
                        SET status = :status,
                            error_message = :error_msg,
                            updated_at = NOW()
                            {webhook_delivered_at_clause}
                        WHERE id = CAST(:id AS uuid)
                        """
                    ),
                    {
                        "status": webhook_new_status,
                        "error_msg": webhook_error_msg,
                        "id": webhook_delivery_id,
                    },
                )
                pending_ops += 1

                if webhook_success:
                    if not success:
                        # Webhook succeeded but Slack didn't — still set cooldown
                        # and count it; webhook delivery is a valid delivery channel.
                        await set_alert_cooldown(redis_client, rule_id, rule_cooldown)
                        delivered_count += 1
                    logger.info(
                        "alert_webhook_delivered",
                        rule_id=rule_id,
                        item_id=item_id,
                        urgency=urgency.value,
                    )

            # Batch commit: flush every ALERT_BATCH_SIZE operations
            if pending_ops >= ALERT_BATCH_SIZE:
                await session.commit()
                pending_ops = 0

    # Final commit for remaining operations
    if pending_ops > 0:
        await session.commit()

    return delivered_count
