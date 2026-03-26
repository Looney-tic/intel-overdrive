"""GET /v1/context-pack — token-budgeted intelligence briefing for agent system prompt injection."""
import json as _json_mod
import urllib.parse
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from src.api.deps import get_session, require_api_key
from src.api.limiter import limiter
from src.api.query_logger import log_query
from src.api.cache import (
    make_cache_key,
    get_cached_response,
    set_cached_response,
    is_cache_enabled,
    get_redis_from_request,
)
from src.api.schemas import ContextPackMeta, IntelItemResponse
from src.models.models import APIKey
from src.api.v1.feed import TAG_GROUPS
from src.api.v1.similar import _embed_concept
from src.core.logger import get_logger

logger = get_logger(__name__)

context_pack_router = APIRouter(tags=["context-pack"])

CHARS_PER_TOKEN = 4


def _format_item(item: dict) -> str:
    """Format a single item as plain text for agent injection."""
    tags_str = ", ".join(item["tags"] or [])
    score = item.get("relevance_score") or 0.0
    sig = item.get("significance") or "informational"
    return (
        f"## {item['title']}\n"
        f"{item.get('summary') or item.get('excerpt') or ''}\n"
        f"URL: {item['url']}\n"
        f"Type: {item['primary_type']} | Sig: {sig} | Score: {score:.2f}\n"
        f"Tags: {tags_str}\n\n"
    )


