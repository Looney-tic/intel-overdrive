import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from ..models.models import IntelItem
from ..core.logger import get_logger

logger = get_logger(__name__)


def normalize_url(url: str) -> str:
    """Normalize known URL patterns to canonical form for dedup.

    Handles:
    - Smithery: /servers/ and /server/ are the same item
    - Trailing slashes
    - Fragment removal
    """
    url = url.split("#")[0].rstrip("/")
    # Smithery: normalize /servers/ to /server/ (canonical)
    url = re.sub(r"smithery\.ai/servers/", "smithery.ai/server/", url)
    return url


class DedupService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------------
    # Hash helpers
    # ------------------------------------------------------------------

    def _compute_url_hash(self, url: str) -> str:
        """Returns SHA-256 hex digest of the normalized URL."""
        return hashlib.sha256(normalize_url(url).encode()).hexdigest()

    def _get_url_hash(self, url: str) -> str:
        """Alias for backwards compatibility — delegates to _compute_url_hash."""
        return self._compute_url_hash(url)

    def _get_content_fingerprint(self, content: str) -> str:
        """Normalises content and returns SHA-256 hex digest."""
        # Normalize: lowercase, remove non-alphanumeric, collapse whitespace
        normalized = re.sub(r"\W+", "", content.lower())
        return hashlib.sha256(normalized.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Layer 1: URL hash check
    # ------------------------------------------------------------------

    async def check_url_exists(self, url: str) -> bool:
        """Layer 1: checks whether the normalized URL is already in the DB."""
        normalized = normalize_url(url)
        # Check both the normalized URL and the original (handles pre-normalization data)
        query = (
            select(IntelItem.id)
            .where((IntelItem.url == url) | (IntelItem.url == normalized))
            .limit(1)
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Layer 2: Content fingerprint check
    # ------------------------------------------------------------------

    async def find_duplicate_by_content(self, content: str) -> Optional[IntelItem]:
        """Layer 2: finds an existing item with the same content fingerprint."""
        fingerprint = self._get_content_fingerprint(content)
        query = select(IntelItem).where(IntelItem.content_hash == fingerprint)
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Layer 3: Embedding cosine similarity check
    # ------------------------------------------------------------------

    async def find_similar_by_embedding(
        self,
        embedding: list[float],
        threshold: float = 0.08,
        days: int = 7,
    ) -> Optional[IntelItem]:
        """
        Layer 3: finds a semantically similar item using pgvector cosine distance.

        Uses the <=> operator (cosine distance) against IntelItem.embedding.
        Only compares against items ingested within the last `days` days.
        Returns the closest matching item if distance < threshold, else None.

        Single round-trip: distance computation, threshold filter, and ORM
        hydration are all performed in one query via select().from_statement().
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        stmt = select(IntelItem).from_statement(
            text(
                """
                    SELECT *
                    FROM intel_items
                    WHERE embedding IS NOT NULL
                      AND created_at >= :cutoff
                      AND (embedding <=> CAST(:embedding AS vector)) < :threshold
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT 1
                    """
            ).bindparams(
                cutoff=cutoff,
                embedding=str(embedding),
                threshold=threshold,
            )
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Unified 3-layer check
    # ------------------------------------------------------------------

    async def is_duplicate(
        self,
        url: str,
        content: str,
        embedding: Optional[list[float]] = None,
    ) -> tuple[bool, Optional[str]]:
        """
        Runs all 3 dedup layers in cost order (cheapest first).

        Returns:
            (True, reason) if duplicate found — reason is one of:
                "url_hash", "content_fingerprint", "embedding_similarity"
            (False, None) if no duplicate found
        """
        # Layer 1: URL hash (O(1) index lookup)
        if await self.check_url_exists(url):
            logger.debug("DEDUP_HIT_URL_HASH", url=url)
            return True, "url_hash"

        # Layer 2: Content fingerprint (O(1) index lookup)
        if await self.find_duplicate_by_content(content) is not None:
            logger.debug("DEDUP_HIT_CONTENT_FINGERPRINT", url=url)
            return True, "content_fingerprint"

        # Layer 3: Embedding cosine similarity (vector ANN search — most expensive)
        if embedding is not None:
            similar = await self.find_similar_by_embedding(embedding)
            if similar is not None:
                logger.debug(
                    "DEDUP_HIT_EMBEDDING_SIMILARITY",
                    url=url,
                    similar_id=str(similar.id),
                )
                return True, "embedding_similarity"

        return False, None
