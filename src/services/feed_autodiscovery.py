"""Feed autodiscovery service — detect feed type (RSS, Atom, JSON Feed) from URL."""
from __future__ import annotations

import json
from enum import Enum

import feedparser
import httpx


class FeedType(str, Enum):
    RSS = "rss"
    ATOM = "atom"
    JSON_FEED = "json_feed"
    UNKNOWN = "unknown"


async def detect_feed_type(url: str) -> FeedType:
    """Fetch URL and detect whether it is RSS, Atom, JSON Feed, or Unknown.

    Detection order:
    1. Content-Type header contains "application/feed+json" → JSON_FEED
    2. JSON body with "version" key containing "jsonfeed" → JSON_FEED
    3. feedparser version string: "atom" in version → ATOM
    4. feedparser version is non-empty → RSS (covers rss20, rss10, rss090, etc.)
    5. feedparser has entries (version-less but parseable) → RSS
    6. Otherwise → UNKNOWN
    """
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        follow_redirects=True,
    ) as client:
        response = await client.get(
            url,
            headers={
                "User-Agent": "Overdrive-Intel/1.0 (feed aggregator; polite crawler)"
            },
        )
        response.raise_for_status()

        content_type = response.headers.get("Content-Type", "")
        if "application/feed+json" in content_type:
            return FeedType.JSON_FEED

        content = response.content
        try:
            parsed_json = json.loads(content)
            if isinstance(parsed_json, dict):
                version_val = parsed_json.get("version", "")
                if isinstance(version_val, str) and "jsonfeed" in version_val.lower():
                    return FeedType.JSON_FEED
        except (json.JSONDecodeError, ValueError):
            pass

        parsed = feedparser.parse(content)
        version = parsed.get("version", "")
        if version:
            if "atom" in version.lower():
                return FeedType.ATOM
            return FeedType.RSS

        if parsed.get("entries"):
            return FeedType.RSS

        return FeedType.UNKNOWN
