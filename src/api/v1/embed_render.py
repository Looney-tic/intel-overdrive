"""GET /v1/items/{item_id}/embed — multi-format item renderer for newsletters, Slack, and terminal."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.models.models import APIKey

embed_router = APIRouter(tags=["embed"])


def render_markdown(item: dict) -> str:
    """Render item as Markdown newsletter block."""
    tags_str = ", ".join(item["tags"] or [])
    pub = item.get("published_at") or item.get("created_at")
    pub_str = pub.strftime("%Y-%m-%d") if pub else ""
    return (
        f"## [{item['title']}]({item['url']})\n\n"
        f"**{item['significance'] or 'informational'}** · "
        f"{item['primary_type']} · {item['source_name'] or 'Unknown source'}\n\n"
        f"{item['summary'] or item['excerpt'] or ''}\n\n"
        f"*Tags: {tags_str}* · *{pub_str}*"
    )


def _escape_mrkdwn(text: str) -> str:
    """Escape special characters for Slack mrkdwn format."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_slack(item: dict) -> dict:
    """Render item as Slack Block Kit JSON."""
    title = _escape_mrkdwn(item["title"])
    summary_text = _escape_mrkdwn(item["summary"] or item["excerpt"] or item["title"])
    source_name = _escape_mrkdwn(item["source_name"] or "")
    return {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*<{item['url']}|{title}>*\n{summary_text}",
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"{item['significance'] or 'info'} · {item['primary_type']} · {source_name}",
                    }
                ],
            },
        ]
    }


def render_terminal(item: dict) -> str:
    """Render item with ANSI color codes for terminal display."""
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
    tags_str = " ".join(f"[{t}]" for t in (item["tags"] or []))
    return (
        f"{BOLD}{item['title']}{RESET}\n"
        f"{DIM}{item['url']}{RESET}\n"
        f"{item['summary'] or item['excerpt'] or ''}\n"
        f"{DIM}{tags_str}{RESET}"
    )


@embed_router.get("/items/{item_id}/embed")
@limiter.limit("100/minute")
async def get_item_embed(
    request: Request,
    item_id: uuid.UUID,
    format: str = Query(
        "markdown",
        pattern="^(markdown|slack|terminal)$",
        description="Output format: markdown, slack (Block Kit JSON), or terminal (ANSI)",
    ),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns a pre-rendered representation of an intel item in the requested format.

    - markdown: Newsletter-ready Markdown block
    - slack: Slack Block Kit JSON
    - terminal: ANSI-colored plain text for terminal display

    All rendering is pure Python string templates — zero LLM calls.
    Returns 404 if item not found or not yet processed.
    """
    fetch_sql = text(
        """
        SELECT id, title, url, excerpt, summary, primary_type, tags,
               relevance_score, significance, source_name, published_at, created_at
        FROM intel_items
        WHERE id = CAST(:item_id AS uuid) AND status = 'processed'
        """
    )
    result = await session.execute(fetch_sql, {"item_id": str(item_id)})
    row = result.mappings().fetchone()

    if row is None:
        raise HTTPException(
            status_code=404, detail="Item not found or not yet processed"
        )

    item = dict(row)

    if format == "markdown":
        return Response(content=render_markdown(item), media_type="text/markdown")
    elif format == "slack":
        return JSONResponse(content=render_slack(item))
    else:  # terminal
        return Response(content=render_terminal(item), media_type="text/plain")
