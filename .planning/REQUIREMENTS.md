# Requirements: Overdrive Intel

**Defined:** 2026-03-14
**Core Value:** Developers and their AI agents get curated, relevant ecosystem intelligence without noise

## v1 Requirements

### Foundation

- [x] **FOUND-01**: Postgres schema with all models (IntelItem, Source, User, APIKey, AlertRule, ReferenceItem) with pgvector embedding column
- [x] **FOUND-02**: Alembic migration infrastructure with async recipe
- [x] **FOUND-03**: Docker Compose for local dev (Postgres + Redis)
- [x] **FOUND-04**: Core dedup service (3-layer: URL hash, content fingerprint, embedding similarity)
- [x] **FOUND-05**: Spend tracker service (Redis INCRBYFLOAT, hard daily limit, mandatory pre-flight gate)
- [x] **FOUND-06**: LLM client wrapper (Haiku via Anthropic SDK, spend-gated, enum coercion)
- [x] **FOUND-07**: Structured logging (structlog) and pydantic-settings config
- [x] **FOUND-08**: API key model with SHA-256 hashing, versioned format (`dti_v1_`), per-key usage counter

### Ingestion

- [x] **INGEST-01**: ARQ dual-queue setup (fast queue max_jobs=50, slow queue max_jobs=5)
- [x] **INGEST-02**: RSS/Atom source adapter with conditional GET (ETag/Last-Modified)
- [x] **INGEST-03**: GitHub Search API adapter with rate limit awareness (30 req/min)
- [x] **INGEST-04**: Per-source circuit breaker (consecutive_errors, last_successful_poll, dead-source detection)
- [x] **INGEST-05**: Per-source cooldown via Redis SET NX with TTL
- [x] **INGEST-06**: Source health tracking (last_successful_poll, consecutive_empty_fetches)
- [x] **INGEST-07**: Hacker News adapter via Algolia API (free, no auth)
- [x] **INGEST-08**: Reddit adapter via public RSS endpoints
- [x] **INGEST-09**: MCP Registry adapter (REST API, treated as secondary source)
- [x] **INGEST-10**: npm registry adapter (keyword search for claude/mcp/agent packages)
- [x] **INGEST-11**: Claude Code GitHub releases adapter (Atom feed)
- [x] **INGEST-12**: YouTube adapter (RSS feeds for Claude Code channels)
- [x] **INGEST-13**: Releasebot adapter (Anthropic, OpenAI, GitHub releases)
- [x] **INGEST-14**: awesome-claude-code repo adapter (git diff for new entries)

### Pipeline

- [x] **PIPE-01**: Reference set seeding script (50-100 manually curated gold-standard items, embedded)
- [x] **PIPE-02**: Embedding service using Voyage AI (voyage-3.5-lite, 1024-dim)
- [x] **PIPE-03**: Relevance gate worker (cosine similarity against reference set, queued/filtered routing)
- [x] **PIPE-04**: LLM classification worker (Haiku, primary type + tags, confidence score, spend-gated)
- [x] **PIPE-05**: Status state machine enforced (raw → embedded → queued|filtered → processing → processed|failed)
- [x] **PIPE-06**: Classification taxonomy: primary type (skill|tool|update|practice|docs) + freeform tags
- [x] **PIPE-07**: Relevance scoring formula (content_match 0.40 + authority 0.25 + engagement 0.20 + freshness 0.15)

### Server

