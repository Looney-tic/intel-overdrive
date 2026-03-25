import redis.asyncio as redis
from ..core.config import get_settings
from ..core.logger import get_logger
from datetime import datetime, timezone

logger = get_logger(__name__)


class SpendLimitExceeded(Exception):
    """Raised when the daily spend limit has been reached."""

    def __init__(self, current: float, limit: float):
        self.current = current
        self.limit = limit
        super().__init__(f"Daily spend limit exceeded: ${current:.4f} >= ${limit:.2f}")


# Atomic Lua script: checks limit and increments using integer cents (INCRBY).
# All values are in cents (1 dollar = 100 cents) to avoid float precision drift.
# Returns new_total_cents on success, or nil if limit would be exceeded.
LUA_SPEND_TRACKER = """
local current_cents = tonumber(redis.call('GET', KEYS[1]) or "0")
local increment_cents = tonumber(ARGV[1])
local limit_cents = tonumber(ARGV[2])

if current_cents + increment_cents > limit_cents then
    return nil
end

local new_total = redis.call('INCRBY', KEYS[1], increment_cents)
redis.call('EXPIRE', KEYS[1], ARGV[3])

return new_total
"""


class SpendTracker:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.settings = get_settings()
        self._script = self.redis.register_script(LUA_SPEND_TRACKER)

    def _get_key(self) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"spend:{today}"

    def _get_user_key(self, user_id: str) -> str:
        """Get per-user daily spend Redis key."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"spend:{user_id}:{today}"

    def _dollars_to_cents(self, dollars: float) -> int:
        """Convert dollar amount to integer cents for precision-safe storage.

        Any non-zero positive charge registers as at least 1 cent — prevents
        sub-cent API calls (e.g. $0.000x) from being silently ignored and
        causing cumulative untracked spend.
        """
        cents = round(dollars * 100)
        if cents == 0 and dollars > 0:
            return 1
        return cents

    def _cents_to_dollars(self, cents: int) -> float:
        """Convert integer cents back to dollar float."""
        return cents / 100.0

    async def _track_key(self, key: str, amount: float) -> float:
        """Atomically increment a spend key and check against the global limit.

        Returns the new total (in dollars) for the given key.
        Raises SpendLimitExceeded if the global limit would be exceeded.
        """
        limit = self.settings.DAILY_SPEND_LIMIT
        expiry = 172800  # 48 hours

        increment_cents = self._dollars_to_cents(amount)
        limit_cents = self._dollars_to_cents(limit)

        result = await self._script(
            keys=[key], args=[increment_cents, limit_cents, expiry]
        )

        if result is None:
            current = await self.get_current_spend()
            logger.warning(
                "DAILY_SPEND_LIMIT_EXCEEDED", limit=limit, current=current, key=key
            )
            raise SpendLimitExceeded(current=current, limit=limit)

        return self._cents_to_dollars(int(result))

    async def track_spend(self, amount: float, user_id: str | None = None) -> float:
        """
        Atomically increments daily spend and checks against limit.

        Tracks against the global key (safety ceiling). If user_id is provided,
        also tracks per-user spend for cost attribution (C-2).

        Returns the new cumulative global spend total (in dollars) on success.
        Raises SpendLimitExceeded if adding amount would exceed the daily limit.
        """
        if amount <= 0:
            return await self.get_current_spend()

        # Track against global key (safety ceiling)
        new_total = await self._track_key(self._get_key(), amount)

        # Also track per-user if user_id provided
        if user_id:
            user_key = self._get_user_key(user_id)
            await self._track_key(user_key, amount)

        logger.debug(
            "SPEND_TRACKED", amount=amount, new_total=new_total, user_id=user_id
        )
        return new_total

    async def reserve_spend(self, amount: float, user_id: str | None = None) -> float:
        """Atomically reserve budget BEFORE the LLM call (H-2).

        Pessimistic reservation: increments the global (and optionally per-user)
        key by the estimated cost. Call refund_spend() after the actual cost is known
        to return any over-reservation.
        """
        return await self.track_spend(amount, user_id=user_id)

    async def refund_spend(self, reserved: float, actual: float) -> None:
        """Refund over-reservation after actual cost is known (H-2).

        If actual cost was less than reserved, decrement the difference from the
        global key. Per-user refunds are not implemented yet (global ceiling is
        the safety-critical path).
        """
        delta = reserved - actual
        if delta > 0:
            key = self._get_key()
            delta_cents = self._dollars_to_cents(delta)
            await self.redis.decrby(key, delta_cents)
            logger.debug(
                "SPEND_REFUNDED", reserved=reserved, actual=actual, delta=delta
            )

    async def check_spend_gate(self) -> None:
        """
        Pre-flight check: raises SpendLimitExceeded if current spend is already
        at or over the daily limit. Does NOT increment spend.
        """
        current = await self.get_current_spend()
        limit = self.settings.DAILY_SPEND_LIMIT
        if current >= limit:
            logger.warning("SPEND_GATE_BLOCKED", current=current, limit=limit)
            raise SpendLimitExceeded(current=current, limit=limit)

    async def get_current_spend(self) -> float:
        key = self._get_key()
        val = await self.redis.get(key)
        if val is None:
            return 0.0
        # Stored as integer cents; convert back to dollars
        return self._cents_to_dollars(int(val))

    async def get_user_spend(self, user_id: str) -> float:
        """Read per-user spend for cost attribution (C-2)."""
        key = self._get_user_key(user_id)
        val = await self.redis.get(key)
        if val is None:
            return 0.0
        return self._cents_to_dollars(int(val))

    async def get_remaining_spend(self) -> float:
        current = await self.get_current_spend()
        return max(0.0, self.settings.DAILY_SPEND_LIMIT - current)
