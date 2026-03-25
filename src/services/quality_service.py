"""
Quality scoring service: computes transparent sub-scores for GitHub-backed items.

Uses cheap GitHub API signals (stars, forks, pushed_at, archived, license)
to compute maintenance, security, and compatibility sub-scores. Also detects
staleness (no push in 180+ days) and unsafe code patterns.

All pure functions except fetch_github_signals (async HTTP).
"""

import math
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.core.logger import get_logger

logger = get_logger(__name__)

# --- GitHub URL parsing ---

_GITHUB_URL_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/.*)?$")


def parse_github_url(url: str) -> tuple[str, str] | None:
    """Extract (owner, repo) from a GitHub URL. Returns None for non-GitHub URLs."""
    m = _GITHUB_URL_RE.match(url)
    if m is None:
        return None
    return m.group(1), m.group(2)


# --- Unsafe pattern detection ---

UNSAFE_PATTERNS: list[tuple[str, str]] = [
    (r"\beval\s*\(", "usage of eval()"),
    (r"\bexec\s*\(", "usage of exec()"),
    (r"os\.system\s*\(", "os.system() call"),
    (r"subprocess\.call\(.*shell\s*=\s*True", "subprocess with shell=True"),
    (r"(?:/home/|/Users/|~/.ssh/|~/.aws/)", "hardcoded user paths"),
    (r"AKIA[A-Z0-9]{16}", "potential AWS access key"),
    (r"sk-[A-Za-z0-9]{17,}", "potential OpenAI/Anthropic secret key"),
    (r"ghp_[A-Za-z0-9]{36,}", "potential GitHub personal access token"),
    (r"gho_[A-Za-z0-9]{36,}", "potential GitHub OAuth token"),
]


def check_safe_patterns(content: str) -> tuple[float, list[str]]:
    """Run regex patterns against content.

    Returns (security_score, findings).
    Score = max(0.0, 1.0 - len(findings) * 0.2).
    """
    findings: list[str] = []
    for pattern, description in UNSAFE_PATTERNS:
        if re.search(pattern, content):
            findings.append(description)
    score = max(0.0, 1.0 - len(findings) * 0.2)
    return score, findings


# --- Staleness detection ---


