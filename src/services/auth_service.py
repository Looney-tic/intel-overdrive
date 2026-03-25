import secrets
import hashlib
from typing import Tuple, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ..models.models import APIKey
from ..core.logger import get_logger

logger = get_logger(__name__)

# Dummy hash used for constant-time comparison when key is not found,
# preventing timing attacks that reveal whether a key exists.
_DUMMY_HASH = hashlib.sha256(b"dummy-constant-time-placeholder").hexdigest()


class AuthService:
    def generate_api_key(self, prefix: str = "dti_v1_") -> Tuple[str, str]:
        """
        Generates a new API key and its SHA-256 hash.

        Args:
            prefix: Key prefix — "dti_v1_" for email users, "dti_v1_anon_" for anonymous.

        Returns:
            (raw_key, hashed_key) — store hashed_key in DB, give raw_key to user.
        """
        token = secrets.token_urlsafe(32)
        raw_key = f"{prefix}{token}"
        hashed_key = self.hash_key(raw_key)
        return raw_key, hashed_key

    def hash_key(self, key: str) -> str:
        """
        Hashes an API key using plain SHA-256 (no salt needed — token has 256 bits entropy).
        """
        return hashlib.sha256(key.encode()).hexdigest()

    def validate_key(self, raw_key: str, stored_hash: str) -> bool:
        """
        Timing-safe comparison of a raw API key against its stored hash.

        Uses secrets.compare_digest to prevent timing side-channel attacks.
        """
        computed_hash = self.hash_key(raw_key)
        return secrets.compare_digest(computed_hash, stored_hash)

    def validate_key_format(self, key: str) -> bool:
        """Checks if the key starts with a valid prefix (dti_v1_ or dti_v1_anon_)."""
        return key.startswith("dti_v1_")

    async def get_key_by_hash(
        self, session: AsyncSession, key_hash: str
    ) -> Optional[APIKey]:
        """
        Looks up an APIKey by its SHA-256 hash.

        Returns None if not found, performing a dummy compare_digest to prevent
        timing leakage about whether the key exists.
        """
        from sqlalchemy import select

        result = await session.execute(
            select(APIKey).where(APIKey.key_hash == key_hash)
        )
        api_key = result.scalar_one_or_none()

        if api_key is None:
            # Constant-time dummy comparison to prevent timing oracle
            secrets.compare_digest(key_hash, _DUMMY_HASH)
            return None

        return api_key

    async def increment_usage(self, session: AsyncSession, key_hash: str) -> None:
        """
        Atomically increments usage_count and sets last_used_at for the key.

        Uses a single UPDATE statement (not SELECT + UPDATE) to avoid race conditions.
        """
        stmt = text(
            """
            UPDATE api_keys
            SET usage_count = usage_count + 1,
                last_used_at = NOW()
            WHERE key_hash = :key_hash
            """
        )
        await session.execute(stmt, {"key_hash": key_hash})
        await session.commit()