def _build_header(
    topic: str | None,
    n_items: int,
    budget: int,
    days: int,
    n_library_entries: int = 0,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = (
        f"Intelligence Briefing: {topic}" if topic else "General Intelligence Briefing"
    )
    library_note = (
        f" + {n_library_entries} library entries" if n_library_entries > 0 else ""
    )
    return (
        f"# {title}\n"
        f"Generated: {now}\n"
        f"Items: {n_items}{library_note} | Budget: {budget} tokens\n\n"
    )


def _build_footer(n_items: int, days: int) -> str:
    return f"---\nSource: Intel Overdrive | {n_items} items from last {days} days"


def _format_library_entry(entry: dict) -> str:
    """Format a single library entry as plain text for agent injection."""
    lines = [f"## Library: {entry['title']}"]
    if entry.get("tldr"):
        lines.append(entry["tldr"])
    key_points = entry.get("key_points") or []
    if key_points:
        lines.append("Key points:")
        for kp in key_points:
            lines.append(f"- {kp}")
    gotchas = entry.get("gotchas") or []
    if gotchas:
        lines.append("Gotchas:")
        for g in gotchas:
            if isinstance(g, dict):
                lines.append(f"- {g.get('title', '')}: {g.get('detail', '')}")
            else:
                lines.append(f"- {g}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _compress_to_bullets(selected: list[dict], topic: str | None, days: int) -> str:
    """Compress a list of intel items into 3-5 significance-tier bullets.

    Groups items by significance (breaking, major, minor, informational),
    generates one bullet per non-empty group, and prepends a count header.
    No LLM call — purely rule-based.
    """
    topic_str = f" on {topic}" if topic else ""
    header = f"{len(selected)} items{topic_str} (last {days}d) -- compressed view:"

    if not selected:
        return header

    # Group by significance tier
    tiers: dict[str, list[dict]] = {
        "breaking": [],
        "major": [],
        "minor": [],
        "informational": [],
    }
    for item in selected:
        sig = (item.get("significance") or "informational").lower()
        if sig in tiers:
            tiers[sig].append(item)
        else:
            tiers["informational"].append(item)

    bullets: list[str] = []
    for tier_name, items in tiers.items():
        if not items:
            continue
        n = len(items)
        if tier_name == "breaking":
            raw_summary = (
                items[0].get("summary")
                or items[0].get("excerpt")
                or items[0].get("title", "")
            )
            top_summary = raw_summary[:150] + ("..." if len(raw_summary) > 150 else "")
            bullets.append(f"BREAKING ({n}): {top_summary}")
        elif tier_name == "major":
            raw_summary = (
                items[0].get("summary")
                or items[0].get("excerpt")
                or items[0].get("title", "")
            )
            top_summary = raw_summary[:150] + ("..." if len(raw_summary) > 150 else "")
            bullets.append(f"Major update ({n} items): {top_summary}")
        else:
            # minor + informational: compact title list
            titles = "; ".join(item.get("title", "untitled") for item in items[:3])
            bullets.append(f"{n} {tier_name}: {titles}")

    # Single-tier subdivision: when all items share the same tier, subdivide by tag
    if len(bullets) <= 1 and len(selected) > 1:
        from collections import Counter

        tag_groups: dict[str, list[dict]] = {}
        for item in selected:
            tags = item.get("tags") or []
            tag = tags[0] if tags else "general"
            tag_groups.setdefault(tag, []).append(item)
        # Sort by group size descending, take top 5
        sorted_tags = sorted(tag_groups.items(), key=lambda x: -len(x[1]))[:5]
        if len(sorted_tags) > 1:
            bullets = []
            for tag, tag_items in sorted_tags:
                titles = "; ".join(
                    item.get("title", "untitled") for item in tag_items[:3]
                )
                bullets.append(f"{len(tag_items)} on {tag}: {titles}")

    # Cap at 5 bullets
    bullets = bullets[:5]

    return "\n".join([header] + [f"- {b}" for b in bullets])


def _build_bottom_line(topic: str | None, selected: list) -> str:
    """Generate a TL;DR that leads with the highest-significance item's summary.

    Leads with the top item's summary (actionable intelligence), then a count line.
    """
    n = len(selected)
    if n == 0:
        return f"No items found{f' for {topic}' if topic else ''}."

    # Lead with most significant item's summary
    top_item = selected[0]
    lead = (
        top_item.get("summary") or top_item.get("excerpt") or top_item.get("title", "")
    )
    # Truncate lead to ~200 chars for conciseness
    if len(lead) > 200:
        lead = lead[:197] + "..."

    breaking = sum(1 for item in selected if item.get("significance") == "breaking")
    breaking_note = f" ({breaking} breaking)" if breaking else ""

    topic_label = f" on {topic}" if topic else ""
    return f"{lead}\n\n{n} items{topic_label}{breaking_note}."


def _dedup_items(rows: list) -> list:
    """Two-layer deduplication: cluster_id then base-URL.

    Layer 1 — Cluster dedup: group by non-null cluster_id, keep highest
    relevance_score per cluster. Items with null cluster_id pass through.

    Layer 2 — URL-base dedup: for remaining items, strip query string and
    fragment from URL, keep highest relevance_score per base URL.

    Returns deduplicated list preserving original ordering.
    """
    # Layer 1: cluster dedup
    best_by_cluster: dict[str, dict] = {}
    after_cluster: list[dict] = []
    for row in rows:
        item = dict(row)
        cid = item.get("cluster_id")
        if cid is not None:
            cid_str = str(cid)
            existing = best_by_cluster.get(cid_str)
            if existing is None or (item.get("relevance_score") or 0) > (
                existing.get("relevance_score") or 0
            ):
                best_by_cluster[cid_str] = item
        else:
            after_cluster.append(item)

    # Collect cluster winners preserving original order
    seen_clusters: set[str] = set()
    for row in rows:
        item = dict(row)
        cid = item.get("cluster_id")
        if cid is not None:
            cid_str = str(cid)
            if cid_str not in seen_clusters:
                seen_clusters.add(cid_str)
                after_cluster.append(best_by_cluster[cid_str])

    # Restore original ordering (significance then score) by re-sorting
    # based on position in original rows
    row_order = {id(dict(r)): i for i, r in enumerate(rows)}
    # Use URL + score as proxy key for ordering since id() won't match
    original_order: dict[tuple, int] = {}
    for i, r in enumerate(rows):
        key = (r.get("url", ""), r.get("relevance_score", 0))
        if key not in original_order:
            original_order[key] = i

    after_cluster.sort(
        key=lambda item: original_order.get(
            (item.get("url", ""), item.get("relevance_score", 0)), 999
        )
    )

    # Layer 2: URL-base dedup
    best_by_url: dict[str, dict] = {}
    for item in after_cluster:
        url = item.get("url") or ""
        parsed = urllib.parse.urlparse(url)
        base_url = (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme else url
        )
        existing = best_by_url.get(base_url)
        if existing is None or (item.get("relevance_score") or 0) > (
            existing.get("relevance_score") or 0
        ):
            best_by_url[base_url] = item

    # Preserve original ordering for URL-deduped results
    seen_urls: set[str] = set()
    result: list[dict] = []
    for item in after_cluster:
        url = item.get("url") or ""
        parsed = urllib.parse.urlparse(url)
        base_url = (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme else url
        )
        if base_url not in seen_urls:
            seen_urls.add(base_url)
            result.append(best_by_url[base_url])

    return result


def _truncate_to_budget(rows: list, budget: int) -> tuple[list, int]:
    """Return (selected_items, cumulative_chars) fitting within budget."""
    budget_chars = budget * CHARS_PER_TOKEN
    cumulative = 0
    selected = []
    for item in rows:
        item_dict = dict(item)
        text = _format_item(item_dict)
        if cumulative + len(text) > budget_chars and selected:
            break
        selected.append(item_dict)
        cumulative += len(text)
    return selected, cumulative


@context_pack_router.get("/context-pack")
@limiter.limit("60/minute")
async def get_context_pack(
    request: StarletteRequest,
    response: StarletteResponse,
    topic: str
    | None = Query(
        None, description="Tag to filter items by. Omit for general briefing."
    ),
    budget: int = Query(
        2000, ge=100, le=16000, description="Max tokens (4 chars/token approx)"
    ),
    days: int = Query(14, ge=1, le=90, description="Recency window in days"),
    sort: str = Query(
        "significance",
        pattern="^(significance|score)$",
        description="Ordering: significance or score",
    ),
    format: str = Query(
        "text",
        pattern="^(text|json)$",
        description="Response format: text (plain) or json (structured)",
    ),
    include_library: bool = Query(
        False,
        description="Include evergreen library entries alongside feed items",
    ),
    compress: bool = Query(
        False,
        description="Return compressed bullet summary instead of full item list",
    ),
    library_budget: int = Query(
        0,
        ge=0,
        le=4000,
        description="Token budget for library entries (0 = auto 30% of total budget)",
    ),
    session: AsyncSession = Depends(get_session),
    api_key: APIKey = Depends(require_api_key),
):
    """
    Returns a token-budgeted intelligence briefing for agent system prompt injection.

    Default response is plain text (text/plain) for direct injection.
    Use ?format=json for structured metadata + items list.

    When topic is omitted, returns a general briefing across all topics by significance.
    When topic is provided, semantic expansion is applied via TAG_GROUPS (e.g. 'mcp'
    also matches 'model-context-protocol', 'mcp-server', 'mcp-client').

    Token budget is approximated as 4 characters per token (no LLM tokenizer required).
    At least 1 item is always returned even if it exceeds the budget.

    include_library=true injects evergreen library entries before feed items (library-first).
    Default split: 30% of budget for library, 70% for feed.
    """
    # Query logging BEFORE cache check — ensures every query is always counted
    try:
        await log_query(session, api_key.id, "context-pack", topic, 0)
    except Exception:
        pass

    # Response cache check
    _cache_key = None
    _cp_redis = get_redis_from_request(request)
    if is_cache_enabled() and _cp_redis:
        _cache_params = {
            "topic": topic,
            "budget": budget,
            "days": days,
            "sort": sort,
            "format": format,
            "compress": compress,
            "include_library": include_library,
            "library_budget": library_budget,
        }
        _cache_key = make_cache_key("context-pack", _cache_params)
        _cached = await get_cached_response(_cp_redis, _cache_key)
        if _cached is not None:
            if format == "json":
                return JSONResponse(content=_json_mod.loads(_cached))
            else:
                return Response(content=_cached, media_type="text/plain")

    # Resolve token budgets
    lib_token_budget = library_budget if library_budget > 0 else int(budget * 0.30)
    feed_token_budget = budget - lib_token_budget if include_library else budget

    # P1-10: Validate sort direction allowlist — safe: sort is validated by Query pattern,
    # not user-controlled, but explicit check prevents future contributors from introducing injection.
    if sort not in ("significance", "score"):
        sort = "significance"

    # Freshness-decayed ranking: 7-day half-life keeps briefings current
    # Quality boost: multiply by factor in [0.5, 1.0] so established items get up to 2x over unverified
    decay_expr = (
        "COALESCE(relevance_score, 0.5)"
        " * EXP(LN(0.5) / 7.0 * EXTRACT(EPOCH FROM (NOW() - COALESCE(published_at, created_at))) / 86400.0)"
        " * (0.5 + 0.5 * COALESCE(quality_score, 0.5))"
    )
    order_clause = (
        f"""CASE significance
            WHEN 'breaking' THEN 0 WHEN 'major' THEN 1 WHEN 'minor' THEN 2 ELSE 3
        END ASC, {decay_expr} DESC"""
        if sort == "significance"
        else f"{decay_expr} DESC"
    )

    # Build query params and WHERE clause based on topic presence + semantic expansion
    query_params: dict = {"days": days}
    use_semantic_order = False

    if topic is None:
        # General briefing: no tag filter — return top items across all topics
        tag_filter = ""
    else:
        tag_variants = TAG_GROUPS.get(topic)
        if tag_variants:
            # Semantic expansion: OR-match any variant in the group
            tag_filter = (
                "AND EXISTS (\n"
                "    SELECT 1 FROM jsonb_array_elements_text(CAST(tags AS jsonb)) t\n"
                "    WHERE t = ANY(:tag_variants)\n"
                ")"
            )
            query_params["tag_variants"] = tag_variants
        else:
            # Semantic fallback: embed the topic and filter by cosine distance
            # instead of exact tag match (which misses multi-word queries like "OpenAI Codex")
            topic_embedding = None
            try:
                topic_embedding = await _embed_concept(topic, request=request)
            except Exception:
                logger.debug("context_pack_embed_fallback", reason="embed failed")

            if topic_embedding is not None:
                embedding_str = "[" + ",".join(str(v) for v in topic_embedding) + "]"
                tag_filter = (
                    "AND embedding IS NOT NULL"
                    " AND embedding <=> CAST(:topic_embed AS vector) < 0.45"
                )
                query_params["topic_embed"] = embedding_str
                use_semantic_order = True
            else:
                # Last resort: exact tag match
                tag_filter = (
                    "AND CAST(tags AS jsonb) @> jsonb_build_array(CAST(:topic AS text))"
                )
                query_params["topic"] = topic

    # When using semantic embedding, prepend distance sort so closest matches surface first
    effective_order = (
        f"embedding <=> CAST(:topic_embed AS vector) ASC, {order_clause}"
        if use_semantic_order
        else order_clause
    )

    fetch_sql = text(
        f"""
        SELECT id, title, url, excerpt, summary, primary_type, tags,
               relevance_score, significance, source_name, published_at, created_at,
               cluster_id
        FROM intel_items
        WHERE status = 'processed'
          AND COALESCE(published_at, created_at) >= NOW() - INTERVAL '1 day' * :days
          AND relevance_score >= 0.50
          AND (COALESCE(quality_score, 0) >= 0.55
               OR COALESCE(published_at, created_at) >= NOW() - INTERVAL '7 days'
               OR significance IN ('breaking', 'major'))
          AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
          {tag_filter}
        ORDER BY {effective_order}
        LIMIT 200
        """
    )

    result = await session.execute(fetch_sql, query_params)
    rows = result.mappings().all()

    # Semantic fallback: if topic-based tag filter returned 0 items,
    # fall back to full-text search using topic as query
    if topic and len(rows) == 0:
        fallback_sql = text(
            f"""
            SELECT id, title, url, excerpt, summary, primary_type, tags,
                   relevance_score, significance, source_name, published_at, created_at,
                   cluster_id
            FROM intel_items
            WHERE status = 'processed'
              AND COALESCE(published_at, created_at) >= NOW() - INTERVAL '1 day' * :days
              AND relevance_score >= 0.50
              AND (COALESCE(quality_score, 0) >= 0.55
                   OR COALESCE(published_at, created_at) >= NOW() - INTERVAL '7 days'
                   OR significance IN ('breaking', 'major'))
              AND COALESCE(summary, '') NOT LIKE '%%unavailable%%'
              AND search_vector @@ plainto_tsquery('english', :search_topic)
            ORDER BY {order_clause}
            LIMIT 200
            """
        )
        result = await session.execute(
            fallback_sql, {"days": days, "search_topic": topic.replace("+", " ")}
        )
        rows = result.mappings().all()

    # Deduplicate by cluster_id and base URL before budget truncation
    deduped = _dedup_items(rows)
    selected, cumulative_chars = _truncate_to_budget(deduped, feed_token_budget)

    # ---------------------------------------------------------------------------
    # Library priming — fetch active library entries when include_library=True
    # ---------------------------------------------------------------------------
    library_entries: list[dict] = []
    lib_chars = 0
    if include_library:
        # Build library tag filter (same TAG_GROUPS expansion pattern)
        lib_tag_filter = ""
        lib_params: dict = {"lib_limit": 20}  # fetch extra, trim to budget

        if topic is not None:
            tag_variants = TAG_GROUPS.get(topic)
            if tag_variants:
                lib_tag_filter = (
                    "AND EXISTS (\n"
                    "    SELECT 1 FROM jsonb_array_elements_text(li.tags::jsonb) t\n"
                    "    WHERE t = ANY(:lib_tag_variants)\n"
                    ")"
                )
                lib_params["lib_tag_variants"] = tag_variants
            else:
                lib_tag_filter = (
                    "AND li.tags::jsonb @> jsonb_build_array(CAST(:lib_topic AS text))"
                )
                lib_params["lib_topic"] = topic

        # Profile boost: if user has tech_stack, prefer matching entries
        lib_sql = text(
            f"""
            SELECT
                li.slug, li.title, li.tldr,
                li.key_points, li.gotchas,
                li.graduation_score, li.helpful_count
            FROM library_items li
            WHERE li.status = 'active'
              AND li.is_current = TRUE
              {lib_tag_filter}
            ORDER BY li.graduation_score DESC, li.helpful_count DESC
            LIMIT :lib_limit
            """
        )
        lib_rows = (await session.execute(lib_sql, lib_params)).mappings().all()

        # Trim to library_budget using same char-based accounting
        lib_budget_chars = lib_token_budget * CHARS_PER_TOKEN
        for lib_row in lib_rows:
            import json as _json

            entry = {
                "title": lib_row["title"],
                "tldr": lib_row["tldr"],
                "key_points": lib_row["key_points"]
                if isinstance(lib_row["key_points"], list)
                else (
                    _json.loads(lib_row["key_points"]) if lib_row["key_points"] else []
                )
                or [],
                "gotchas": lib_row["gotchas"]
                if isinstance(lib_row["gotchas"], list)
                else (_json.loads(lib_row["gotchas"]) if lib_row["gotchas"] else [])
                or [],
            }
            entry_text = _format_library_entry(entry)
            if lib_chars + len(entry_text) > lib_budget_chars and library_entries:
                break
            library_entries.append(entry)
            lib_chars += len(entry_text)

    bottom_line = _build_bottom_line(topic, selected)

    if format == "json":
        meta = ContextPackMeta(
            topic=topic or "",
            budget_tokens=budget,
            items_included=len(selected),
            chars_used=cumulative_chars,
            tokens_estimated=cumulative_chars // CHARS_PER_TOKEN,
            days=days,
            generated_at=datetime.now(timezone.utc),
        )
        # Serialize items using IntelItemResponse-compatible fields (subset)
        items_out = []
        for item in selected:
            items_out.append(
                {
                    "id": str(item["id"]),
                    "title": item["title"],
                    "url": item["url"],
                    "summary": item.get("summary"),
                    "excerpt": item.get("excerpt"),
                    "primary_type": item["primary_type"],
                    "tags": item.get("tags") or [],
                    "significance": item.get("significance"),
                    "relevance_score": item.get("relevance_score"),
                    "quality_score": item.get("quality_score"),
                    "quality_score_details": item.get("quality_score_details"),
                    "source_name": item.get("source_name"),
                    "published_at": item["published_at"].isoformat()
                    if item.get("published_at")
                    else None,
                    "created_at": item["created_at"].isoformat()
                    if item.get("created_at")
                    else None,
                }
            )
        json_response: dict = {
            "bottom_line": bottom_line,
            "meta": meta.model_dump(mode="json"),
            "items": items_out if not compress else items_out[:3],
        }
        if compress:
            json_response["compressed_briefing"] = _compress_to_bullets(
                selected, topic, days
            )
        if include_library:
            json_response["library_priming"] = library_entries

        # Cache the JSON response on miss
        if _cache_key and _cp_redis:
            try:
                await set_cached_response(
                    _cp_redis,
                    _cache_key,
                    _json_mod.dumps(json_response, default=str),
                )
            except Exception:
                pass

        return JSONResponse(content=json_response)

    # Default: plain text for direct agent injection
    # Library section goes BEFORE feed (foundational knowledge first)
    lib_section = ""
    if include_library and library_entries:
        lib_section = "# Library Priming\n\n" + "".join(
            _format_library_entry(e) for e in library_entries
        )

    header = _build_header(topic, len(selected), budget, days, len(library_entries))
    if compress:
        body = _compress_to_bullets(selected, topic, days) + "\n\n"
    else:
        body = "".join(_format_item(item) for item in selected)
    footer = _build_footer(len(selected), days)
    briefing_text = f"TL;DR: {bottom_line}\n\n" + header + lib_section + body + footer

    # Cache the text response on miss
    if _cache_key and _cp_redis:
        try:
            await set_cached_response(_cp_redis, _cache_key, briefing_text)
        except Exception:
            pass

    return Response(content=briefing_text, media_type="text/plain")
