from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import User, APIKey
from src.api.deps import get_session, require_api_key
from src.api.schemas import ProfileRequest, ProfileResponse
from src.api.limiter import limiter
from src.api.v1.feed import (
    SKILL_TAG_EXPANSION,
    TOOL_TAG_EXPANSION,
    PROVIDER_TAG_EXPANSION,
)

profile_router = APIRouter(tags=["profile"])


@profile_router.get("/profile")
@limiter.limit("60/minute")
async def get_user_profile(
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """Read your current profile. Shows tech_stack and skills used for feed boosting."""
    user_query = select(User).where(User.id == api_key.user_id)
    result = await session.execute(user_query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    profile = user.profile or {}
    return JSONResponse(
        content={
            "profile": profile,
            "hint": "POST /v1/profile to update. Use GET /v1/tags to see valid tech_stack values.",
        }
    )


@profile_router.post("/profile", response_model=ProfileResponse)
@limiter.limit("30/minute")
async def update_user_profile(
    request: Request,
    body: ProfileRequest,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Update or create a user profile with tech stack and skills.
    User is identified via the APIKey's user_id.
    """
    user_query = select(User).where(User.id == api_key.user_id)
    result = await session.execute(user_query)
    user = result.scalar_one_or_none()

    if not user:
        # Should not happen as APIKey has a FK to User
        raise HTTPException(status_code=404, detail="User not found")

    # Validate skills against known SKILL_TAG_EXPANSION keys
    valid_skills = sorted(SKILL_TAG_EXPANSION.keys())
    valid_tools = sorted(TOOL_TAG_EXPANSION.keys())
    valid_providers = sorted(PROVIDER_TAG_EXPANSION.keys())
    warnings = []
    for skill in body.skills or []:
        if skill not in SKILL_TAG_EXPANSION:
            warnings.append(
                f"Unrecognized skill '{skill}' — will not affect feed ranking. "
                f"Valid: {', '.join(valid_skills)}"
            )
    for tool in body.tools:
        if tool not in TOOL_TAG_EXPANSION:
            warnings.append(
                f"Unrecognized tool '{tool}' — will not affect feed ranking. "
                f"Valid: {', '.join(valid_tools)}"
            )
    for provider in body.providers:
        if provider not in PROVIDER_TAG_EXPANSION:
            warnings.append(
                f"Unrecognized provider '{provider}' — will not affect feed ranking. "
                f"Valid: {', '.join(valid_providers)}"
            )

    # Update profile JSON (store all values, even unrecognized — backward compat)
    # Start from existing profile so partial updates (e.g., tech_stack only) preserve other fields
    existing_profile: dict = user.profile or {}
    profile_data: dict = {**existing_profile, "tech_stack": body.tech_stack}
    # Only overwrite skills when explicitly provided; omitting skills preserves existing value
    if body.skills is not None:
        profile_data["skills"] = body.skills
    if body.tools:
        profile_data["tools"] = body.tools
    if body.providers:
        profile_data["providers"] = body.providers
    if body.role:
        profile_data["role"] = body.role
    user.profile = profile_data

    session.add(user)
    await session.commit()
    await session.refresh(user)

    response = {
        "message": "Profile updated successfully",
        "profile": user.profile,
        "valid_skills": valid_skills,
        "valid_tools": valid_tools,
        "valid_providers": valid_providers,
    }
    if warnings:
        response["warnings"] = warnings

    return JSONResponse(content=response)
