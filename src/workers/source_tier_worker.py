"""Source tier auto-adjustment worker: promote/demote source tiers based on signal ratios.

Weekly cron (Sunday 03:00 UTC) that evaluates item signal ratios per source
and adjusts tiers: demote on >50% negative, promote on >70% positive.
Requires minimum 10 signals to act.
"""

import src.core.init_db as _db
from src.core.logger import get_logger
from sqlalchemy import text

logger = get_logger(__name__)

# Tier ordering for promotion/demotion
TIER_ORDER = ["tier3", "tier2", "tier1"]


def _promote(current_tier: str) -> str:
    """Move one tier up (tier3->tier2, tier2->tier1, tier1 stays)."""
    idx = TIER_ORDER.index(current_tier)
    return TIER_ORDER[min(idx + 1, len(TIER_ORDER) - 1)]


def _demote(current_tier: str) -> str:
    """Move one tier down (tier1->tier2, tier2->tier3, tier3 stays)."""
    idx = TIER_ORDER.index(current_tier)
    return TIER_ORDER[max(idx - 1, 0)]


async def adjust_source_tiers(ctx: dict) -> None:
    """Slow queue cron: adjust source tiers based on signal ratios over last 30 days.

    For each active source with >= 10 signals in the last 30 days:
    - negative_ratio > 0.5 => demote
    - positive_ratio > 0.7 => promote
    - Otherwise: no change
    """
    if _db.async_session_factory is None:
        logger.error("adjust_source_tiers_called_before_db_init")
        return

    query = text(
        """
        SELECT s.id, s.tier, s.name,
               COUNT(CASE WHEN sig.action = 'upvote' THEN 1 END) AS positive,
               COUNT(CASE WHEN sig.action = 'dismiss' THEN 1 END) AS negative,
               COUNT(*) AS total
        FROM sources s
        JOIN intel_items ii ON ii.source_id = s.id
        JOIN item_signals sig ON sig.item_id = ii.id
        WHERE sig.created_at > NOW() - INTERVAL '30 days'
          AND s.is_active = TRUE
        GROUP BY s.id, s.tier, s.name
        HAVING COUNT(*) >= 10
    """
    )

    promotions = 0
    demotions = 0
    evaluated = 0

    async with _db.async_session_factory() as session:
        result = await session.execute(query)
        rows = result.fetchall()
        evaluated = len(rows)

        for row in rows:
            source_id = row.id
            current_tier = row.tier
            source_name = row.name
            positive = row.positive
            negative = row.negative
            total = row.total

            if total == 0:
                continue

            negative_ratio = negative / total
            positive_ratio = positive / total

            new_tier = current_tier

            if negative_ratio > 0.5:
                new_tier = _demote(current_tier)
            elif positive_ratio > 0.7:
                new_tier = _promote(current_tier)

            if new_tier != current_tier:
                await session.execute(
                    text("UPDATE sources SET tier = :new_tier WHERE id = :source_id"),
                    {"new_tier": new_tier, "source_id": source_id},
                )

                action = (
                    "promoted"
                    if TIER_ORDER.index(new_tier) > TIER_ORDER.index(current_tier)
                    else "demoted"
                )
                if action == "promoted":
                    promotions += 1
                else:
                    demotions += 1

                logger.info(
                    "SOURCE_TIER_ADJUSTED",
                    source_id=source_id,
                    source_name=source_name,
                    action=action,
                    from_tier=current_tier,
                    to_tier=new_tier,
                    positive=positive,
                    negative=negative,
                    total=total,
                    positive_ratio=round(positive_ratio, 3),
                    negative_ratio=round(negative_ratio, 3),
                )

        await session.commit()

    logger.info(
        "SOURCE_TIER_ADJUSTMENT_COMPLETE",
        sources_evaluated=evaluated,
        promotions=promotions,
        demotions=demotions,
    )
