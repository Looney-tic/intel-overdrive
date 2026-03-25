from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import redis.asyncio as aioredis
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import Response

import src.core.init_db as _init_db
from src.core.config import get_settings
from src.core.init_db import init_db, close_db
from src.core.logger import configure_logging, get_logger
from src.api.v1.router import v1_router
from fastapi.middleware.cors import CORSMiddleware
from src.api.limiter import limiter, RateLimitExceeded
from src.api.error_handlers import install_error_handlers
from src.api.ingest_email import router as email_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan manager to handle startup and shutdown logic.
    Inits database engine and creates the shared Redis client.
    """
    configure_logging()
    settings = get_settings()
    await init_db()
    app.state.redis = aioredis.from_url(settings.REDIS_URL)
    yield
    await close_db()
    await app.state.redis.aclose()


_settings = get_settings()
_is_prod = _settings.ENVIRONMENT == "production"
_docs_url = None if _is_prod else "/docs"
_redoc_url = None if _is_prod else "/redoc"
_openapi_url = None if _is_prod else "/openapi.json"

app = FastAPI(
    title="Overdrive Intel API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
    openapi_url=_openapi_url,
)

# Explicit CORS deny-all policy — no browser origins allowed
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["X-API-Key"],
)


# Null byte sanitization middleware — reject requests with %00 in query params or path
@app.middleware("http")
async def sanitize_null_bytes(request: Request, call_next):
    """Return 400 if null bytes are found in query parameters or URL path."""
    # Check URL path
    if "\x00" in request.url.path:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_input",
                    "message": "Null bytes not allowed in request path",
                    "hint": "Remove %00 from your request",
                }
            },
        )
    # Check query parameter values
    for key, value in request.query_params.items():
        if "\x00" in key or "\x00" in value:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "invalid_input",
                        "message": "Null bytes not allowed in query parameters",
                        "hint": "Remove %00 from your request",
                    }
                },
            )
    return await call_next(request)


# P2-28: Security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses.

    Note: HSTS (Strict-Transport-Security) is intentionally omitted here —
    Caddy sets it at the proxy level (max-age=31536000). Setting it here too
    would cause a conflict and duplicate headers in production.
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "0"  # Modern browsers: disabled, use CSP
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# Wire the rate limiter to the app
app.state.limiter = limiter

# Install structured error handlers (replaces slowapi default RateLimitExceeded handler
# and adds global HTTPException + RequestValidationError handlers)
install_error_handlers(app)

# Include v1 router
app.include_router(v1_router)

# Include email webhook router (push-based ingestion via Mailgun)
app.include_router(email_router)


@app.get("/health", tags=["system"])
async def health_check():
    """Infrastructure health check — verifies DB and Redis connectivity.

    Returns 200 {"status": "ok"} if both are reachable.
    Returns 503 {"status": "degraded", "detail": "..."} if either fails.
    """
    errors: list[str] = []

    # Check database connectivity
    if _init_db.async_session_factory is None:
        errors.append("database not initialized")
    else:
        try:
            async with _init_db.async_session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:
            logger.error("health_check_db_failed", error=str(exc))
            errors.append(f"database unreachable: {exc}")

    # Check Redis connectivity
    redis = getattr(app.state, "redis", None)
    if redis is None:
        errors.append("redis not initialized")
    else:
        try:
            await redis.ping()
        except Exception as exc:
            logger.error("health_check_redis_failed", error=str(exc))
            errors.append(f"redis unreachable: {exc}")

    if errors:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "detail": "; ".join(errors)},
        )

    # Fetch last_ingestion timestamp from intel_items (non-fatal if it fails)
    last_ingestion = None
    try:
        async with _init_db.async_session_factory() as session:
            result = await session.execute(
                text("SELECT MAX(created_at) FROM intel_items WHERE status = 'processed'")
            )
            ts = result.scalar()
            last_ingestion = ts.isoformat() if ts else None
    except Exception:
        pass  # Non-fatal — health check still reports DB/Redis status

    return {"status": "ok", "last_ingestion": last_ingestion}
