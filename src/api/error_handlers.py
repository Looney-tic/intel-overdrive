"""
Structured error response formatting for the Overdrive Intel API.

All error responses use the envelope:
  {"error": {"code": "...", "message": "...", "hint": "..."}}

Error codes:
  MISSING_API_KEY    - No X-API-Key header provided
  INVALID_API_KEY    - Key not found or bad format
  INACTIVE_API_KEY   - Key revoked / is_active=False
  RATE_LIMITED       - Rate limit exceeded (429)
  NOT_FOUND          - Resource not found (404)
  VALIDATION_ERROR   - Request body/query validation failure (422)
  INTERNAL_ERROR     - Unhandled server error (500)
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi.errors import RateLimitExceeded

from src.core.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Error code constants
# ---------------------------------------------------------------------------

MISSING_API_KEY = "MISSING_API_KEY"
INVALID_API_KEY = "INVALID_API_KEY"
INACTIVE_API_KEY = "INACTIVE_API_KEY"
RATE_LIMITED = "RATE_LIMITED"
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"

# Mapping from plain-text detail strings (legacy) to structured error codes.
# deps.py raises HTTPException with these detail strings; the handler maps them.
_DETAIL_TO_CODE = {
    "Missing API key": (MISSING_API_KEY, "Add X-API-Key header. See GET /v1/guide"),
    "Invalid API key": (INVALID_API_KEY, "Check your API key. Keys start with dti_v1_"),
    "Inactive API key": (
        INACTIVE_API_KEY,
        "This key has been revoked. Register a new account via POST /v1/auth/register",
    ),
    # Legacy catch-all (old combined message)
    "Invalid or inactive API key": (
        INVALID_API_KEY,
        "Check your API key. Keys start with dti_v1_",
    ),
}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def structured_error_response(
    status_code: int,
    code: str,
    message: str,
    hint: str | None = None,
    extra_headers: dict | None = None,
) -> JSONResponse:
    """Return a JSONResponse with the standard error envelope."""
    content = {"error": {"code": code, "message": message, "hint": hint}}
    headers = extra_headers or {}
    return JSONResponse(status_code=status_code, content=content, headers=headers)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


async def _rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Replace slowapi's default 429 handler with structured response + Retry-After."""
    # slowapi attaches reset info to the exception; try to extract seconds until reset.
    retry_after: int = 60  # safe default

    # RateLimitExceeded has a `limit` attribute with a `reset_time` on the storage
    # The easiest way: inspect exc.detail which slowapi sets as a string like
    # "2 per 1 minute" — we use a fixed 60s default as the authoritative reset.
    # slowapi also adds X-RateLimit-Reset header automatically via headers_enabled=True.
    try:
        # Try to get window size in seconds from the limit string
        limit_str = str(exc.detail) if hasattr(exc, "detail") else ""
        if "second" in limit_str:
            retry_after = 1
        elif "minute" in limit_str:
            retry_after = 60
        elif "hour" in limit_str:
            retry_after = 3600
        elif "day" in limit_str:
            retry_after = 86400
    except Exception:
        pass

    logger.warning(
        "RATE_LIMIT_EXCEEDED",
        path=str(request.url.path),
        retry_after=retry_after,
    )

    return structured_error_response(
        status_code=429,
        code=RATE_LIMITED,
        message="Rate limit exceeded. Please slow down your requests.",
        hint=f"Retry after {retry_after} seconds.",
        extra_headers={"Retry-After": str(retry_after)},
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Wrap HTTPException detail strings into the structured error envelope.

    Special case: context-pack with format=text returns plain-text errors so that
    error messages don't get injected as JSON into agent system prompts.
    """
    from starlette.responses import Response

    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)

    # Return plain-text error for context-pack requests with format=text
    if (
        "/context-pack" in str(request.url.path)
        and request.query_params.get("format") == "text"
    ):
        return Response(
            content=f"ERROR: {detail}",
            media_type="text/plain",
            status_code=exc.status_code,
        )

    # Map known detail strings → structured codes
    if detail in _DETAIL_TO_CODE:
        code, hint = _DETAIL_TO_CODE[detail]
        message = detail
    elif exc.status_code == 404:
        code = NOT_FOUND
        message = detail or "Resource not found."
        hint = None
    elif exc.status_code == 401:
        code = MISSING_API_KEY
        message = detail or "Authentication required."
        hint = "Add X-API-Key header. See GET /v1/guide"
    elif exc.status_code == 403:
        code = INVALID_API_KEY
        message = detail or "Forbidden."
        hint = None
    elif exc.status_code >= 500:
        code = INTERNAL_ERROR
        message = "An internal server error occurred."
        hint = None
        logger.error("HTTP_EXCEPTION", status=exc.status_code, detail=detail)
    else:
        code = f"HTTP_{exc.status_code}"
        message = detail
        hint = None

    return structured_error_response(
        status_code=exc.status_code,
        code=code,
        message=message,
        hint=hint,
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return VALIDATION_ERROR with field-level details from pydantic errors."""
    errors = exc.errors()
    # Build a concise message from the first error
    if errors:
        first = errors[0]
        loc = " → ".join(str(p) for p in first.get("loc", []))
        msg = first.get("msg", "Validation error")
        message = f"Validation error at {loc}: {msg}" if loc else msg
    else:
        message = "Request validation failed."

    return structured_error_response(
        status_code=422,
        code=VALIDATION_ERROR,
        message=message,
        hint=None,
    )


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install_error_handlers(app) -> None:
    """
    Register structured error handlers on the FastAPI app.

    Must be called AFTER adding slowapi middleware (so our RateLimitExceeded
    handler replaces the slowapi default).
    """
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