- [x] **API-01**: FastAPI server with API versioning (`/v1/` prefix)
- [x] **API-02**: API key auth middleware (SHA-256 comparison, per-key usage logging)
- [x] **API-03**: GET /v1/feed endpoint (filtering by type, tag, days, with pagination)
- [x] **API-04**: GET /v1/search endpoint (full-text search across title + excerpt + summary)
- [x] **API-05**: GET /v1/info/:id endpoint (full item detail with quality score)
- [x] **API-06**: GET /v1/status endpoint (pipeline health, per-source last-poll, spend remaining)
- [x] **API-07**: GET /v1/health endpoint (last ingestion timestamp for monitoring)
- [x] **API-08**: POST /v1/profile endpoint (receive user profile with tech stack + skill inventory)
- [x] **API-09**: POST /v1/feedback endpoint (miss/noise reports with item reference)
- [x] **API-10**: Server-side prefiltering based on user profile (filter feed results by profile match)
- [x] **API-11**: Rate limiting per API key (Redis-backed via slowapi)

### CLI

- [x] **CLI-01**: pipx-installable `overdrive-intel` package
- [x] **CLI-02**: `overdrive-intel feed` command with --days, --type, --tag filters
- [x] **CLI-03**: `overdrive-intel search <query>` command
- [x] **CLI-04**: `overdrive-intel info <name-or-url>` command
- [x] **CLI-05**: `overdrive-intel status` command (pipeline health, spend, source status)
- [x] **CLI-06**: `overdrive-intel profile --sync` command (reads local ~/.claude/ dirs, sends to server, opt-in)
- [x] **CLI-07**: `overdrive-intel auth login` and `auth status` commands
- [x] **CLI-08**: TTY detection — rich terminal tables for humans, JSON for agents
- [x] **CLI-09**: `--json` flag always available for explicit JSON output
- [x] **CLI-10**: API key storage (keyring → env var → ~/.config/overdrive-intel/key fallback)
- [x] **CLI-11**: Empty-state handling (0 results suggests --all or wider filters)

### Alerting

- [x] **ALERT-01**: Alert rule model (name, keywords_csv, cooldown_minutes, is_active, delivery_channels)
- [x] **ALERT-02**: Keyword-based alert matching (case-insensitive substring)
- [x] **ALERT-03**: Redis-based per-rule cooldown (SET NX with TTL)
- [x] **ALERT-04**: Slack webhook delivery
- [x] **ALERT-05**: Breaking change classification (breaking vs non-breaking from changelogs)
- [x] **ALERT-06**: `overdrive-intel alerts set-slack <webhook>` CLI command
- [x] **ALERT-07**: `overdrive-intel alerts status` CLI command
- [x] **ALERT-08**: Urgency tiers (critical/important/interesting) labeled on deliveries; all delivered immediately in v1

### Quality Scoring

- [x] **QUAL-01**: Static quality signals for GitHub-backed items (recent commits, star/fork activity, license, archive status, subscribers)
- [x] **QUAL-02**: Safe pattern detection (no dangerous shell execution, no hardcoded paths)
- [x] **QUAL-03**: Staleness detection (no commits in 6 months → flag)
- [x] **QUAL-04**: Transparent component scores exposed in API (Security, Maintenance, Compatibility)

### Feedback

- [x] **FEED-01**: `overdrive-intel feedback miss <url>` command (false negative reporting)
- [x] **FEED-02**: `overdrive-intel feedback noise <id>` command (false positive reporting)
- [x] **FEED-03**: Feedback stored server-side, queryable for classification improvement

### Operations

- [x] **OPS-01**: Docker Compose production deployment (API + fast worker + slow worker + Redis)
- [x] **OPS-02**: Managed Postgres with automated backups (Neon or Supabase)
- [x] **OPS-03**: Spend gating mandatory with hard daily limit (default $10)
- [x] **OPS-04**: Structured logging with JSON output for production
- [x] **OPS-05**: Dead man's switch — alert if no ingestion runs for >24h

## v2 Requirements

### Security Scanning

- **SEC-01**: Rule-based security scanner for skills (prompt injection, credential exfiltration, supply chain patterns)
- **SEC-02**: False-positive dispute process (author notification, appeal workflow)
- **SEC-03**: Re-scanning of tracked items when new commits detected
- **SEC-04**: `overdrive-intel scan <repo-url>` for on-demand client-side scans