def is_stale(pushed_at_iso: str) -> bool:
    """Return True if >180 days since last push."""
    pushed_at = datetime.fromisoformat(pushed_at_iso.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return (now - pushed_at).days > 180


# --- GitHub API signals ---


async def fetch_github_signals(
    owner: str, repo: str, github_token: str | None
) -> dict | None:
    """Fetch cheap signals from GitHub /repos endpoint.

    Returns dict with: stars, forks, open_issues, pushed_at, archived,
    has_license, subscribers_count. Returns None on 404/timeout.

    Returns {"rate_limited": True} on 403 or when x-ratelimit-remaining == 0,
    so callers can distinguish rate limiting from repo-not-found (404) and
    break early instead of burning remaining quota.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)

        # 403 = rate limited (or token invalid) — return sentinel so callers break early
        if resp.status_code == 403:
            logger.warning(
                "github_rate_limited",
                status=resp.status_code,
                owner=owner,
                repo=repo,
            )
            return {"rate_limited": True}

        # Check rate limit header — if exhausted, signal callers to stop batching
        remaining = resp.headers.get("x-ratelimit-remaining")
        if remaining is not None and int(remaining) == 0:
            logger.warning(
                "github_rate_limit_exhausted",
                remaining=remaining,
                owner=owner,
                repo=repo,
            )
            return {"rate_limited": True}

        if resp.status_code != 200:
            logger.warning(
                "github_fetch_non_200",
                status=resp.status_code,
                owner=owner,
                repo=repo,
            )
            return None

        data = resp.json()
        return {
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "open_issues": data.get("open_issues_count", 0),
            "pushed_at": data.get("pushed_at"),
            "archived": data.get("archived", False),
            "has_license": data.get("license") is not None,
            "subscribers_count": data.get("subscribers_count", 0),
        }

    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        logger.warning(
            "github_fetch_error",
            error=str(exc),
            owner=owner,
            repo=repo,
        )
        return None


# --- Community score (stars/forks/subscribers) ---


def compute_community_score(signals: dict) -> float:
    """Compute community adoption score from GitHub stars, forks, subscribers.

    Uses log-scale stars as primary signal so that popular projects score
    meaningfully higher.  Approximate scale:
        0 stars=0.0, 10=0.20, 100=0.40, 1000=0.60, 10000=0.80, 100000=1.0

    Non-GitHub items (no stars/forks data) should NOT call this function;
    callers should default to 0.5 (neutral) for them.
    """
    stars = signals.get("stars", 0)
    forks = signals.get("forks", 0)
    subscribers = signals.get("subscribers_count", 0)

    if stars == 0 and forks == 0:
        return 0.0

    # Primary signal: log-scale stars (log10(100000) = 5)
    star_score = min(1.0, math.log10(max(1, stars)) / 5.0)

    # Bonus signals (capped so they stay secondary)
    fork_bonus = min(0.1, math.log10(max(1, forks)) / 50.0)
    sub_bonus = min(0.05, math.log10(max(1, subscribers)) / 100.0)

    return round(min(1.0, star_score + fork_bonus + sub_bonus), 3)


# --- Sub-score computation ---


def compute_quality_subscores(signals: dict, content: Optional[str] = None) -> dict:
    """Compute sub-scores from GitHub signals.

    Returns dict with maintenance, security, compatibility scores,
    is_stale flag, and raw signals.
    """
    # Maintenance: inversely proportional to days since push. Archived = 0.
    pushed_at = signals.get("pushed_at")
    if signals.get("archived"):
        maintenance = 0.0
        days_since_push = None
    elif pushed_at:
        pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        days_since_push = max(0, (datetime.now(timezone.utc) - pushed_dt).days)
        maintenance = max(0.0, min(1.0, 1.0 - (days_since_push / 365)))
    else:
        days_since_push = None
        maintenance = 0.5  # unknown

    # Security: check content patterns if available
    if content:
        security, findings = check_safe_patterns(content)
    else:
        security = 1.0  # benefit of doubt
        findings = []

    # Compatibility: license presence matters, archived penalised
    compatibility = 1.0 if signals.get("has_license") else 0.5
    if signals.get("archived"):
        compatibility = max(0.0, compatibility - 0.2)

    # Staleness
    stale = is_stale(pushed_at) if pushed_at else False

    # Community adoption (stars/forks/subscribers)
    community = compute_community_score(signals)

    return {
        "maintenance": round(maintenance, 3),
        "security": round(security, 3),
        "compatibility": round(compatibility, 3),
        "community": round(community, 3),
        "is_stale": stale,
        "findings": findings,
        "signals": {
            "stars": signals.get("stars", 0),
            "forks": signals.get("forks", 0),
            "open_issues": signals.get("open_issues", 0),
            "days_since_push": days_since_push,
            "has_license": signals.get("has_license", False),
            "archived": signals.get("archived", False),
            "subscribers_count": signals.get("subscribers_count", 0),
        },
    }


def compute_aggregate_quality(subscores: dict) -> float:
    """Weighted average of quality sub-scores, clamped to [0, 1].

    Weights: maintenance 0.25, community 0.30, security 0.25, compatibility 0.20.
    For backward compatibility, missing "community" key defaults to 0.5 (neutral).
    """
    community = subscores.get("community", 0.5)
    score = (
        subscores["maintenance"] * 0.25
        + community * 0.30
        + subscores["security"] * 0.25
        + subscores["compatibility"] * 0.20
    )
    return round(max(0.0, min(1.0, score)), 3)


# --- Heuristic quality scoring for non-GitHub and fallback items ---

_TEST_DEMO_PATTERNS = re.compile(
    r"(?:test[\s_-]?repo|my[\s_-]?first|hello[\s_-]?world|demo[\s_-]?project"
    r"|example[\s_-]?app|todo[\s_-]?app|starter[\s_-]?template|boilerplate"
    r"|foo[\s_-]?bar|lorem)",
    re.IGNORECASE,
)

_LOW_VALUE_TITLE_PATTERNS = re.compile(
    r"^(?:Untitled|)$",
)

_BARE_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def compute_title_penalty(title: str) -> float:
    """Return a penalty for low-value titles.

    Returns -0.25 for empty/untitled/bare-owner-repo, -0.15 for test/demo patterns, 0.0 otherwise.
    """
    if not title or _LOW_VALUE_TITLE_PATTERNS.match(title.strip()):
        return -0.25
    if _BARE_OWNER_REPO_RE.match(title.strip()):
        return -0.25
    if _TEST_DEMO_PATTERNS.search(title):
        return -0.15
    return 0.0


_BAD_SUMMARY_PATTERNS = re.compile(
    r"(?:cannot determine|insufficient detail|placeholder|unavailable"
    r"|no description|click here|read more|subscribe|sign up)",
    re.IGNORECASE,
)


def compute_summary_penalty(summary: str | None) -> float:
    """Return a penalty for missing or low-quality summaries.

    Returns -0.15 for None/short (<20 chars)/bad-pattern match, 0.0 otherwise.
    """
    if summary is None or len(summary.strip()) < 20:
        return -0.15
    if _BAD_SUMMARY_PATTERNS.search(summary):
        return -0.15
    return 0.0


def compute_content_substance(content: str | None) -> float:
    """Graduated content substance score based on content length.

    Returns a value from -0.20 (None) to +0.20 (2000+ chars).
    """
    if content is None:
        return -0.20
    length = len(content)
    if length < 100:
        return -0.15
    if length < 500:
        return 0.05
    if length < 2000:
        return 0.15
    return 0.20


def compute_heuristic_quality(
    tier: str | None,
    content: str | None,
    summary: str | None,
    tags: str | list | None,
    title: str | None = None,
) -> tuple[float, dict]:
    """Compute a heuristic quality score for items without GitHub API signals.

    Uses source tier as base, then applies content substance, summary quality,
    tag presence, title penalties, and summary penalties.

    Returns (score, details_dict).
    """
    import json as _json

    # Base score by tier
    tier_bases = {"tier1": 0.40, "tier2": 0.25, "tier3": 0.10}
    base = tier_bases.get(tier, 0.15)

    # Content substance (graduated)
    content_sub = compute_content_substance(content)

    # Summary bonus: +0.15 for valid summary (>20 chars AND not bad pattern)
    summary_bonus = 0.0
    if (
        summary
        and len(summary.strip()) >= 20
        and not _BAD_SUMMARY_PATTERNS.search(summary)
    ):
        summary_bonus = 0.15

    # Tags bonus: +0.10 for non-empty tags
    has_tags = False
    if tags:
        try:
            parsed_tags = _json.loads(tags) if isinstance(tags, str) else tags
            if parsed_tags:
                has_tags = True
        except (_json.JSONDecodeError, TypeError):
            pass
    tags_bonus = 0.10 if has_tags else 0.0

    # Title penalty (only if title provided)
    title_pen = compute_title_penalty(title) if title is not None else 0.0

    # Summary penalty
    summary_pen = compute_summary_penalty(summary)

    # Combine
    score = base + content_sub + summary_bonus + tags_bonus + title_pen + summary_pen

    # Clamp to [0.05, 1.0]
    score = max(0.05, min(1.0, score))
    score = round(score, 3)

    details = {
        "method": "heuristic",
        "source_tier": tier or "unknown",
        "base_score": base,
        "content_substance": content_sub,
        "summary_bonus": summary_bonus,
        "tags_bonus": tags_bonus,
        "title_penalty": title_pen,
        "summary_penalty": summary_pen,
        "has_tags": has_tags,
        "final_score": score,
    }

    return score, details
