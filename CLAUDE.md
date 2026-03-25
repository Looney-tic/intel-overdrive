# Overdrive Intel

A persistent intelligence pipeline for AI-assisted development — monitors 1100+ sources for new tools, breaking changes, best practices, and patterns relevant to developers using Claude Code and other AI coding agents.

## What This Project Is For

The unique niche: the **intersection of general software development and AI coding tools**. Not "what's new in Next.js" (other tools cover that) but "what changed in Next.js that will cause an AI agent to generate outdated code." Three core needs this serves:

1. **"Did this API change since my training?"** — Breaking changes in SDKs/frameworks that cause agents to generate wrong code
2. **"Is there a tool/MCP/library that already solves this?"** — New tools, MCP servers, extensions that prevent agents from building from scratch
3. **"What's the current best practice for X?"** — Evolving workflows, prompt patterns, CLAUDE.md conventions, project structures that work well with agents

Users are developers building **everything** (web apps, APIs, databases, mobile) with AI coding tools. The most valuable intelligence is what changed in the general coding world that will cause an AI agent to generate outdated or wrong patterns.

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

Python 3.12 + FastAPI + ARQ/Redis + Postgres/pgvector + Voyage AI embeddings + Haiku classification. Dual-queue ARQ: fast (ingestion, max_jobs=50) + slow (LLM, max_jobs=5).

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