### Personalization

- **PERS-01**: Personalized breaking change impact analysis ("2 of your skills affected")
- **PERS-02**: Collaborative intelligence ("users like you also use X")
- **PERS-03**: Automated skill testing in Docker sandboxes

### Monetization

- **MON-01**: Self-service signup web page
- **MON-02**: Stripe checkout integration (subscription tiers)
- **MON-03**: x402 protocol support (agent micropayments per request)
- **MON-04**: Free tier rate limits vs paid tier full access

### Extensions (v2)

- **EXTV2-01**: Competitive landscape monitoring (Cursor, Windsurf, Copilot, Aider)
- **EXTV2-02**: VS Code extension (client)
- **EXTV2-03**: Web dashboard UI

### Extended Ingestion (Phase 12)

- [ ] **EXT-01**: PyPI adapter — track configured Python packages by version via per-package JSON API
- [x] **EXT-02**: CHANGELOG.md diffing — SHA-based change detection in watched files within deep GitHub analyzer
- [ ] **EXT-03**: Newsletter email ingest — Mailgun webhook with HMAC verification, replay prevention, text extraction
- [ ] **EXT-04**: Sitemap ingest — XML sitemap parsing with sitemapindex recursion, page fetching, HTML content extraction
- [ ] **EXT-05**: Bluesky adapter — AT Protocol via atproto SDK with Redis session caching, account feed + keyword search
- [ ] **EXT-06**: GitHub Discussions — GraphQL API adapter for discussion threads from configured repositories
- [ ] **EXT-07**: VS Code Marketplace — POST-based extension search with keyword queries and version tracking
- [x] **EXT-08**: Feed autodiscovery — detect RSS, Atom, JSON Feed types from URL (utility service)
- [ ] **EXT-09**: Expanded source seeds — seed script for all new adapter types with initial source configurations

## Out of Scope

| Feature                              | Reason                                                                                   |
| ------------------------------------ | ---------------------------------------------------------------------------------------- |
| Web UI for v1                        | CLI is the interface for target audience. Web UI doubles surface area without PMF signal |
| "All developer tools" scope          | Destroys positioning. daily.dev covers general dev tools already                         |
| Real-time streaming for all updates  | Batch polling. Real-time delivery of batch data is misleading                            |
| Self-service signup at launch        | Invite-only allows faster iteration without billing edge cases                           |
| Security scanning in v1              | False-positive dispute process needed. Reputation risk too high                          |
| Social features / community platform | This is a utility, not a social network                                                  |

## Traceability

