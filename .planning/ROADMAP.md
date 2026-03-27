# Roadmap: Overdrive Intel

## Overview

Overdrive Intel is built in eight phases that respect strict pipeline dependencies. Foundation and core services come first because every subsequent phase depends on them. Ingestion infrastructure feeds the pipeline; the pipeline must exist before the API can serve results; the CLI wraps the API; enrichment layers (alerting, quality scoring, feedback) extend the running system; remaining source adapters expand breadth after quality is validated; and operations hardens everything for production. The build order is dictated by the pipeline's own dependency graph — nothing is arbitrary.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - Data models, core services, and dev infrastructure (completed 2026-03-14)
- [x] **Phase 2: Ingestion Core** - Tier 1 source adapters, dual-queue ARQ, circuit breakers (completed 2026-03-14)
- [x] **Phase 3: Pipeline Core** - Reference set, relevance gate, LLM classification, scoring (completed 2026-03-14)
- [x] **Phase 4: Server + Auth** - FastAPI server with all endpoints, API key auth, rate limiting (completed 2026-03-14)
- [x] **Phase 5: CLI** - pipx-installable overdrive-intel client, all commands, TTY detection (completed 2026-03-14)
- [ ] **Phase 6: Alerting, Quality + Feedback** - Alert engine, quality scoring, feedback mechanism
- [x] **Phase 7: Additional Source Adapters** - Tier 2/3 adapters expanding to 10-15 sources (completed 2026-03-15)
- [x] **Phase 8: Operations** - Production deployment, managed Postgres, monitoring, dead man's switch (completed 2026-03-15)
- [x] **Phase 19: Evaluation Fixes** - Resolve all critical evaluation findings (completed 2026-03-20)
- [x] **Phase 20: Content Coverage Fixes** - Fix dormant sources, enrich release titles, expand reference set, add RAG/embedding sources, per-source thresholds (completed 2026-03-20)
- [x] **Phase 24: Search Quality Overhaul** - Hybrid RRF search, cluster dedup, phrase proximity, confidence thresholds, structured markdown MCP responses (completed 2026-03-22)
- [x] **Phase 25: Search Quality & Pipeline Fixes** - Fix pipeline stall, noise filter, content quality gate, library embeddings/search, cross-source dedup, source hygiene, test flakiness (completed 2026-03-22)
- [x] **Phase 26: Production Deep Dive Fixes** - Fix 30+ verified issues from 6-agent deep dive: P0 critical bugs, P1 security/correctness, P2 improvements (completed 2026-03-24)
- [x] **Phase 27: System Audit Fixes** - Context-pack quality floor, integer overflow protection, alert significance, search field selector, similarity threshold, breaking classification prompt (3 plans) (completed 2026-03-24)
- [x] **Phase 28: Search & Feed Quality Overhaul** - Briefing compression, relevance score recalibration, feed source ranking, zero-result coverage metadata (3 plans) (completed 2026-03-24)
- [x] **Phase 29: Round 3 Audit Fixes** - P0 admin auth exposure, P1 context-pack budget/compression, breaking false relevance, feed dedup, P2 multi-tag, library duplicates, production investigation (4 plans) (completed 2026-03-25)

## Phase Details

### Phase 1: Foundation

**Goal**: Schema, core services, and dev infrastructure exist so every subsequent phase can build on a stable base
**Depends on**: Nothing (first phase)
**Requirements**: FOUND-01, FOUND-02, FOUND-03, FOUND-04, FOUND-05, FOUND-06, FOUND-07, FOUND-08
**Success Criteria** (what must be TRUE):

1. `docker compose up` starts Postgres with pgvector and Redis locally with no manual steps
2. All database models (IntelItem, Source, User, APIKey, AlertRule, ReferenceItem) exist in schema with pgvector embedding column; Alembic migration applies cleanly from scratch
3. An API key can be created, hashed with SHA-256, and validated — per-key usage counter increments on each use
4. The spend tracker refuses a classification call when the daily limit is reached (hard gate, not a warning)
5. The LLM client wrapper calls Haiku and returns a structured response; a second call after limit is hit raises SpendLimitExceeded
   **Plans**: 3 plans in 3 waves

Plans:

- [ ] 01-01 (Wave 1): Project scaffold — pyproject.toml, fix models (nullable embeddings, correct types, new fields), fix migration (CREATE EXTENSION vector), config production validator, idempotent logging
- [ ] 01-02 (Wave 2): Core services — spend_tracker (SpendLimitExceeded exception, Lua atomic, integer cents), llm_client (LLMResponse dataclass, spend-gated), dedup (3-layer with embedding cosine), auth (timing-safe SHA-256, atomic usage counter)
- [ ] 01-03 (Wave 3): Test suite — conftest with DB/Redis fixtures, tests for all 8 FOUND-XX requirements

### Phase 2: Ingestion Core

**Goal**: Items flow from Tier 1 sources through the ingestion pipeline with deduplication and source health tracking
**Depends on**: Phase 1
**Requirements**: INGEST-01, INGEST-02, INGEST-03, INGEST-04, INGEST-05, INGEST-06
**Success Criteria** (what must be TRUE):

1. Running the ARQ worker ingests items from an RSS/Atom feed; duplicate URLs are rejected at the URL-hash layer without DB writes
2. Running the ARQ worker ingests items from the GitHub Search API; rate limit awareness prevents 429 errors
3. A source that fails 5 consecutive times is marked as dead and no longer polled; `last_successful_poll` is current for healthy sources
4. Per-source cooldown via Redis SET NX prevents a source from being polled more frequently than its configured interval
5. The `fast` queue and `slow` queue run independently; a stalled slow-queue job does not block fast-queue ingestion
   **Plans**: 3 plans in 3 waves

Plans:

- [ ] 02-01 (Wave 1): Infrastructure — Alembic migration (Source columns), config (GITHUB_TOKEN), dependencies (feedparser+tenacity), ARQ WorkerSettings (dual-queue), source_health service (circuit breaker + cooldown), feed_fetcher service (conditional GET + GitHub API)
- [ ] 02-02 (Wave 2): Adapters — RSS/Atom ingest worker (cron dispatcher + per-source job with feedparser), GitHub Search ingest worker (rate limit awareness, configurable queries), wire into WorkerSettings
- [ ] 02-03 (Wave 3): Tests — test_workers (INGEST-01), test_source_health (INGEST-04/05/06), test_ingest_rss (INGEST-02), test_ingest_github (INGEST-03), full suite validation

### Phase 3: Pipeline Core

**Goal**: Ingested items are filtered for relevance, classified by type, and scored — only items passing the relevance gate reach LLM classification
**Depends on**: Phase 2
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PIPE-04, PIPE-05, PIPE-06, PIPE-07
**Success Criteria** (what must be TRUE):

1. The reference set seed script runs and stores 50-100 manually curated items with embeddings; the relevance gate routes items to `queued` or `filtered` based on cosine similarity
2. An item ingested from a relevant source reaches `processed` status with a primary_type (one of: skill, tool, update, practice, docs) and freeform tags within the pipeline run cycle
3. An item that scores below the relevance threshold never reaches LLM classification — spend_tracker confirms zero Haiku calls for filtered items
4. The status state machine is enforced: `raw → embedded → queued|filtered → processing → processed|failed`; no item skips a state
5. Classification recall on the labeled eval set (200 items) is ≥90% before the pipeline is considered production-ready
   **Plans**: 4 plans in 3 waves

Plans:

- [x] 03-01 (Wave 1): Pipeline services — relevance gate (pgvector cosine, pos/neg weighting), scoring service (4-component formula), pipeline helpers (safe_transition, build_embed_input)
- [x] 03-02 (Wave 1): Reference set seed script — 100 curated items (~70 positive, ~30 negative), idempotent, embedded via Voyage AI
- [x] 03-03 (Wave 2): Pipeline workers — embed_items, gate_relevance, classify_items ARQ cron jobs on slow queue with staggered schedules
- [ ] 03-04 (Wave 3): Eval harness — 200-item labeled eval set (disjoint from reference set) + recall measurement script (>=90% gate)

### Phase 4: Server + Auth

**Goal**: A running FastAPI server exposes all v1 endpoints with API key auth, rate limiting, and profile-based prefiltering
**Depends on**: Phase 3
**Requirements**: API-01, API-02, API-03, API-04, API-05, API-06, API-07, API-08, API-09, API-10, API-11
**Success Criteria** (what must be TRUE):

1. `GET /v1/feed` returns paginated results filterable by type, tag, and days; server-side prefiltering applies when a user profile exists
2. `GET /v1/search` returns results matching full-text query across title, excerpt, and summary
3. A request with an invalid API key receives 401; a request that exceeds the per-key rate limit receives 429
4. `GET /v1/status` shows pipeline health with per-source last-poll timestamps and daily spend remaining
5. `POST /v1/feedback` stores a miss or noise report server-side; `GET /v1/health` returns last ingestion timestamp
   **Plans**: 4 plans in 3 waves

Plans:

- [ ] 04-01 (Wave 1): App skeleton — FastAPI app factory with lifespan, deps (get_session, get_redis, require_api_key), slowapi limiter, Pydantic schemas, Alembic migration (tsvector GIN + Feedback table)
- [ ] 04-02 (Wave 2): Core read endpoints — /feed (pagination + type/tag/days filtering), /search (tsvector full-text), /info/{id}, with auth + rate limiting
- [ ] 04-03 (Wave 2): Operational endpoints — /status (source health + spend), /health (last ingestion), /profile (upsert), /feedback (miss/noise), + feed prefiltering
- [ ] 04-04 (Wave 3): Test suite — ~25 tests covering all API-01 through API-11 requirements with httpx AsyncClient

### Phase 5: CLI

**Goal**: Developers and AI agents can interact with the service via a pipx-installable CLI with rich output for humans and JSON output for agents
**Depends on**: Phase 4
**Requirements**: CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, CLI-06, CLI-07, CLI-08, CLI-09, CLI-10, CLI-11
**Success Criteria** (what must be TRUE):

1. `pipx install overdrive-intel` succeeds and `overdrive-intel --help` shows all commands
2. `overdrive-intel feed` renders a rich terminal table when run in a TTY; the same command piped to a file outputs newline-delimited JSON
3. `overdrive-intel auth login` stores the API key via keyring (falling back to env var, then ~/.config file); `auth status` confirms authentication
4. `overdrive-intel profile --sync` reads ~/.claude/ directories, detects tech stack and skill inventory, and sends to the server (only after explicit opt-in prompt)
5. When a query returns 0 results, the CLI shows a suggestion to use wider filters (e.g., `--days 30`) rather than a bare empty state
   **Plans**: 4 plans in 3 waves

Plans:

- [ ] 05-01 (Wave 1): CLI scaffold — pyproject.toml entry point + deps (typer[all], keyring), src/cli/ package (main.py, client.py, config.py, render.py), TTY detection, --json flag, three-tier API key fallback
- [ ] 05-02 (Wave 2): Core read commands — feed (--days, --type, --tag), search <query>, info <name-or-url> (UUID detection + search-then-fetch), status (sources + spend + health)
- [ ] 05-03 (Wave 2): Auth, profile, feedback — auth login/status, profile --sync (opt-in scanner), feedback miss/noise
- [ ] 05-04 (Wave 3): Test suite — 9 test files covering all CLI-01 through CLI-11 with mocked HTTP and keyring

### Phase 6: Alerting, Quality + Feedback

**Goal**: Users receive keyword-triggered alerts via Slack, items carry transparent quality scores, and false positive/negative reports are stored for classification improvement
**Depends on**: Phase 4
**Requirements**: ALERT-01, ALERT-02, ALERT-03, ALERT-04, ALERT-05, ALERT-06, ALERT-07, ALERT-08, QUAL-01, QUAL-02, QUAL-03, QUAL-04, FEED-01, FEED-02, FEED-03
**Success Criteria** (what must be TRUE):

1. A user sets a keyword alert rule; when a matching item is classified, a Slack webhook notification fires within the next pipeline cycle
2. A keyword rule that has fired does not re-fire within its configured cooldown window (Redis SET NX prevents duplicate alerts)
3. A breaking change item triggers a critical-tier alert; all urgency tiers (critical/important/interesting) are labeled on deliveries and delivered immediately in v1
4. A GitHub-backed item exposes transparent quality sub-scores (Security, Maintenance, Compatibility) visible in `overdrive-intel info <id>`; a staleness flag appears for repos with no commits in 6 months
5. `overdrive-intel feedback miss <url>` and `feedback noise <id>` store reports server-side queryable by type and item reference
   **Plans**: 4 plans in 3 waves

Plans:

- [ ] 06-01 (Wave 1): Alert engine core — AlertDelivery outbox model + migration, alert_engine.py (keyword matching, Redis cooldown, urgency tiers), slack_delivery.py, check_alerts cron worker
- [ ] 06-02 (Wave 1): Quality scoring — quality_service.py (GitHub signals, safe pattern detection, staleness), quality_score_details JSONB column + migration, score_quality cron worker, API schema update
- [ ] 06-03 (Wave 2): Breaking change detection + alerts API + CLI — keyword heuristic (no LLM), alerts endpoints (rule CRUD, set-slack, status), CLI alerts sub-app. FEED-01/02/03 already complete.
- [ ] 06-04 (Wave 3): Test suite — ~30 tests covering all 15 requirements (ALERT-01..08, QUAL-01..04, FEED-01..03 verified existing)

### Phase 7: Additional Source Adapters

**Goal**: Source coverage expands from 2 Tier 1 sources to 10-15 total, providing the breadth that makes Overdrive Intel's signal comprehensive
**Depends on**: Phase 2
**Requirements**: INGEST-07, INGEST-08, INGEST-09, INGEST-10, INGEST-11, INGEST-12, INGEST-13, INGEST-14
**Success Criteria** (what must be TRUE):

1. `overdrive-intel status` shows 10+ active sources each with a recent `last_successful_poll` timestamp
2. HN Algolia, Reddit RSS, and YouTube RSS adapters deliver items that pass the relevance gate and reach `processed` status
3. npm, MCP Registry, Claude Code GitHub releases, Releasebot, and awesome-claude-code adapters each deliver items without blocking or sharing circuit state with other adapters
4. A source adapter failure (404, timeout, API error) triggers the circuit breaker for that source only; all other sources continue polling
   **Plans**: 5 plans in 3 waves

Plans:

- [ ] 07-01 (Wave 1): HN Algolia REST adapter + Reddit RSS adapter (INGEST-07, INGEST-08)
- [ ] 07-02 (Wave 1): YouTube RSS adapter + GitHub Releases Atom adapter (INGEST-11, INGEST-12)
- [ ] 07-03 (Wave 2): npm Registry search adapter + MCP Registry cursor-pagination adapter (INGEST-09, INGEST-10)
- [ ] 07-04 (Wave 2): awesome-claude-code git diff adapter + Releasebot RSS adapter (INGEST-13, INGEST-14)
- [ ] 07-05 (Wave 3): WorkerSettings wiring + test suite for all 8 adapters (INGEST-07..14)

### Phase 8: Operations

**Goal**: The service runs reliably in production with automated backups, spend gating enforced, structured logging, and a dead man's switch for silent ingestion failure
**Depends on**: Phase 7
**Requirements**: OPS-01, OPS-02, OPS-03, OPS-04, OPS-05
**Success Criteria** (what must be TRUE):

