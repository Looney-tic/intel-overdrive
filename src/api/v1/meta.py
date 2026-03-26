"""Discovery endpoints — help agents and users understand the API surface."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.models.models import IntelItem, APIKey
from src.api.v1.feed import TAG_GROUPS

meta_router = APIRouter(tags=["discovery"])


@meta_router.get("/tags")
@limiter.limit("60/minute")
async def list_tags(
    request: Request,
    min_count: int = Query(
        1, ge=1, description="Minimum item count for a tag to appear"
    ),
    limit: int = Query(100, ge=1, le=1000, description="Max tags to return"),
    offset: int = Query(0, ge=0, le=10_000_000, description="Offset for pagination"),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """List all tags in the system with item counts.

    Use these values for:
    - `tag` filter on /v1/feed and /v1/search
    - `tech_stack` values when setting your profile via POST /v1/profile

    Returns tags sorted by frequency (most common first).
    Supports limit/offset pagination (default: 100 per page).
    """
    # P2-26: Separate total count query for pagination metadata
    count_result = await session.execute(
        text(
            """
            SELECT count(*) FROM (
                SELECT tag
                FROM intel_items, jsonb_array_elements_text(CAST(tags AS jsonb)) AS tag
                WHERE status = 'processed'
                GROUP BY tag
                HAVING count(*) >= :min_count
            ) sub
        """
        ),
        {"min_count": min_count},
    )
    total = count_result.scalar() or 0

    result = await session.execute(
        text(
            """
            SELECT tag, count(*) as cnt
            FROM intel_items, jsonb_array_elements_text(CAST(tags AS jsonb)) AS tag
            WHERE status = 'processed'
            GROUP BY tag
            HAVING count(*) >= :min_count
            ORDER BY cnt DESC
            LIMIT :limit OFFSET :offset
        """
        ),
        {"min_count": min_count, "limit": limit, "offset": offset},
    )
    rows = result.fetchall()
    tags = [{"tag": row[0], "count": row[1]} for row in rows]
    return JSONResponse(
        content={"tags": tags, "total": total, "limit": limit, "offset": offset}
    )


@meta_router.get("/types")
@limiter.limit("60/minute")
async def list_types(
    request: Request,
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """List all primary_type values with counts.

    Use these for the `type` filter on /v1/feed and /v1/search.
    """
    result = await session.execute(
        select(IntelItem.primary_type, func.count())
        .where(IntelItem.status == "processed")
        .group_by(IntelItem.primary_type)
        .order_by(func.count().desc())
    )
    types = [{"type": row[0], "count": row[1]} for row in result.all()]
    return JSONResponse(content={"types": types})


@meta_router.get("/tag-groups")
@limiter.limit("60/minute")
async def list_tag_groups(
    request: Request,
    api_key: APIKey = Depends(require_api_key),
):
    """List semantically related tag clusters.

    Use the group `name` as the `?group=` parameter on /v1/feed to filter
    by category instead of guessing individual tag names.
    """
    groups = [{"name": name, "tags": tags} for name, tags in TAG_GROUPS.items()]
    return JSONResponse(content={"groups": groups})


@meta_router.get("/guide")
async def api_guide(request: Request):
    """Machine-readable API usage guide. No auth required.

    Auto-generated from OpenAPI spec — always reflects the current API surface.
    An agent hitting this endpoint gets everything it needs to use the API.
    """
    # Derive base URL from request for dynamic guide content
    scheme = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("host", request.base_url.hostname or "localhost:8000")
    base_url = f"{scheme}://{host}"

    # Build endpoints dict from FastAPI's OpenAPI spec (always in sync)
    # In production openapi_url is None, so app.openapi() returns {}.
    # Use get_openapi() directly to always generate the schema.
    from fastapi.openapi.utils import get_openapi

    app = request.app
    openapi = app.openapi()
    if not openapi.get("paths"):
        openapi = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
    endpoints = {}
    skip_paths = {
        "/health",
        "/v1/guide",
        "/v1/ingest/email-webhook",
        "/openapi.json",
        "/docs",
        "/redoc",
    }
    skip_prefixes = ("/v1/admin",)

    for path, methods in sorted(openapi.get("paths", {}).items()):
        if path in skip_paths or any(path.startswith(p) for p in skip_prefixes):
            continue
        for method, details in methods.items():
            key = f"{method.upper()} {path}"
            ep: dict = {"description": details.get("summary", "")}
            # Extract query/path params
            params = {}
            for p in details.get("parameters", []):
                name = p.get("name", "")
                schema = p.get("schema", {})
                desc = p.get("description", "")
                if not desc and "enum" in schema:
                    desc = f"One of: {', '.join(str(v) for v in schema['enum'])}"
                if not desc and "default" in schema:
                    desc = f"Default: {schema['default']}"
                if name and name != "request" and name != "response":
                    params[name] = desc or schema.get("type", "")
            if params:
                ep["params"] = params
            # Extract request body fields
            body = details.get("requestBody", {})
            if body:
                content = body.get("content", {})
                json_body = content.get("application/json", {})
                schema = json_body.get("schema", {})
                props = schema.get("properties", {})
                if props:
                    ep["body"] = {
                        k: v.get("description", v.get("type", ""))
                        for k, v in props.items()
                    }
            endpoints[key] = ep

    return JSONResponse(
        content={
            "name": "Intel Overdrive API",
            "description": "Curated intelligence feed for the AI coding ecosystem — tools, skills, updates, practices, and docs. Auto-classified with significance tiers (breaking/major/minor/informational).",
            "base_url": base_url,
            "auth": {
                "method": "API key in X-API-Key header",
                "example": f"curl -H 'X-API-Key: dti_v1_...' {base_url}/v1/feed",
            },
            "endpoints": endpoints,
            "response_fields": {
                "title": "Item name",
                "summary": "1-2 sentence actionable description of what it is and why it matters",
                "primary_type": "Classification: skill, tool, update, practice, docs",
                "significance": "Impact tier: breaking (act now), major (worth adopting), minor (niche), informational (awareness)",
                "tags": "Searchable labels (lowercase)",
                "url": "Link to source",
                "relevance_score": "0.0-1.0: Semantic relevance to the AI coding ecosystem. Computed by pgvector cosine similarity against curated reference set. Use for ranking/sorting.",
                "quality_score": "0.0-1.0: Source quality signal. Derived from GitHub stars, maintenance activity, security posture (when available). 0.0 for non-GitHub sources. Use for filtering low-quality items.",
                "confidence_score": "0.0-1.0: LLM classification confidence. How sure the classifier is about primary_type and significance. Below 0.7 = uncertain classification. Use for filtering noisy items.",
            },
            "quick_start": [
                "1. GET /v1/guide — you're here (no auth needed)",
                f"2. Register: curl -X POST {base_url}/v1/auth/register -H 'Content-Type: application/json' -d '{{}}' → returns {{api_key: 'dti_v1_anon_...'}}",
                "3. Set header: X-API-Key: dti_v1_... (all endpoints below require auth)",
                "4. GET /v1/feed — get your curated feed",
                "5. GET /v1/feed?sort=significance — breaking changes first",
                "6. POST /v1/profile — set tech_stack, skills, tools, providers for personalized results",
                "7. GET /v1/diff — personalized delta (only items matching your profile)",
                "8. GET /v1/context-pack?topic=mcp — agent-optimized briefing",
                "9. GET /v1/library/topics — browse evergreen best practices",
                "10. GET /v1/library/search?q=<topic> — find synthesized best practices",
            ],
            "agent_setup": {
                "description": "For AI agents (Claude Code, Codex, etc.) — register and use via HTTP. No email or invite code needed.",
                "register": f"curl -X POST {base_url}/v1/auth/register -H 'Content-Type: application/json' -d '{{}}'",
                "usage": f"curl -H 'X-API-Key: dti_v1_...' {base_url}/v1/feed?sort=significance&limit=5",
                "mcp": "For MCP-capable agents: npx overdrive-intel-mcp (set OVERDRIVE_API_KEY and OVERDRIVE_API_URL env vars)",
                "setup_script": f"bash <(curl -s {base_url}/dl/setup.sh)",
            },
        }
    )