| Requirement | Phase                                 | Status   |
| ----------- | ------------------------------------- | -------- |
| FOUND-01    | Phase 1: Foundation                   | Complete |
| FOUND-02    | Phase 1: Foundation                   | Complete |
| FOUND-03    | Phase 1: Foundation                   | Complete |
| FOUND-04    | Phase 1: Foundation                   | Complete |
| FOUND-05    | Phase 1: Foundation                   | Complete |
| FOUND-06    | Phase 1: Foundation                   | Complete |
| FOUND-07    | Phase 1: Foundation                   | Complete |
| FOUND-08    | Phase 1: Foundation                   | Complete |
| INGEST-01   | Phase 2: Ingestion Core               | Complete |
| INGEST-02   | Phase 2: Ingestion Core               | Complete |
| INGEST-03   | Phase 2: Ingestion Core               | Complete |
| INGEST-04   | Phase 2: Ingestion Core               | Complete |
| INGEST-05   | Phase 2: Ingestion Core               | Complete |
| INGEST-06   | Phase 2: Ingestion Core               | Complete |
| PIPE-01     | Phase 3: Pipeline Core                | Complete |
| PIPE-02     | Phase 3: Pipeline Core                | Complete |
| PIPE-03     | Phase 3: Pipeline Core                | Complete |
| PIPE-04     | Phase 3: Pipeline Core                | Complete |
| PIPE-05     | Phase 3: Pipeline Core                | Complete |
| PIPE-06     | Phase 3: Pipeline Core                | Complete |
| PIPE-07     | Phase 3: Pipeline Core                | Complete |
| API-01      | Phase 4: Server + Auth                | Complete |
| API-02      | Phase 4: Server + Auth                | Complete |
| API-03      | Phase 4: Server + Auth                | Complete |
| API-04      | Phase 4: Server + Auth                | Complete |
| API-05      | Phase 4: Server + Auth                | Complete |
| API-06      | Phase 4: Server + Auth                | Complete |
| API-07      | Phase 4: Server + Auth                | Complete |
| API-08      | Phase 4: Server + Auth                | Complete |
| API-09      | Phase 4: Server + Auth                | Complete |
| API-10      | Phase 4: Server + Auth                | Complete |
| API-11      | Phase 4: Server + Auth                | Complete |
| CLI-01      | Phase 5: CLI                          | Complete |
| CLI-02      | Phase 5: CLI                          | Complete |
| CLI-03      | Phase 5: CLI                          | Complete |
| CLI-04      | Phase 5: CLI                          | Complete |
| CLI-05      | Phase 5: CLI                          | Complete |
| CLI-06      | Phase 5: CLI                          | Complete |
| CLI-07      | Phase 5: CLI                          | Complete |
| CLI-08      | Phase 5: CLI                          | Complete |
| CLI-09      | Phase 5: CLI                          | Complete |
| CLI-10      | Phase 5: CLI                          | Complete |
| CLI-11      | Phase 5: CLI                          | Complete |
| ALERT-01    | Phase 6: Alerting, Quality + Feedback | Complete |
| ALERT-02    | Phase 6: Alerting, Quality + Feedback | Complete |
| ALERT-03    | Phase 6: Alerting, Quality + Feedback | Complete |
| ALERT-04    | Phase 6: Alerting, Quality + Feedback | Complete |
| ALERT-05    | Phase 6: Alerting, Quality + Feedback | Complete |
| ALERT-06    | Phase 6: Alerting, Quality + Feedback | Complete |
| ALERT-07    | Phase 6: Alerting, Quality + Feedback | Complete |
| ALERT-08    | Phase 6: Alerting, Quality + Feedback | Complete |
| QUAL-01     | Phase 6: Alerting, Quality + Feedback | Complete |
| QUAL-02     | Phase 6: Alerting, Quality + Feedback | Complete |
| QUAL-03     | Phase 6: Alerting, Quality + Feedback | Complete |
| QUAL-04     | Phase 6: Alerting, Quality + Feedback | Complete |
| FEED-01     | Phase 6: Alerting, Quality + Feedback | Complete |
| FEED-02     | Phase 6: Alerting, Quality + Feedback | Complete |
| FEED-03     | Phase 6: Alerting, Quality + Feedback | Complete |
| INGEST-07   | Phase 7: Additional Source Adapters   | Complete |
| INGEST-08   | Phase 7: Additional Source Adapters   | Complete |
| INGEST-09   | Phase 7: Additional Source Adapters   | Complete |
| INGEST-10   | Phase 7: Additional Source Adapters   | Complete |
| INGEST-11   | Phase 7: Additional Source Adapters   | Complete |
| INGEST-12   | Phase 7: Additional Source Adapters   | Complete |
| INGEST-13   | Phase 7: Additional Source Adapters   | Complete |
| INGEST-14   | Phase 7: Additional Source Adapters   | Complete |
| OPS-01      | Phase 8: Operations                   | Complete |
| OPS-02      | Phase 8: Operations                   | Complete |
| OPS-03      | Phase 8: Operations                   | Complete |
| OPS-04      | Phase 8: Operations                   | Complete |
| OPS-05      | Phase 8: Operations                   | Complete |
| DIST-01     | Phase 16: Distribution & Discovery    | Planned  |
| DIST-02     | Phase 16: Distribution & Discovery    | Planned  |
| DIST-03     | Phase 16: Distribution & Discovery    | Planned  |
| DIST-04     | Phase 16: Distribution & Discovery    | Planned  |
| DIST-05     | Phase 16: Distribution & Discovery    | Planned  |
| DIST-06     | Phase 16: Distribution & Discovery    | Planned  |

