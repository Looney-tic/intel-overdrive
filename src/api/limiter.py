import ipaddress

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from src.core.config import get_settings


def get_ip_from_request(request: Request) -> str:
    """Extract real client IP, respecting X-Forwarded-For behind trusted proxies.

    When the direct client IP is within a trusted proxy CIDR range (configured
    via TRUSTED_PROXY_CIDRS), extracts the real client IP from X-Forwarded-For
    or X-Real-IP headers. This ensures rate limiting works correctly behind
    Caddy or other reverse proxies.

    H-3 fix: Always bucket by IP to prevent rate limit bypass via random
    API key headers. Per-user scoping deferred to Plan 04 tier-based limits.
    P1-12: X-Forwarded-For extraction with trusted proxy validation.
    """
    client_ip = request.client.host if request.client else "unknown"
    settings = get_settings()
    trusted_cidrs = [
        c.strip() for c in settings.TRUSTED_PROXY_CIDRS.split(",") if c.strip()
    ]
    try:
        addr = ipaddress.ip_address(client_ip)
        if any(
            addr in ipaddress.ip_network(cidr, strict=False) for cidr in trusted_cidrs
        ):
            forwarded = request.headers.get("x-forwarded-for") or request.headers.get(
                "x-real-ip"
            )
            if forwarded:
                return f"ip:{forwarded.split(',')[0].strip()}"
    except ValueError:
        pass
    return f"ip:{client_ip}"


# Backward-compatible alias for tests that import the old name
get_api_key_from_request = get_ip_from_request


def get_rate_limit(endpoint: str) -> str:
    """Get configurable rate limit string for an endpoint (H-14).

    Rate limit values are operator-tunable via env vars:
    RATE_LIMIT_FEED, RATE_LIMIT_SEARCH, RATE_LIMIT_CONTEXT_PACK, RATE_LIMIT_ADMIN.

    Usage: endpoints should use ``@limiter.limit(get_rate_limit("feed"))`` pattern.
    Since slowapi evaluates the string at decoration time, changing env vars
    requires a server restart. A future plan can switch to dynamic limit
    functions using ``limiter.limit(lambda: get_rate_limit("feed"))``.

    This function is importable and tested. Actual wiring into endpoint
    decorators is deferred — it requires touching all endpoint files.
    """
    settings = get_settings()
    limits = {
        "feed": settings.RATE_LIMIT_FEED,
        "search": settings.RATE_LIMIT_SEARCH,
        "context_pack": settings.RATE_LIMIT_CONTEXT_PACK,
        "admin": settings.RATE_LIMIT_ADMIN,
    }
    return limits.get(endpoint, settings.RATE_LIMIT_DEFAULT)


# storage_uri MUST be passed at construction time
limiter = Limiter(
    key_func=get_ip_from_request,
    storage_uri=get_settings().REDIS_URL,
    headers_enabled=True,
    default_limits=[],
)
