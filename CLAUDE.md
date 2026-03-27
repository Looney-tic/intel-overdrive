# Intel Overdrive

## Prime Directive

Intel Overdrive gives AI coding agents **live intelligence** about the AI ecosystem — new tools, MCP servers, skills, frameworks, SDK updates, breaking changes — that are **not in the agent's training data**.

It works via a **skill + CLI** architecture: a skill tells the agent when to query, and the `intel-overdrive` CLI does the querying via Bash. No MCP server process needed, no restart, no configuration. The agent runs `intel-overdrive search "query"` whenever it encounters an AI ecosystem topic.

The agent either:

1. **Detects automatically** — when the user asks about tools, SDKs, or features, the skill triggers and the agent runs `intel-overdrive search` via Bash
2. **On-demand** — the user asks "what's new?", "what MCP servers exist for X?", "best agent framework?" and gets instant, quality-ranked results

This is **faster, cheaper, and more reliable** than web search. One CLI call vs 10-30s of Googling and scraping random pages. MCP server mode is available optionally via `intel-overdrive mcp-enable` for users who want the structured `overdrive_intel` tool in their tool list.

## What This Project Covers

The unique niche: the **intersection of general software development and AI coding tools**. Three core needs:

1. **"What new tools/MCPs/agents exist for X?"** — New MCP servers, Claude Code skills, agent frameworks, extensions. Quality-ranked with star counts so the agent recommends proven tools
2. **"Did this API change since my training?"** — Breaking changes in SDKs/frameworks that cause agents to generate wrong code
3. **"What's the current best practice for X?"** — Evolving workflows, prompt patterns, CLAUDE.md conventions, project structures that work well with agents

Users are developers building **everything** (web apps, APIs, databases, mobile) with AI coding tools. The most valuable intelligence is what's new and what changed that the agent doesn't know about yet.

## Source Selection Rules

When evaluating whether to add a new source, apply these filters:

1. **We track changes, not documentation.** Static docs are served by tools like Context7. We only ingest a source if it tells us **what changed** — release notes, changelogs, breaking changes, new patterns, deprecations. A sitemap of 700 doc pages is noise; a release feed for the same project is signal.
2. **We track the intersection, not the general.** Not "what's new in Next.js" but "what in Next.js will cause an AI agent to generate wrong code." General programming news belongs elsewhere.
3. **Preferred source types (in order):** GitHub release Atom feeds > RSS/Atom blogs > Sitemap (only for sites with no RSS that publish changes as page edits, e.g., protocol specs) > Scraper (last resort).
4. **Sitemaps are only justified when:** the site has no RSS AND publishes high-value changing content as doc pages (e.g., MCP protocol spec, curated best-practice wikis like ClaudeLog). Never use sitemaps for static documentation.
5. **Every source must have a verified, working feed URL.** Test with HTTP request before adding.

## Commands

- `docker compose up -d` — start Postgres (pgvector) + Redis
- `python -m pytest` — run full test suite (requires Docker services)
- `alembic upgrade head` — apply migrations

## Architecture

**Backend:** Python 3.12 + FastAPI + ARQ/Redis + Postgres/pgvector + Voyage AI embeddings + Haiku classification. Dual-queue ARQ: fast (ingestion, max_jobs=50) + slow (LLM, max_jobs=5).

**CLI + MCP package:** TypeScript (ESM) at `overdrive-intel-mcp/`. Single binary serves as both CLI tool (`intel-overdrive search/feed/breaking/setup`) and MCP stdio server (no args). Published to npm as `intel-overdrive`. Zero npm deps beyond `@modelcontextprotocol/sdk`.

**Distribution:** Skill at `agent-skills/` (published to `Looney-tic/agent-skills` on GitHub/skills.sh). Skill teaches agents when to query; CLI does the querying via Bash. MCP server mode optional via `intel-overdrive mcp-enable`.

## Code Style

- SQLAlchemy DeclarativeBase (not SQLModel)
- `import src.core.init_db as _db` pattern for runtime attribute access (not from-import)
- Structured logging via `get_logger(__name__)`

## Testing

pytest + pytest-asyncio. Function-scoped async fixtures. Docker required (Postgres 5434, Redis 6381).

## Visual Testing

CLI only — no browser testing

## Environment

DATABASE_URL, REDIS_URL, ANTHROPIC_API_KEY, VOYAGE_API_KEY, GITHUB_TOKEN (optional), MAILGUN_WEBHOOK_SIGNING_KEY (required in production). OpenAPI docs available in development only (ENVIRONMENT != production).

## Gotchas

- **Async fixtures must be function-scoped**: Session-scoped async fixtures cause "Future attached to different loop" with asyncpg. Always use `scope="function"` for async engine fixtures.
- **Never call session.expire_all() in async tests**: Triggers MissingGreenlet with asyncpg. Use `.execution_options(populate_existing=True)` on SELECT queries instead, or rely on `expire_on_commit=False` for in-memory state.
- **Raw SQL UPDATE doesn't invalidate ORM identity map**: After `safe_transition` or raw `text()` UPDATE, ORM SELECT returns stale cached objects. Same fix: `.execution_options(populate_existing=True)` on the SELECT.
- **get_settings() lru_cache in tests**: `get_settings()` is `@lru_cache`. If you change `os.environ["DATABASE_URL"]` between tests, the cached Settings object still holds the old value. Always call `get_settings.cache_clear()` after changing env vars in tests.