### Distribution & Discovery (Phase 16)

- [x] **DIST-01**: pip package — fix pyproject.toml src-layout with hatchling, publish to PyPI, `pip install overdrive-intel` produces working CLI
- [x] **DIST-02**: npm MCP server — TypeScript HTTP API client package `overdrive-intel-mcp` with all 6 tools, publishable via `npx`
- [x] **DIST-03**: MCP registry submission — server.json for mcp-publisher + Smithery web form (requires production deploy + published npm package)
- [x] **DIST-04**: CLAUDE.md auto-inject hook — SessionStart hook detects AI/MCP projects, injects overdrive-intel context, `overdrive-intel hook install` CLI command
- [x] **DIST-05**: Slack daily digest — ARQ cron worker posting formatted digest blocks to configured webhook URL
- [x] **DIST-06**: GitHub Action — composite action for CI breaking change detection using project dependency list

**Coverage:**

- v1 requirements: 87 total (8 FOUND + 14 INGEST + 7 PIPE + 11 API + 11 CLI + 8 ALERT + 4 QUAL + 3 FEED + 5 OPS + 6 DIST + 5 RANK + 10 DIST-Phase35)
- Mapped to phases: 77
- Unmapped: 0

### Search & Ranking Quality

- [x] **RANK-01**: `type=breaking` in MCP tool returns items with `significance IN ('breaking', 'major')` matching the query — expanding beyond only breaking-classified items
- [x] **RANK-02**: `/v1/action-items` endpoint accepts a `q` query parameter and filters results by query relevance when provided
- [x] **RANK-03**: Feed endpoint applies source-type diversity caps (max N items per source_type) before final ranking to prevent social post flooding
- [x] **RANK-04**: RRF search quality weight increased from 0.25 to 0.35 (semantic 0.25, fulltext 0.35, quality 0.35, freshness 0.05) so established tools outrank niche repos
- [x] **RANK-05**: `/v1/feed` and `/v1/search` endpoints accept a `source` filter parameter to scope results to a specific source ID

### Unified CLI + Skills.sh Distribution (Phase 35)

- [x] **DIST-CLI-01**: Unified `overdrive-intel` npm package (replaces `intel-overdrive-mcp`) as both CLI and MCP server
- [ ] **DIST-CLI-02**: `overdrive-intel setup` provisions API key + registers MCP + installs SKILL.md
- [ ] **DIST-CLI-03**: `overdrive-intel search/feed/breaking` human-facing CLI commands
- [x] **DIST-CLI-04**: Same binary serves as MCP server in stdio mode (no args)
- [x] **DIST-SKILLS-01**: `Looney-tic/agent-skills` GitHub repo content with agent-neutral SKILL.md
- [x] **DIST-SKILLS-02**: SKILL.md rewritten to be agent-neutral (no ToolSearch, no deferred-tool)
- [ ] **DIST-FIX-01**: Fix C-2: replace tgz install with npm install from registry
- [x] **DIST-FIX-02**: Fix H-2: standardize on `OVERDRIVE_INTEL_API_KEY` everywhere; legacy fallback
- [x] **DIST-FIX-03**: Fix H-6: single source of truth for version (read from package.json at runtime)
- [x] **DIST-FIX-04**: Fix M-3: plain-text error messages instead of JSON.stringify wrapping

---

_Requirements defined: 2026-03-14_
_Last updated: 2026-03-27 after Phase 35 addition_
