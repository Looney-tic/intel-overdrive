"""
Self-service registration endpoint.

Endpoints:
  POST /v1/auth/register  -- create a new User + first API key (no auth required)

Supports two modes:
  - Anonymous (no email): returns dti_v1_anon_ prefixed key, tier "free-anon"
  - Email: returns dti_v1_ prefixed key, tier "free"

Gated behind SIGNUP_ENABLED config. Rate-limited by IP (5/hour).
"""
import asyncio
import re
import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from src.api.deps import get_session
from src.api.limiter import limiter
from src.core.config import get_settings
from src.core.logger import get_logger
from src.models.models import APIKey, User
from src.services.auth_service import AuthService
from src.services.slack_delivery import notify_signup
from src.api.schemas import RegisterRequest, RegisterResponse

logger = get_logger(__name__)
_auth = AuthService()

auth_router = APIRouter(prefix="/auth", tags=["auth"])

# Simple but solid email regex -- catches most invalid inputs
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


@auth_router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Self-service registration",
    description=(
        "Creates a new user account and first API key. "
        "Email is optional — omit for anonymous registration. "
        "Rate-limited to 5/hour per IP."
    ),
)
@limiter.limit("5/hour")
async def register(
    request: Request,
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Self-service registration: creates User + first API key.

    Anonymous (no email): key prefix dti_v1_anon_, tier free-anon.
    With email: key prefix dti_v1_, tier free.
    """
    settings = get_settings()

    # Gate: signup must be enabled
    if not settings.SIGNUP_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is currently disabled.",
        )

    # P1-8: Global registration rate limit — defense-in-depth against distributed abuse
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is not None:
        global_count = await redis_client.incr("registration:global:count")
        if global_count == 1:
            # First registration in this window — set 1-hour TTL
            await redis_client.expire("registration:global:count", 3600)
        if global_count > 50:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": {
                        "code": "registration_limit",
                        "message": "Too many registrations system-wide. Please try again later.",
                        "hint": "Try again in an hour",
                    }
                },
            )

    is_anon = not body.email or not body.email.strip()

    if is_anon:
        # Anonymous registration — no email, no invite code check
        user = User(email=None, is_active=True, tier="free-anon")
        session.add(user)
        await session.flush()

        raw_key, key_hash = _auth.generate_api_key(prefix="dti_v1_anon_")
        api_key_obj = APIKey(
            key_hash=key_hash,
            key_prefix=raw_key[:14],  # "dti_v1_anon_XX"
            user_id=user.id,
            name="default",
            is_active=True,
        )
        session.add(api_key_obj)
        await session.commit()

        anon_id = _uuid.uuid4().hex[:12]
        logger.info(
            "USER_REGISTERED",
            user_id=str(user.id),
            anon_ref=anon_id,
            tier="free-anon",
        )

        # Slack notification (fire-and-forget)
        webhook_url = settings.SLACK_WEBHOOK_URL
        if webhook_url:
            asyncio.create_task(notify_signup(webhook_url, str(user.id), "free-anon"))

        response_obj = RegisterResponse(
            api_key=raw_key,
            user_id=str(user.id),
            tier="free-anon",
            message="Registration successful. Store your API key securely -- it cannot be retrieved again.",
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=response_obj.model_dump(mode="json"),
        )

    # --- Email registration path ---

    email = body.email.strip()

    # Validate email format
    if not _EMAIL_RE.match(email):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid email format.",
        )

    # Check for existing user
    existing = await session.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    # Create user with email
    user = User(email=email, is_active=True, tier="free")
    session.add(user)
    await session.flush()

    # Generate first API key
    raw_key, key_hash = _auth.generate_api_key(prefix="dti_v1_")
    api_key_obj = APIKey(
        key_hash=key_hash,
        key_prefix=raw_key[:14],  # "dti_v1_XXXXXXX"
        user_id=user.id,
        name="default",
        is_active=True,
    )
    session.add(api_key_obj)
    await session.commit()

    logger.info(
        "USER_REGISTERED",
        user_id=str(user.id),
        email_masked=email[:3] + "***" if len(email) > 3 else "***",
        tier="free",
    )

    # Slack notification (fire-and-forget)
    webhook_url = settings.SLACK_WEBHOOK_URL
    if webhook_url:
        asyncio.create_task(notify_signup(webhook_url, str(user.id), "free"))

    response_obj = RegisterResponse(
        api_key=raw_key,
        user_id=str(user.id),
        tier="free",
        message="Registration successful. Store your API key securely -- it cannot be retrieved again.",
    )
    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=response_obj.model_dump(mode="json"),
    )
