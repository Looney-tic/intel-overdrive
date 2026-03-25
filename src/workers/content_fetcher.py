"""Shared content fetching helpers for ingest adapters.

Provides reusable functions for fetching article bodies, GitHub READMEs,
and GitHub repo descriptions. All functions are safe — they return None
on any failure and never raise exceptions.
"""

import asyncio
import re

import httpx

from src.core.logger import get_logger
from src.services.quality_service import parse_github_url  # noqa: F401 — re-export

logger = get_logger(__name__)

_USER_AGENT = "Overdrive-Intel/1.0 (feed aggregator)"

# trafilatura is optional — graceful fallback to regex stripping if not installed
try:
    import trafilatura as _trafilatura  # type: ignore[import]

    _TRAFILATURA_AVAILABLE = True
except ImportError:
    _trafilatura = None  # type: ignore[assignment]
    _TRAFILATURA_AVAILABLE = False


async def fetch_article_body(
    url: str, max_chars: int = 5000, timeout: int = 10
) -> str | None:
    """Fetch article body text from a URL via HTTP GET + text extraction.

    Uses trafilatura for intelligent article extraction when available,
    falls back to regex HTML tag stripping.

    Returns:
        Extracted text (up to max_chars), or None on any failure.
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(float(timeout)),
            follow_redirects=True,
        ) as client:
            response = await client.get(url, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
            html = response.text

        if not html or len(html) < 50:
            return None

        # Try trafilatura first (intelligent article extraction)
        if _TRAFILATURA_AVAILABLE and _trafilatura is not None:
            text = await asyncio.to_thread(
                _trafilatura.extract,
                html,
                output_format="txt",
                include_comments=False,
                include_tables=False,
            )
            if text and len(text) > 50:
                return text[:max_chars]

        # Fallback: regex HTML tag stripping
        text = re.sub(r"<[^>]+>", "", html)
        text = re.sub(r"\s+", " ", text).strip()
        if text and len(text) > 50:
            return text[:max_chars]

        return None

    except Exception as exc:
        logger.debug("fetch_article_body_failed", url=url, error=str(exc)[:100])
        return None


async def fetch_github_readme(
    owner: str, repo: str, token: str | None = None, max_chars: int = 5000
) -> str | None:
    """Fetch README content from GitHub API (raw text).

    Uses the GitHub REST API with Accept: application/vnd.github.raw+json
    to get the README as plain text without base64 decoding.

    Returns:
        README text (up to max_chars), or None on any failure.
    """
    try:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{owner}/{repo}/readme"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            text = response.text

        if text and len(text) > 10:
            return text[:max_chars]
        return None

    except Exception as exc:
        logger.debug(
            "fetch_github_readme_failed",
            owner=owner,
            repo=repo,
            error=str(exc)[:100],
        )
        return None


async def fetch_github_description(
    owner: str, repo: str, token: str | None = None
) -> str | None:
    """Fetch repository description from GitHub API.

    Lightweight alternative to fetch_github_readme — returns just the
    one-line description field from the repo metadata.

    Returns:
        Repo description string, or None on any failure.
    """
    try:
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "User-Agent": _USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{owner}/{repo}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

        description = data.get("description")
        if description and isinstance(description, str) and len(description) > 3:
            return description
        return None

    except Exception as exc:
        logger.debug(
            "fetch_github_description_failed",
            owner=owner,
            repo=repo,
            error=str(exc)[:100],
        )
        return None