1. `docker compose up` in the production config starts API server, fast worker, slow worker, and Redis; all services restart on failure
2. Postgres is managed (Neon or Supabase) with automated daily backups; a restore can be performed from the last backup
3. The hard daily spend limit ($10 default) is enforced by the spend gate; no classification runs when the limit is reached, and the limit survives worker restarts
4. All log output in production is structured JSON; a log entry for a classification call includes item ID, source, cost, and spend_remaining
5. If no ingestion run completes for >24 hours, an alert fires (dead man's switch) to detect silent pipeline failure
   **Plans**: 4 plans in 2 waves

Plans:

- [ ] 08-01 (Wave 1): Production Docker Compose — Dockerfile, docker-compose.prod.yml (API + fast worker + slow worker + Redis + Caddy), Caddyfile, .env.example with deploy checklist (OPS-01)
- [ ] 08-02 (Wave 1): Neon SSL hardening (init_db.py connect_args), SLACK_WEBHOOK_URL config, spend gate startup warning, per-item classify_items log fields (OPS-02, OPS-03, OPS-04)
- [ ] 08-03 (Wave 2): Dead man's switch — dms_worker.py cron (module-level get_settings import), heartbeat writes in ingestion pollers, startup cold-start seeding, test_dms_worker.py (5 tests incl. cold-start) + test_logging.py (OPS-05, OPS-03, OPS-04)
- [ ] 08-04 (Wave 2): Neon restore runbook — docs/ops/restore-runbook.md with point-in-time restore procedure and OPS-02 acceptance test (OPS-02)

### Phase 29: Round 3 Audit Fixes

**Goal**: Close all Round 3 audit findings — admin endpoint exposure, context-pack bugs, breaking false relevance, feed dedup, multi-tag, library duplicates
**Depends on**: Phase 28
**Requirements**: R3-P0-01, R3-P1-02, R3-P1-03, R3-P1-04, R3-P1-05, R3-P2-06, R3-P2-07, R3-P2-08, R3-BONUS-09
**Success Criteria** (what must be TRUE):

1. GET /v1/guide does not expose /v1/admin/* endpoints; error hints do not leak admin paths
2. Context-pack compress=true with explicit budget respects the budget; single-tier briefings produce multiple bullets
3. Breaking changes MCP responses show disclaimer when results do not match query entity
4. Feed with limit=100 returns 100 items after dedup; multi-tag AND filtering works
5. Library search response has items but no duplicate results array

Plans:

- [ ] 29-01 (Wave 1): P0 admin exposure + P1 context-pack budget/compression (R3-P0-01, R3-P1-02, R3-P1-03)
- [ ] 29-02 (Wave 1): P1 feed dedup overfetch + P2 multi-tag + P2 library duplicates (R3-P1-05, R3-P2-06, R3-P2-07)
- [ ] 29-03 (Wave 1): P1 breaking false relevance disclaimer in MCP (R3-P1-04)
- [ ] 29-04 (Wave 1): P2 pipeline lag + bonus Slack digest investigation (R3-P2-08, R3-BONUS-09)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

Note: Phase 6 depends on Phase 4 (not Phase 5); Phase 7 depends on Phase 2 (not Phase 6). Phases 5, 6, and 7 can begin as soon as their respective dependencies are met and may overlap.

| Phase                           | Plans Complete | Status      | Completed  |
| ------------------------------- | -------------- | ----------- | ---------- |
| 1. Foundation                   | 3/3            | Complete    | 2026-03-14 |
| 2. Ingestion Core               | 3/3            | Complete    | 2026-03-14 |
| 3. Pipeline Core                | 4/4            | Complete    | 2026-03-14 |
| 4. Server + Auth                | 4/4            | Complete    | 2026-03-14 |
| 5. CLI                          | 4/4            | Complete    | 2026-03-14 |
| 6. Alerting, Quality + Feedback | 0/4            | Not started | -          |
| 7. Additional Source Adapters   | 5/5            | Complete    | 2026-03-15 |
| 8. Operations                   | 4/4            | Complete    | 2026-03-15 |

| 9. Product UX Improvements | 5/5 | Complete | 2026-03-17 |
| 10. Intelligence Layer | 6/6 | Complete | 2026-03-17 |
| 11. Advanced Source Adapters| 4/4 | Complete | 2026-03-17 |
| 12. Extended Ingestion | 6/6 | Complete | 2026-03-17 |
| 13. Stability + Intel Quality | 1/5 | Complete | 2026-03-18 |
| 14. Agent Experience + Data Quality | 5/5 | Complete | 2026-03-18 |
| 15. Knowledge Library | 5/5 | Complete | 2026-03-18 |
| 16. Distribution & Discovery | 4/4 | Complete | 2026-03-19 |
| 17. Adoption Readiness | 5/5 | Complete | 2026-03-19 |
| 18. GitHub Repo Intelligence | 4/4 | Complete | 2026-03-20 |
| 19. Evaluation Fixes | 0/4 | In progress | - |
| 20. Content Coverage Fixes | 4/4 | Complete | 2026-03-20 |
| 21. Production Audit Fixes | 6/6 | Complete | 2026-03-20 |
| 22. Production Deep Dive Fixes | 6/6 | Complete | 2026-03-20 |
| 23. Deep Dive Fix Cycle 3 | 6/6 | Complete | 2026-03-22 |
| 24. Search Quality Overhaul | 4/4 | Complete | 2026-03-22 |
| 25. Search Quality & Pipeline Fixes | 7/7 | Complete   | 2026-03-22 |
| 26. Production Deep Dive Fixes | 8/8 | Complete    | 2026-03-24 |
| 27. System Audit Fixes | 3/3 | Complete    | 2026-03-24 |
| 28. Search & Feed Quality Overhaul | 3/3 | Complete    | 2026-03-24 |
| 29. Round 3 Audit Fixes | 3/4 | Complete    | 2026-03-25 |

### Phase 9: Product UX Improvements

**Goal:** Make the intelligence feed genuinely useful for all four personas (agent builder, newsletter curator, learner, autonomous agent) with incremental polling, digest endpoint, complete CLI, semantic search, and story clustering
**Depends on:** Phase 8
**Requirements:** UX-01, UX-02, UX-03, UX-04, UX-05, UX-06, UX-07, UX-08, UX-09, UX-10
**Success Criteria** (what must be TRUE):

1. `feed --since <timestamp>` returns only items newer than the given timestamp; an agent polling hourly gets zero results when nothing changed
2. `GET /v1/digest?days=7` returns the week's top items grouped by primary_type with per-group counts
3. `GET /v1/info/<id>` returns full item detail including raw content; CLI `info` command works
4. Every feed item has a non-null `summary`, `source_name`, `published_at`, and `significance` field
5. `GET /v1/similar/<id>` returns semantically similar items using pgvector embeddings
6. CLI exposes all API surface: `alerts`, `profile`, `feedback` subcommands work
7. Alert rules support webhook delivery (not just Slack) — an agent endpoint can receive push notifications
8. `feed --sort significance` returns items ordered by significance tier first, then relevance score
9. Search supports the same filters as feed (`type`, `tag`, `significance`, `days`)
10. Semantically related items from different sources are clustered with a `cluster_id`

**Plans**: 5 plans in 3 waves

Plans:

- [x] 09-01 (Wave 1): Schema migration (published_at, source_name, cluster_id + indexes + backfill) + new API endpoints GET /v1/digest and GET /v1/similar/{id} + confirm /v1/info/{id} (UX-02, UX-03, UX-04, UX-05)
- [x] 09-02 (Wave 2): Feed enhancements (since, sort=significance) + search filter extension (type, tag, significance, days) + webhook alert delivery service + POST /v1/alerts/webhook endpoint (UX-01, UX-07, UX-08, UX-09)
- [ ] 09-03 (Wave 2): CLI completeness — Typer: --since/--sort on feed, --type/--tag/--significance/--days on search, verify alerts/profile/feedback; bash: cmd_alerts/cmd_profile/cmd_feedback, all new flags, URL encoding fix (UX-06, UX-01, UX-08, UX-09)
- [x] 09-04 (Wave 3): cluster_items ARQ worker (pgvector per-item proximity, idempotent, 30-min cron) + ingest adapter published_at/source_name population (rss, github, hn, youtube) + pipeline summary fallback (UX-10, UX-04)
- [x] 09-05 (Wave 3): Full test suite — 5 new test files (digest, similar, search, webhook_delivery, cluster_worker) + extend test_api_feed + test_alert_engine; all UX-01..10 have passing tests (UX-01..UX-10)

### Phase 10: Intelligence Layer

**Goal:** Transform the API from an information feed into an operational intelligence layer — agents can act on intel autonomously, curators get pre-rendered output, and every consumer gets personalized, temporal, contextual intelligence
**Depends on:** Phase 9
**Requirements:** INTEL-01, INTEL-02, INTEL-03, INTEL-04, INTEL-05, INTEL-06, INTEL-07, INTEL-08, INTEL-09, INTEL-10, INTEL-11, INTEL-12
**Success Criteria** (what must be TRUE):

1. `GET /v1/context-pack?topic=mcp&budget=2000` returns a token-budgeted intelligence briefing optimized for agent system prompt injection
2. `GET /v1/trends` returns topics/tools with velocity scores (accelerating, plateauing, declining) over configurable windows
3. `GET /v1/diff` returns a personalized delta — only items that affect the user's stack, with impact descriptions
4. `GET /v1/feed?new=true` (or `since-last-check`) automatically tracks cursor per API key and returns only unseen items
5. `GET /v1/feed?persona=agent-builder` returns a pre-configured feed optimized for that role (also: curator, learner, agent)
6. `POST /v1/items/{id}/signal` accepts upvote/bookmark/dismiss; `GET /v1/items/{id}/signals` returns community adoption counts
7. `POST /v1/watchlists` accepts a natural language concept description; matched semantically using vector search on incoming items
8. `GET /v1/items/{id}/embed?format=markdown` returns pre-rendered newsletter blocks, Slack blocks, or terminal output
9. `GET /v1/landscape/{domain}` returns a structured competitive map for a given domain (tools, positioning, momentum, gaps)
10. `GET /v1/threads` returns evolving storylines grouping related items over time with generated narrative summaries
11. `GET /v1/sla` exposes pipeline freshness guarantees — max item age, pipeline lag, coverage score, source health summary
12. Items with hype consensus carry a `contrarian_signals` field surfacing adoption failures, security concerns, or regressions

**Plans**: 6 plans in 2 waves

Plans:

- [ ] 10-01 (Wave 1): Feed cursor (new=true, last_seen_at migration) + persona presets + SLA endpoint (INTEL-04, INTEL-05, INTEL-11)
- [ ] 10-02 (Wave 1): ItemSignal model + migration + signals API (upvote/bookmark/dismiss) + contrarian_signals computed field (INTEL-06, INTEL-12)
- [ ] 10-03 (Wave 1): Trends velocity endpoint (SQL window aggregation) + landscape domain map endpoint (INTEL-02, INTEL-09)
- [ ] 10-04 (Wave 1): Context-pack briefing (token-budgeted, text/plain) + embed renderer (markdown/slack/terminal) (INTEL-01, INTEL-08)
- [ ] 10-05 (Wave 2): Diff personalized delta (profile tag intersection) + watchlists (pgvector semantic concept matching) (INTEL-03, INTEL-07)
- [ ] 10-06 (Wave 2): Threads storylines (cluster_id GROUP BY + narrative summary + signal momentum) + full test suite (INTEL-10)

### Phase 11: Advanced Source Adapters — web scraping, research paper analysis, deep GitHub repository analysis

**Goal:** Expand source coverage with three new adapter types: arXiv research papers (Atom API), deep GitHub repository analysis (REST stats, event-driven), and Playwright web scraping (JS-rendered blogs/changelogs). Social media monitoring deferred (X/Twitter cost-prohibitive, Discord requires bot auth); replaced with dev.to/Hashnode RSS feeds.
**Depends on:** Phase 10
**Requirements**: ADAPT-01, ADAPT-02, ADAPT-03, ADAPT-04
**Success Criteria** (what must be TRUE):

1. arXiv adapter fetches recent AI/SE papers via Atom API, extracts abstracts directly (no PDF download), and stores as IntelItems
2. Deep GitHub analyzer monitors watched repos for star milestones, commit bursts, and README changes; only creates items on threshold events (most polls produce 0 items)
3. Playwright scraper fetches JS-rendered pages using config-driven CSS selectors; blocks images/CSS/fonts for bandwidth savings; Dockerfile includes chromium binary
4. All three adapters registered in WorkerSettings with appropriate cron schedules; source seed script creates initial sources
5. Unit tests cover all three adapters with mocked external calls; full test suite passes with no regressions

**Plans**: 4 plans in 2 waves

Plans:

- [x] 11-01 (Wave 1): arXiv research paper adapter — fetch_arxiv_feed helper + ingest_arxiv.py worker with feedparser (ADAPT-01)
- [x] 11-02 (Wave 1): Deep GitHub repository analyzer — fetch_github_repo_info/stats helpers + ingest_github_deep.py event-driven worker (ADAPT-02)
- [x] 11-03 (Wave 1): Playwright web scraper — playwright dependency + Dockerfile update + ingest_scraper.py selector-driven worker (ADAPT-03)
- [ ] 11-04 (Wave 2): Worker wiring + source seeds + unit tests for all 3 adapters + full regression test (ADAPT-01, ADAPT-02, ADAPT-03, ADAPT-04)

### Phase 12: Extended Ingestion — PyPI adapter, CHANGELOG.md diffing, newsletter/email ingest, sitemap ingest, Bluesky adapter, GitHub Discussions, VS Code Marketplace, feed autodiscovery, expanded source seeds

**Goal:** Expand ingestion to 20+ sources with PyPI package tracking, CHANGELOG.md diffing, Mailgun newsletter webhook, XML sitemap parsing, Bluesky social monitoring, GitHub Discussions GraphQL, VS Code Marketplace extensions, feed type autodiscovery, and comprehensive source seeds
**Depends on:** Phase 11
**Requirements**: EXT-01, EXT-02, EXT-03, EXT-04, EXT-05, EXT-06, EXT-07, EXT-08, EXT-09
**Success Criteria** (what must be TRUE):

1. PyPI adapter tracks configured packages by version; new releases create IntelItems
2. CHANGELOG.md changes in watched repos are detected via SHA diffing and create IntelItems with decoded content
3. Mailgun newsletter emails are received, HMAC-verified, and stored as IntelItems via push webhook
4. XML sitemaps are parsed (including sitemapindex recursion), pages fetched and content extracted
5. Bluesky adapter authenticates via atproto with Redis session caching; supports both account feeds and keyword search
6. GitHub Discussions adapter fetches from configured repos via GraphQL API
7. VS Code Marketplace adapter searches extensions by keyword using the POST gallery API
8. Feed autodiscovery service detects RSS, Atom, JSON Feed, and Unknown feed types
9. Source seed script creates initial sources for all new adapter types; all adapters wired in WorkerSettings
10. Unit tests cover all adapters with mocked external calls; full test suite passes

**Plans**: 6 plans in 2 waves

Plans:

- [x] 12-01 (Wave 1): Config settings + CHANGELOG.md diffing in github-deep + feed autodiscovery service (EXT-02, EXT-08)
- [x] 12-02 (Wave 1): PyPI adapter + VS Code Marketplace adapter (EXT-01, EXT-07)
- [x] 12-03 (Wave 1): Bluesky adapter port + Sitemap adapter port (EXT-04, EXT-05)
- [x] 12-04 (Wave 1): GitHub Discussions GraphQL adapter + Newsletter email webhook (EXT-03, EXT-06)
- [x] 12-05 (Wave 2): Worker settings wiring + source seed script (EXT-09)
- [x] 12-06 (Wave 2): Full test suite — 7 new test files + existing test updates (EXT-01..EXT-09)

### Phase 13: Stability + Intelligence Quality — deep-dive fixes (C-1..C-4, H-1..H-18) and briefing endpoint

**Goal:** Fix all critical and high-priority production issues found in the full project audit: batch recovery race, connection pool exhaustion, Redis death detection, Mailgun auth bypass, Bluesky timezone corruption, feed cursor crash, spend TTL race, SSRF webhook, Redis password, O(N^2) cluster worker, Playwright OOM, N+1 threads query, classify cycling, profile re-sort scope. Also improve intelligence endpoint quality: context-pack semantic expansion, trends source-diversity weighting, landscape significance-weighted momentum.
**Requirements**: STAB-01, STAB-02, STAB-03, STAB-04, STAB-05, STAB-06, STAB-07, STAB-08, STAB-09, STAB-10, STAB-11, STAB-12, STAB-13, STAB-14, STAB-15, STAB-16, STAB-17
**Depends on:** Phase 12
**Success Criteria** (what must be TRUE):

1. Batch API items are not reset by recovery while an active batch poll is running; results committed in single transaction
2. Connection pool total across all processes stays at 16 or below (4 headroom on Neon free tier)
3. Redis failure is detectable via /health endpoint (external watchdog can poll); Redis password has no default
4. Mailgun webhook returns 500 when signing key is missing in production
5. Bluesky timestamps are UTC-aware; feed cursor handles naive datetimes; spend tracker TTL set unconditionally
6. Webhook URLs validated against SSRF (RFC1918/loopback/link-local rejected)
7. Cluster worker uses batch SQL (not N individual queries); scraper limited to 2 concurrent Chromium instances
8. Threads list uses single partitioned query; classify cycling on spend limit eliminated; profile boost in SQL
9. Context-pack topic is optional with semantic expansion; TL;DR leads with intelligence not counts
10. Trends weighted by source diversity; landscape weighted by significance

**Plans**: 5 plans in 2 waves

Plans:

- [x] 13-01 (Wave 1): Critical fixes — batch recovery race + per-item commits, connection pool, Redis death detection, Mailgun auth bypass (STAB-01, STAB-02, STAB-03, STAB-04)
- [x] 13-02 (Wave 1): Correctness + security — Bluesky timezone, feed cursor crash, spend TTL race, SSRF webhook, Redis password (STAB-05, STAB-06, STAB-07, STAB-08, STAB-09)
- [x] 13-03 (Wave 1): Performance + safety — O(N^2) cluster worker batch SQL, Playwright OOM semaphore (STAB-10, STAB-11)
- [x] 13-04 (Wave 2): Query + logic — N+1 threads, classify cycling, profile re-sort in SQL (STAB-12, STAB-13, STAB-14)
- [ ] 13-05 (Wave 1): Intelligence quality — context-pack semantic expansion + briefing, trends diversity, landscape significance (STAB-15, STAB-16, STAB-17)

### Phase 14: Agent Experience + Data Quality + Reliability — API key provisioning, structured errors, response consistency, classification upgrade, dedup, MCP server, action items

**Goal:** Fix all findings from the UX deep-dive audit: unblock first-use with API key provisioning, make all error responses machine-parseable, unify response envelopes, fix cursor isolation, upgrade classification quality, enable content dedup in ingestion, surface credit exhaustion, extend MCP server, add action items endpoint, and fix integration documentation.
**Requirements**: UX-01, UX-02, UX-03, UX-04, UX-05, UX-06, UX-07, UX-08, UX-09, UX-10, UX-11, UX-12, UX-13, UX-14, UX-15, UX-16
**Depends on:** Phase 13
**Success Criteria** (what must be TRUE):

1. A new user can create an API key via script or admin endpoint and authenticate immediately
2. All error responses use structured `{error: {code, message, hint}}` envelope; 429 includes Retry-After
3. All list endpoints return `{items, total}` envelope; /search echoes offset+limit; /similar is wrapped
4. /feed and /diff have separate cursors; cursors don't advance on empty results
5. Profile endpoint returns warnings for unrecognized skills; TAG_GROUPS unified across endpoints
6. Classification prompt includes source tier, 4000 char window, actionable summary guidance
7. SLA metrics are correctly named, pipeline lag uses median, credits exhaustion is surfaced
8. Content fingerprint dedup runs during RSS/scraper/GitHub ingestion
9. DMS threshold is 4h; dying sources trigger Slack alert; summary fallback uses marker
10. MCP server exposes 5 tools; action items endpoint exists; read/acted_on signals supported
11. Integration snippets use correct field names and values

**Plans**: 5 plans in 2 waves

Plans:

- [ ] 14-01 (Wave 1): API key provisioning script + structured error handler + Retry-After + admin key CRUD (UX-01, UX-02)
- [ ] 14-02 (Wave 1): Response consistency — /similar envelope, context-pack error format, separate cursors, search pagination echo, tags null fix, momentum normalization, diff filters (UX-03, UX-04, UX-05)
- [ ] 14-03 (Wave 1): Data quality — profile skill validation, TAG_GROUPS unification, classification prompt upgrade, thread narratives, score docs (UX-06, UX-07, UX-08)
- [ ] 14-04 (Wave 1): Trust — SLA fixes, content dedup, credit exhaustion surfacing, DMS 4h, dying source alerts, summary fallback, real /health checks (UX-09, UX-10, UX-11, UX-12)
- [ ] 14-05 (Wave 2): Features — MCP server extension, action items endpoint, read/acted_on signals, significance alerts, integration snippets fix (UX-13, UX-14, UX-15, UX-16)

### Phase 15: Knowledge Library — synthesized best practices, tools, gotchas per topic with MCP tool, CLI, API, profile-matched recommendations

**Goal:** Build a durable, auto-curated synthesis layer above the intel feed — agents get evergreen best practices, patterns, and gotchas at session start; developers browse topic guides; operators see which knowledge is stable vs evolving. V1 computed view validates scoring, V2 materializes into dedicated table with LLM synthesis, V3 adds recommendations, search, signals, and context-pack integration.
**Requirements**: LIB-01, LIB-02, LIB-03, LIB-04, LIB-05, LIB-06, LIB-07, LIB-08, LIB-09, LIB-10, LIB-11, LIB-12, LIB-13, LIB-14
**Depends on:** Phase 14
**Success Criteria** (what must be TRUE):

1. GIN index on intel_items.tags accelerates all @> containment queries system-wide
2. GET /v1/library returns topic index ranked by evergreen score; GET /v1/library/{topic} returns items with computed evergreen_score
3. GET /v1/library/topics works without authentication as discovery entry point
4. library_items table stores synthesized entries with tldr/body/key_points/gotchas/agent_hint per topic
5. Weekly synthesis cron generates topic guides via Haiku; graduation cron promotes high-signal items; staleness cron flags aged entries
6. GET /v1/library/{slug} returns full synthesized entry with ETag; GET /v1/library/search returns semantic+text results
7. GET /v1/library/recommend returns profile-matched entries; POST /v1/library/{slug}/signals accepts helpful/outdated feedback
8. GET /v1/context-pack?include_library=true injects library entries alongside feed items with 70/30 budget split
9. MCP intel_library tool distinguishes "how to" (library) from "what's new" (feed) for agent tool selection
10. CLI library command has 8 subcommands: topics, topic, get, search, recommend, helpful, outdated, suggest
11. Full test suite covers all endpoints, workers, and MCP tool with ~33 new tests

**Plans**: 5 plans in 3 waves

Plans:

- [x] 15-01 (Wave 1): GIN index + evergreen scoring + GET /v1/library + /v1/library/topics (LIB-01, LIB-02, LIB-03)
- [x] 15-02 (Wave 1): library_items table + migration + synthesis cron + graduation/staleness crons (LIB-05, LIB-06, LIB-07)
- [x] 15-03 (Wave 2): Recommend + search + signals + context-pack integration (LIB-04, LIB-11, LIB-12, LIB-13)
- [x] 15-04 (Wave 2): MCP tool + CLI command + guide/snippet updates (LIB-08, LIB-09, LIB-10)
- [x] 15-05 (Wave 3): Full test suite for all library features (LIB-14) — 37 tests, worker SQL bug fixed

### Phase 16: Distribution & Discovery — pip package, npm MCP server, MCP registries, CLAUDE.md hook, Slack bot, GitHub Action

**Goal:** Make Overdrive Intel discoverable and accessible through standard distribution channels — pip install for CLI, npx for MCP server, registry listings for agent discovery, Claude Code hook for proactive awareness, Slack digests for teams, GitHub Action for CI integration
**Requirements**: DIST-01, DIST-02, DIST-03, DIST-04, DIST-05, DIST-06
**Depends on:** Phase 15
**Success Criteria** (what must be TRUE):

1. `pip install overdrive-intel` installs the CLI; `overdrive-intel --help` works from a fresh venv
2. `npx overdrive-intel-mcp` starts a working MCP server that calls the HTTP API with all 6 tools
3. MCP registry submission files (server.json) ready for mcp-publisher; Smithery submission ready
4. Claude Code SessionStart hook detects AI/MCP projects and injects overdrive-intel context
5. Slack daily digest worker posts formatted intelligence to configured webhook URL
6. GitHub Action checks for breaking changes in CI using project dependency list

**Plans**: 4 plans in 2 waves

Plans:

- [ ] 16-01 (Wave 1): Fix pyproject.toml packaging for src-layout + hatchling build backend (DIST-01)
- [ ] 16-02 (Wave 1): npm MCP server package — TypeScript HTTP API client with 6 tools (DIST-02)
- [ ] 16-03 (Wave 1): CLAUDE.md auto-inject hook + Slack daily digest ARQ cron worker (DIST-04, DIST-05)
- [ ] 16-04 (Wave 2): GitHub Action composite + check_breaking script + MCP registry server.json + publish checkpoint (DIST-03, DIST-06)

### Phase 17: Adoption Readiness — fix all deep-dive findings (5 critical, 14 high), self-service signup, per-user spend tracking, cursor fix, library route fix, MCP defaults, onboarding hints, billing-later hooks

**Goal:** Fix all critical and high-priority production findings from the 6-reviewer deep-dive audit. Unblock self-service beta onboarding, add per-user spend tracking, fix cursor item loss, fix library route shadowing, fix MCP server defaults, add profile onboarding hints, and lay billing infrastructure hooks.
**Requirements**: ADOPT-01 through ADOPT-27
**Depends on:** Phase 16
**Success Criteria** (what must be TRUE):

1. A new user can register via `POST /v1/auth/register` without operator intervention
2. Rate limits cannot be bypassed by key multiplication; per-user key cap enforced at 5
3. Feed/diff cursors never skip items — advance to max(created_at) from actual rows
4. All three library routes (`/topics`, `/topic/{topic}`, `/{slug}`) are independently reachable
5. MCP server works with default settings (http scheme, correct key path, correct response fields)
6. Per-user spend is tracked in Redis alongside global safety ceiling
7. User.tier and stripe_customer_id columns exist for future billing
8. CLI `feed --new` flag works, CLI handles connection errors gracefully
9. All 5 critical + 14 high priority findings from the deep-dive audit are addressed

**Plans**: 5 plans in 2 waves

Plans:

- [x] 17-01 (Wave 1): Quick fixes — H-1 timezone crash, H-5 key_prefix, H-8 typed admin body, H-9 install.sh placeholder, H-10 CLI profile display, H-12 digest SQL, M-1 HTTP 204, M-17 CORS, L-9 server.json OWNER (completed 2026-03-19)
- [x] 17-02 (Wave 1): Auth + signup — C-1 self-service register, C-3 key cap + user-scoped rate limits, H-4 User.is_active check, H-3 pre-auth IP rate limiting (completed 2026-03-19)
- [x] 17-03 (Wave 1): Pipeline + data fixes — C-4 cursor fix, C-5 library route fix, M-4 JSON tags guard, M-14 CLI --new flag (completed 2026-03-19)
- [ ] 17-04 (Wave 2, depends on 17-02): Spend tracking + billing hooks — C-2 per-user spend, H-2 spend gate hardening, H-13 batch resume spend, H-14 configurable rate limits, M-10 User.tier migration
- [x] 17-05 (Wave 2, depends on 17-01): Onboarding + MCP fixes — H-6 MCP server defaults, H-7 SSRF DNS rebinding at delivery, H-11 profile onboarding hints, M-18 CLI error handling (completed 2026-03-19)

### Phase 18: GitHub Repo Intelligence — auto-promote discovered repos, full awesome-list scrape, trending extraction, Claude marketplace, broad star tracking

**Goal:** Close the gap between repo discovery and repo intelligence. Auto-promote high-star repos to deep monitoring, bulk-import hundreds of repos from awesome-lists, extract individual repos from GitHub Trending, add Claude MCP marketplace as a source, and track stars/maintenance for ALL GitHub-URL items system-wide.
**Requirements**: REPO-01, REPO-02, REPO-03, REPO-04, REPO-05
**Depends on:** Phase 17
**Success Criteria** (what must be TRUE):

1. When GitHub Search discovers a repo with >50 stars and a relevant topic, a github-deep Source is auto-created
2. The awesome-list adapter parses FULL README.md and extracts all [name](url) entries; repos with >50 stars are auto-promoted
3. GitHub Trending scraper creates one IntelItem per trending repo (not one HTML blob)
4. Claude MCP marketplace source exists and can extract server names/descriptions
5. ALL GitHub-URL intel_items get periodic star/maintenance updates via broad tracker cron (24h cycle)

**Plans**: 4 plans in 2 waves

Plans:

- [x] 18-01 (Wave 1): Auto-promote discovered repos in ingest_github.py + broad star/maintenance tracking cron (REPO-01, REPO-05) (completed 2026-03-20)
- [x] 18-02 (Wave 1): Full awesome-list README scrape + auto-promote + 4 new awesome-list source seeds (REPO-02) (completed 2026-03-20)
- [x] 18-03 (Wave 1): Trending repo extraction + Claude MCP marketplace source (REPO-03, REPO-04) (completed 2026-03-20)
- [ ] 18-04 (Wave 2): Unit tests for all 5 features — 16 tests across 3 files (REPO-01..05)

### Phase 19: Evaluation Fixes

**Goal**: Resolve all critical evaluation findings — context-pack, breaking filter, dedup, data quality
**Depends on:** Phase 18
**Requirements**: EVAL-01, EVAL-02, EVAL-03, EVAL-04
**Status**: Completed 2026-03-20

### Phase 20: Content Coverage Fixes

**Goal**: Fix content coverage gaps so all claimed topics (agent frameworks, competing tools, SDK releases, embedding/RAG) have substantial processed items and synthesized library entries
**Depends on:** Phase 19
**Requirements**: COV-01, COV-02, COV-03, COV-04, COV-05
**Success Criteria** (what must be TRUE):

1. Dormant sources (openai-changelog, cursor-blog, copilot-changelog, aider-history) are actively ingesting items
2. Release-feed items (PyPI, GitHub Releases) have enriched titles that embed meaningfully (not bare version strings)
3. Reference set expanded with 20+ positive exemplars covering agent frameworks, competing tools, and RAG topics
4. Dedicated RAG/embedding sources added (vector DB blogs, embedding model releases)
5. Per-source relevance thresholds implemented so release feeds use 0.5 while community sources use global default (0.65)
6. Library re-synthesized with new content; `/v1/library/search` returns results for all claimed topics

**Plans**: 4 plans in 2 waves

Plans:

- [x] 20-01 (Wave 1): Per-source threshold support in gate_relevance + fix dormant sources + fix_dormant_sources.py script (COV-01, COV-05) (completed 2026-03-20)
- [x] 20-02 (Wave 1): Enrich release-feed titles in ingest_gh_releases + ingest_pypi + reprocess_filtered_items.py script (COV-02) (completed 2026-03-20)
- [x] 20-03 (Wave 1): Expand reference set with 25 positive exemplars covering agent frameworks, competing tools, RAG/embedding (COV-03)
- [x] 20-04 (Wave 2, depends on 20-03): Add RAG/embedding sources (seed_rag_sources.py) + verify library synthesis topics (COV-04) (completed 2026-03-20)

### Phase 21: Production Audit Fixes — fix all 31 findings from production readiness audit

**Goal:** Fix all 28 actionable findings from the comprehensive production readiness audit (skipping #15 DB storage, #18 CI/CD, #23 f-string SQL). Covers 3 P0 crash/data bugs, 11 P1 functional issues, 5 P2 defensive coding, and 9 P3 cleanup items.
**Requirements**: P0-1, P0-2, P0-3, P1-4, P1-5, P1-6, P1-7, P1-8, P1-9, P1-10, P1-11, P1-12, P1-13, P1-14, P2-16, P2-17, P2-19, P2-20, P2-21, P2-22, P3-24, P3-25, P3-26, P3-27, P3-28, P3-29, P3-30, P3-31
**Depends on:** Phase 20
**Success Criteria** (what must be TRUE):

1. quality_workers.py no longer crashes with SessionClosedError on non-GitHub items
2. GET /v1/status returns <5KB summary instead of 114KB full source list
3. MCP status route maps to actual API fields (not undefined phantom fields)
4. Library slug endpoint returns 200 (not 500), search returns topic-specific results
5. All adapters set source_name, published_at propagated from RSS dates
6. Search results deduplicated, ILIKE wildcards escaped, total counts accurate
7. Integration docs use https://inteloverdrive.com (no stale IPs)
8. SKILL.md describes MCP tool interface, not CLI commands
9. Redis null guards, SSRF validation, Slack escaping all in place

**Plans**: 6 plans in 1 wave

Plans:

- [x] 21-01 (Wave 1): Critical crash fix — quality_workers session scope, non-GitHub quality scoring, ARQ retries (P0-1, P1-7, P2-17) (completed 2026-03-20)
- [x] 21-02 (Wave 1): API fixes — status bloat, health perf, search dedup, total counts, ILIKE escape (P0-2, P1-11, P1-12, P2-22, P3-26, P3-27) (completed 2026-03-20)
- [x] 21-03 (Wave 1): Library fixes — slug 500, generic slugs, broken search (P1-4, P1-5, P1-6) (completed 2026-03-20)
- [x] 21-04 (Wave 1): Ingestion fixes — source_name 3 adapters, published_at, failed items, source health (P1-8, P1-9, P1-10, P2-21) (completed 2026-03-20)
- [x] 21-05 (Wave 1): MCP + integration — phantom fields, dead code, stale IPs, SKILL.md, source counts, env, setup.sh (P0-3, P1-13, P1-14, P2-19, P3-24, P3-25, P3-28) (completed 2026-03-20)
- [x] 21-06 (Wave 1): Defensive coding — Redis guard, SSRF, Slack escape, feedback schema, dedup optimization (P2-16, P2-20, P3-29, P3-30, P3-31) (completed 2026-03-20)

### Phase 22: Production Deep Dive Fixes — fix all 23 verified issues from post-deploy deep dive

**Goal:** Fix all 23 verified issues from post-deploy deep dive: rebuild MCP tgz (pre-Phase 21 code deployed), add context-pack dedup, optimize DB storage, clean library data, backfill quality scores, fix published_at fallback, requeue failed items, raise scraper throughput, triage erroring sources, fix setup script edge cases, align versions.
**Requirements**: DD-C1, DD-C2, DD-C3, DD-H1, DD-H2, DD-H3, DD-H4, DD-H5, DD-H6, DD-H7, DD-H8, DD-M1, DD-M2, DD-M3, DD-M4, DD-M5, DD-M6, DD-L1, DD-L2, DD-L3, DD-L4, DD-L5
**Depends on:** Phase 21
**Success Criteria** (what must be TRUE):

1. MCP server at 0.4.0 deployed to VPS with library route, fixed inferType, valid JSON truncation
2. Context-pack deduplicates by cluster_id and base URL
3. DB storage reduced by ~100 MB (filtered item embeddings nulled)
4. Generic library slugs deleted; Cursor, Copilot, Aider entries exist
5. All RSS adapters fall back to updated_parsed for published_at
6. Quality scores backfilled for all 0.0-scored items
7. Failed items requeued; erroring sources triaged
8. Scraper semaphore at 2; setup.sh warns on missing npm

**Plans**: 6 plans in 2 waves

Plans:

- [ ] 22-01 (Wave 1): MCP server rebuild — version 0.4.0, library route, inferType fix, JSON truncation, source count, dedup instructions/description, slash command examples (DD-C1, DD-H4, DD-H5, DD-M1, DD-M2, DD-M6, DD-L4)
- [x] 22-02 (Wave 1): Context-pack dedup + DB storage optimization — cluster_id + URL-base dedup, null filtered embeddings (DD-C2, DD-C3) (completed 2026-03-20)
- [x] 22-03 (Wave 1): Library data fixes — delete generic slugs, seed missing topics, fix title-case (DD-H1, DD-H2, DD-L1) (completed 2026-03-20)
- [x] 22-04 (Wave 2): Data quality — published_at fallback, quality score backfill, requeue failed items (DD-H3, DD-H7, DD-H8) (completed 2026-03-20)
- [x] 22-05 (Wave 2): Source infrastructure — raise scraper semaphore, triage erroring sources (DD-H6, DD-M5) (completed 2026-03-20)
- [x] 22-06 (Wave 2): Integration fixes — setup.sh npm warning + node fallback, env var docs, marketplace URL, version alignment (DD-M3, DD-M4, DD-L2, DD-L3, DD-L5) (completed 2026-03-20)

### Phase 23: Deep Dive Fix Cycle 3 — Fix all 56 verified issues from 12-agent deep dive

**Goal:** Fix all 56 verified issues from 12-agent deep dive: 7 P0 crash/DoS bugs, 17 P1 security/quality issues, 21 P2 hardening items, 12 P3 hygiene items. Organized by subsystem to minimize file conflicts.
**Requirements**: P0-1 through P0-7, P1-8 through P1-24, P2-25 through P2-45, P3-46 through P3-57
**Depends on:** Phase 22
**Success Criteria** (what must be TRUE):

1. Null bytes, Unicode, and adversarial input return 400 (not 500) on all endpoints
2. Library semantic search works (all items have embeddings)
3. /v1/trends returns non-empty results using published_at
4. Real client IPs extracted behind reverse proxy; security headers on all responses
5. ARQ retry does not triple-count circuit breaker errors
6. Feed/context-pack collapse cluster duplicates
7. MCP server at 0.7.0 with truncation fix, days=0 fix, tool validation
8. Multi-stage Dockerfile (API/slow-worker without Playwright)
9. All tests pass including previously failing test and new email ingestion tests

**Plans**: 6 plans in 3 waves

Plans:

- [x] 23-01 (Wave 1): P0 crash fixes — null byte middleware, library Unicode, trends date, email nudge removal, server.json update (P0-1, P0-2, P0-4, P0-5, P0-7)
- [x] 23-02 (Wave 1): Security + middleware — X-Forwarded-For extraction, registration hardening, SSRF DNS rebinding, security headers, rate limit wiring, PII audit (P1-8, P1-9, P1-12, P1-13, P2-28, P2-29)
- [x] 23-03 (Wave 1): Pipeline + data quality — library embeddings, zero-item source audit, circuit breaker retry fix, batch commits, classify TTL, empty content guard, embed alerting (P0-3, P0-6, P1-11, P1-14, P1-23, P1-24, P2-37, P2-38, P2-39)
- [x] 23-04 (Wave 2): API quality + search — cluster dedup collapse, breaking classification, out-of-scope detection, library ETag, tags pagination, search key consistency, anon nudge fix (P1-10, P1-15, P1-16, P1-17, P2-25, P2-26, P2-27, P2-45)
- [x] 23-05 (Wave 2): MCP rebuild + defaults — MCP 0.7.0 (truncation, days=0, validation), install.sh removal, localhost defaults, .env.example, CLAUDE.md docs (P1-18, P1-19, P1-20, P1-21, P1-22, P2-30, P2-31, P2-32)
- [x] 23-06 (Wave 3): Infra + tests + hygiene — multi-stage Dockerfile, Redis healthcheck, CI/CD, fix failing test, tautological tests, email tests, .gitignore, root cleanup, stale docs (P2-33 through P2-44, P3-46 through P3-57)

### Phase 25: Search Quality & Pipeline Fixes

**Goal:** Fix all issues found in comprehensive 12-agent test audit: unblock 20K stuck items, fix noise filter, add content quality gate, fix library embeddings + search, cross-source dedup, source hygiene, test infrastructure.
**Requirements**: P25-01, P25-02, P25-03, P25-04, P25-05, P25-06, P25-07
**Depends on:** Phase 24
**Success Criteria** (what must be TRUE):

1. Pipeline items flow from embedded to processed (20K backlog draining)
2. Off-topic queries ("chocolate cake") return empty results; on-topic queries work normally
3. Smithery stubs (< 100 chars content) excluded from search results
4. Library search returns results for multi-word queries ("MCP best practices")
5. Feed results don't show same incident 3x from different sources
6. Sources stop retrying after 3 failed recovery cycles
7. Full test suite passes consistently with no flaky async failures

**Plans**: 7 plans in 3 waves

Plans:

- [ ] 25-01 (Wave 1): Unblock pipeline — SSH production ops, raise DAILY_SPEND_LIMIT (P25-01)
- [ ] 25-04 (Wave 1): Fix library embeddings + search — per-word ILIKE scoring, embedding error logging (P25-04)
- [ ] 25-05 (Wave 1): Cross-source dedup — verify feed cluster collapsing, add test (P25-05)
- [ ] 25-06 (Wave 1): Source hygiene — add recovery_attempts, permanent death after 3 cycles (P25-06)
- [ ] 25-07 (Wave 1): Test flakiness — session rollback safety net, query_logger test (P25-07)
- [ ] 25-02 (Wave 2): Noise filter fix — replace binary fulltext check with count >= 3 threshold (P25-02)
- [ ] 25-03 (Wave 3): Content quality gate — LENGTH >= 100 + quality_score >= 0.40 in search, short content penalty in scorer (P25-03)

### Phase 26: Production Deep Dive Fixes

**Goal:** Fix all verified P0/P1/P2 issues from 6-agent production deep dive — critical rate limit waste, unsafe async sessions, destructive MCP telemetry, server hardening, pipeline correctness, MCP improvements.
**Requirements**: OPS-01, OPS-03, OPS-04, PIPE-03, PIPE-04, PIPE-05, PIPE-07, QUAL-01, DIST-02, API-07, INGEST-09, INGEST-10
**Depends on:** Phase 25
**Success Criteria** (what must be TRUE):

1. GitHub API rate limit is preserved — workers abort batch on 403 instead of burning 50+ wasted calls
2. backfill_readmes uses per-coroutine sessions (no shared AsyncSession across concurrent tasks)
3. MCP telemetry does not overwrite user skills to empty array
4. Classify resume and normal paths both use TYPE_FALLBACK_MAP consistently
5. Production server has active UFW firewall, SSH hardened, Docker log rotation
6. MCP server reports correct quality assessments, validates empty queries, lists correct type values
7. Health endpoint returns last_ingestion timestamp for monitoring
8. All changes deployed to production with verified health

**Plans**: 8 plans in 4 waves

Plans:
- [ ] 26-01 (Wave 1): Worker rate limit fixes — score_quality early exit on 403, backfill_readmes per-coroutine sessions + abort on cascade (#1, #2, #3)
- [ ] 26-02 (Wave 1): Pipeline + MCP critical bugs — MCP skills overwrite fix, classify fallback consistency (#4, #5)
- [ ] 26-03 (Wave 2): Server hardening — UFW, SSH, fail2ban, Docker log rotation, Caddy :80, slow worker memory, Redis healthcheck (#6-9, #15, #16)
- [ ] 26-04 (Wave 2): Pipeline correctness — is_noise URL pass-through, source_name for MCP/npm, BATCH_PARSE_ERROR fix (#10, #18, #19)
- [ ] 26-05 (Wave 2): MCP server fixes — server.json types, computeResultQuality RRF, empty query validation (#11, #14, #17)
- [ ] 26-06 (Wave 3): Pipeline/worker improvements — cluster_items exclude filtered, datetime.utcnow, auto_promote savepoints, significance normalization (#20, #21, #26, #35)
- [ ] 26-07 (Wave 3): MCP + API improvements — search days, feed params, source_name display, HSTS, Dockerfile, library slug, error logging, health last_ingestion (#22, #23, #27-29, #32, #33, #36)
- [ ] 26-08 (Wave 4): Deploy all fixes to production + verify health


### Phase 27: Fix all verified issues from comprehensive system audit — P0 context-pack cache bug, P1 integer overflow + alert significance + search field selector, P2 similarity threshold + briefing quality + breaking classification

**Goal:** Fix 7 verified issues from comprehensive system audit: P0 context-pack quality floor, P1 integer overflow on offset params + alert significance column + search field selector library leak, P2 similarity threshold + breaking classification prompt tightening
**Requirements**: P0-1, P1-1, P1-2, P1-3, P2-1, P2-3
**Depends on:** Phase 26
**Plans:** 3/4 plans complete

Plans:
- [ ] 27-01 (Wave 1): Offset le=10_000_000 on 5 API files + context-pack quality floor SQL + tests (P0-1, P1-1)
- [ ] 27-02 (Wave 2): Search field selector library stripping + similar.py cosine distance threshold + tests (P1-3, P2-1)
- [ ] 27-03 (Wave 1): Alert worker significance column + classification prompt tightening + tests (P1-2, P2-3)


### Phase 28: Search and feed quality overhaul — briefing compression, relevance score recalibration, feed source ranking, zero-result coverage metadata

**Goal:** Improve search and feed quality across four dimensions: compress MCP briefings from raw 18-item lists to 3-5 bullet summaries, recalibrate relevance scores to spread from ~0.054 to ~0.35+ range, boost tier1 official sources in feed ranking, and add zero-result coverage metadata
**Requirements**: PIPE-07, API-03
**Depends on:** Phase 27
**Plans:** 3/3 plans complete

**Success Criteria** (what must be TRUE):
1. MCP briefing type returns a compressed 3-5 bullet summary (~200 tokens) instead of raw item list (~2000 tokens)
2. Relevance scores for gate inputs [0.65, 1.0] span a range >= 0.20 (previously ~0.054)
3. Feed results from tier1 official sources rank above tier3 community sources when relevance is similar
4. Zero-result feed queries with a topic include coverage metadata (topic_sources_monitored count + coverage_note)

Plans:
- [ ] 28-01 (Wave 1): Score recalibration — reweight formula to 0.65/0.15/0.20, backfill script for existing items, updated tests (PIPE-07)
- [ ] 28-02 (Wave 1): Briefing compression — _compress_to_bullets() in context_pack.py, compress=true param, MCP server update (PIPE-07)
- [ ] 28-03 (Wave 2): Feed tier boost + zero-result coverage — correlated subquery tier boost in ORDER BY, topic_sources_monitored metadata (PIPE-07, API-03)

### Phase 29: Fix Round 3 audit findings — P0 admin auth bypass, P1 max_tokens + briefing compression + breaking false relevance + feed dedup, P2 multi-tag + library duplicates + pipeline lag

**Goal:** [To be planned]
**Requirements**: TBD
**Depends on:** Phase 28
**Plans:** 2 plans in 2 waves

Plans:
- [ ] TBD (run /co:plan-phase 29 to break down)

### Phase 30: Structural quality signal fix — community sub-score, quality in relevance scoring, stars in MCP output, briefing quality thresholds, novelty protection

**Goal:** Quality score meaningfully differentiates established projects from unverified experiments, influences feed/briefing ranking, and is visible to consuming agents via MCP output.
**Requirements**: QFIX-01, QFIX-02, QFIX-03, QFIX-04, QFIX-05, QFIX-06
**Depends on:** Phase 29
**Success Criteria** (what must be TRUE):

1. A 2400-star repo scores meaningfully higher quality_score than a 0-star repo pushed the same day
2. Feed results are ranked with quality_score as a ranking factor
3. Context-pack briefings use quality threshold of 0.55 (raised from 0.40) with novelty protection for items < 7 days old
4. MCP output shows quality label (established/emerging/new) and star count for each item
5. A backfill script recomputes quality_score for all existing items with the new formula
**Plans:** 3/3 plans complete

Plans:
- [ ] 30-01 (Wave 1): Backend scoring — add community sub-score (stars/forks) to quality formula, update weights, unit tests
- [ ] 30-02 (Wave 1): MCP output — surface quality_score, stars, quality label in MCP formatted output, MCP tests
- [ ] 30-03 (Wave 2): API ranking + backfill — quality in feed ORDER BY, raised context-pack threshold with novelty protection, backfill script

### Phase 31: Result quality overhaul — boost quality weight in ranking, add web framework release feeds, suppress low-quality items, type-aware intent routing

**Goal:** Quality score meaningfully influences search ranking (0.25 weight in RRF), feed suppresses noise items (quality < 0.3 with breaking/major exception), web framework release feeds fill the biggest coverage gap, and intent-aware routing auto-detects tool/breaking queries.
**Requirements**: RANK-01, RANK-02, COVER-01, COVER-02, NOISE-01, NOISE-02, INTENT-01, INTENT-02, INTENT-03, INTENT-04
**Depends on:** Phase 30
**Success Criteria** (what must be TRUE):

1. RRF quality weight is 0.25 (was 0.15), semantic weight reduced to 0.30, sum remains 1.0
2. Feed excludes items with quality_score < 0.3, but always includes breaking/major significance items
3. Web framework release sources (Next.js, React, TypeScript, Node.js + extras) exist as github-releases sources
4. Query "X MCP server" auto-injects primary_type=tool filter; "breaking changes" auto-injects significance=breaking
5. Intent routing does not override explicit user-supplied type/significance params
6. Intent filter falls back to unfiltered when results < 3
**Plans:** 2/2 plans complete

Plans:
- [ ] 31-01 (Wave 1): Search quality — boost RRF quality weight from 0.15 to 0.25, add intent-aware query routing with type/significance detection and fallback
- [ ] 31-02 (Wave 1): Feed quality + coverage — harden feed quality floor to 0.3 with breaking/major exception, seed 7 web framework release sources

### Phase 32: Quality scoring pipeline integration — inline scoring in classify_items, smarter heuristic with penalties, composite feed ranking, backfill rescoring

**Goal:** Quality scoring runs inline during classification (no 0.0 visibility gap), heuristic formula discriminates test/demo/untitled items via penalty system, feed ranking uses weighted composite score giving quality 30% weight, and all existing heuristic-scored items are rescored with the improved formula.
**Requirements**: QSCORE-01, QSCORE-02, QSCORE-03, QSCORE-04, QSCORE-05, QSCORE-06
**Depends on:** Phase 31
**Success Criteria** (what must be TRUE):

1. Items transition to `processed` with a non-zero quality_score already set (no 10-minute gap)
2. Heuristic scoring produces distinct values: tier1+good maxes ~0.85, test/demo/untitled items penalized by 0.15-0.25
3. GitHub API failures get heuristic score instead of punitive 0.1
4. Feed ranking uses composite: 0.40 relevance*freshness + 0.30 quality + 0.20 profile + 0.10 tier
5. Significance partition (breaking/major first) is preserved in feed
6. All 13K+ heuristic-scored items rescored with new formula
**Plans:** 3/3 plans complete

Plans:
- [ ] 32-01 (Wave 1): Smarter heuristic — extract compute_heuristic_quality() with title/summary/content penalties, tier recalibration, fix GitHub 0.1 fallback
- [ ] 32-02 (Wave 1): Composite feed ranking — replace lexicographic ORDER BY with weighted composite score (quality 30% weight)
- [ ] 32-03 (Wave 2): Inline scoring + backfill — quality scoring in classify_items before commit, reduce quality cron to 30-min safety net, backfill rescore 13K items

### Phase 33: Implicit feedback loop + Slack notifications — query chain tracking, auto-miss signals, source tier auto-adjustment, expanded Slack digests

**Goal:** Capture implicit usage signals from MCP queries and surface proactive intelligence via Slack — auto-miss on zero results, query chain refinement tracking, source tier auto-adjustment based on signal ratios, enhanced daily digests, weekly source health reports, coverage gap alerts, and breaking change instant notifications
**Depends on:** Phase 32
**Requirements**: FL-01, FL-02, FL-03, FL-04, FL-05, FL-06, FL-07, FL-08, FL-09
**Success Criteria** (what must be TRUE):

1. POST /v1/feedback/auto accepts auto_miss and query_refinement report types with query metadata
2. Weekly cron job adjusts source tiers based on item signal ratios (10-signal minimum, >50% negative demotes, >70% positive promotes)
3. Daily digest includes top 5 items by significance, new sources, pipeline health, quality stats
4. Weekly source health report shows erroring/stale/dead sources and top producers
5. Coverage gap alerts fire when 3+ auto_miss for same keyword accumulate in 7 days
6. Breaking change items trigger instant Slack notification during classify_items
7. MCP server fires auto_miss on LOW/0 results and query_refinement on sequential queries within 5-minute window
**Plans:** 3/3 plans complete

Plans:
- [ ] 33-01 (Wave 1): Auto-feedback endpoint + expanded report types + source tier adjustment cron (FL-01, FL-02, FL-03)
- [ ] 33-02 (Wave 1): Enhanced daily digest + weekly source health + coverage gap alerts + breaking change instant notification (FL-04, FL-05, FL-06, FL-07)
- [ ] 33-03 (Wave 2): MCP server query chain tracking + auto-miss fire-and-forget (FL-08, FL-09)

### Phase 34: Search & Ranking Quality Fixes

**Goal:** Fix 5 pipeline issues identified during evaluation testing: expand breaking type to include major significance, pass query to action-items endpoint, add source-type diversity caps in feed, increase quality weight in RRF search, add source-scoped query parameter
**Requirements**: RANK-01, RANK-02, RANK-03, RANK-04, RANK-05
**Depends on:** Phase 33
**Success Criteria** (what must be TRUE):

1. `type=breaking` returns items with `significance IN ('breaking', 'major')` that match the query — not just breaking-classified items (RANK-01)
2. `/v1/action-items` accepts a `q` parameter and filters results by query relevance when provided (RANK-02)
3. Feed results include at most N items per source_type before ranking, preventing social posts from flooding the feed (RANK-03)
4. RRF search weights are semantic=0.25, fulltext=0.35, quality=0.35, freshness=0.05 — quality has equal weight to fulltext (RANK-04)
5. `/v1/feed` and `/v1/search` accept a `source` filter parameter to scope results to a specific source (RANK-05)
**Plans:** 2/2 plans complete

Plans:
- [ ] 34-01 (Wave 1): Backend API fixes — RRF weight update, source-type diversity caps, action-items query filter, source filter param (RANK-02, RANK-03, RANK-04, RANK-05)
- [ ] 34-02 (Wave 2): MCP client updates — breaking significance widened, action-items query passthrough (RANK-01, RANK-02)

### Phase 35: Unified CLI + Skills.sh Distribution

**Goal:** Transform intel-overdrive-mcp into a unified overdrive-intel npm package (CLI + MCP server), fix all critical distribution bugs, and create agent-neutral SKILL.md files for skills.sh distribution
**Requirements**: DIST-CLI-01, DIST-CLI-02, DIST-CLI-03, DIST-CLI-04, DIST-SKILLS-01, DIST-SKILLS-02, DIST-FIX-01, DIST-FIX-02, DIST-FIX-03, DIST-FIX-04
**Depends on:** Phase 34
**Plans:** 3/3 plans complete

Plans:
- [x] 35-01 (Wave 1): Package rename + shared config + DIST-FIX issues — rename to overdrive-intel, extract config.ts, fix version/env/errors, update server.json (DIST-CLI-01, DIST-CLI-04, DIST-FIX-02, DIST-FIX-03, DIST-FIX-04)
- [ ] 35-02 (Wave 2): CLI commands + setup flow — overdrive-intel setup/search/feed/breaking, update setup.sh for npm registry install (DIST-CLI-02, DIST-CLI-03, DIST-FIX-01)
- [ ] 35-03 (Wave 1): Agent-skills repo + SKILL.md rewrite — agent-neutral SKILL.md files for skills.sh (DIST-SKILLS-01, DIST-SKILLS-02)

### Phase 36: CLI Completeness + Result Quality

**Goal:** Close the gap between API (8 endpoint types) and CLI (3 commands). Add 5 missing commands, fix feed ranking monotony, add dates to all output, implement smart best-practice routing, and update all SKILL.md files to reflect the complete command set.
**Requirements**: CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, CLI-06, QUAL-01, QUAL-02, QUAL-03, SKILL-01, SKILL-02
**Depends on:** Phase 35
**Plans:** 1/2 plans executed

Plans:
- [ ] 36-01 (Wave 1): CLI commands + quality fixes — add briefing/library/similar/action-items/status commands, enhance formatting with dates, smart best-practice routing, feed ranking fix, --tag flag (CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, CLI-06, QUAL-01, QUAL-02, QUAL-03)
- [ ] 36-02 (Wave 2): SKILL.md updates — fix false --type briefing promise, update all skill files with complete 8-command table (SKILL-01, SKILL-02)
