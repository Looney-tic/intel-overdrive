# Overdrive Intel — Context Seed

All context gathered prior to project initialization. This document captures research findings, design discussions, and requirements from the initial brainstorming sessions.

## Origin

During a deep dive on Ben Tossell's newsletter (Mar 2026) and the [mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill) repo, we identified a gap in the Overdrive workflow framework: there's no structured, persistent way to monitor the Claude Code ecosystem for new tools, skills, best practices, model updates, and documentation changes. The existing maintenance skill's web research is "flaky" — unstructured WebSearch produces inconsistent, noisy results.

## Problem Statement

Overdrive users (and AI coding workflow users generally) are missing useful tools, skills, practices, and updates because:

1. The ecosystem moves fast — new repos, skills, MCP servers, model updates appear daily
2. Manual monitoring of 10+ sources is impractical
3. Generic web searches return mostly noise
4. No persistent tracking means the same things get re-discovered or missed entirely
5. Each user has different needs based on their stack and workflow — blanket results waste time

## Core Concept

A persistent intelligence pipeline (like geo-dashboard) that:

- Ingests from Claude Code ecosystem sources on a schedule
- LLM-classifies for relevance (filtering out 90%+ noise)
- Deduplicates and scores what remains
- Alerts users when something genuinely useful surfaces
- Is queryable from the terminal via CLI
- Does server-side prefiltering based on user workflow/config

## Key Requirements

### Multi-user / Public Service

- Not a personal tool — designed for others to use too
- CLI tool anyone can install and query
- Users authenticate with the service and get personalized, prefiltered results

### Server-side Prefiltering

- Users can register their workflow profile (what skills they use, what stack, what tools)
- The service filters findings based on their profile before returning results
- Similar to geo-dashboard's relevance scoring: tell it what you care about, get only relevant findings
- No local heavy processing needed — all ingestion, classification, filtering happens server-side

### CLI Interface

- Simple CLI tool that hits the service API
- Claude Code can call it via Bash (like gemini-query or codex-query)
- Commands like: `overdrive-intel search "MCP servers"`, `overdrive-intel whats-new --days 7`, `overdrive-intel trending`

### Alerting

- Notify users when high-value findings surface
- Slack webhook, email, or other notification channels
- Configurable alert thresholds per user

## Sources Identified

### Tier 1 — High signal, check frequently

1. **GitHub search** — repos with "claude code" + skill/hook/workflow/MCP, sorted by stars/recent
2. **awesome-claude-code repo** (hesreallyhim) — new entries since last scan
3. **Anthropic blog/changelog** — model updates, API changes, new features
4. **Claude Code docs** (code.claude.com) — new/changed pages
5. **skills.sh** (Vercel) — new published skills

### Tier 2 — Community signal

6. **Reddit** — r/ClaudeAI, r/ChatGPTCoding
7. **Hacker News** — Claude Code, agent workflow discussions
8. **X/Twitter** — @AnthropicAI, @claudeai, community builders

### Tier 3 — Ecosystem adjacent

9. **MCP server registries** — new servers
10. **npm trending** — Claude/MCP/agent packages
11. **Competing frameworks** — claude-code-workflows, dotagents, other orchestration repos
12. **YouTube** — Claude Code tutorials, workflow demos

## Classification Taxonomy (Draft)

Findings would be classified into:

- `skill` — new Claude Code skill
- `tool` — CLI tool, MCP server, or utility
- `practice` — workflow pattern, convention, or technique
- `model-update` — new model version, API change, pricing change
- `docs-change` — documentation update, new guide
- `repo` — notable repository (framework, template, example)
- `community` — discussion, thread, or post with actionable insights

## Architecture Direction (from brainstorm)

**Chosen: Lightweight dedicated service**

- Python service deployed on a server (Hetzner VPS or similar)
- Scheduled ingestion workers (cron or lightweight task queue)
- LLM classification with Haiku (cheap, fast, good enough for filtering)
- SQLite or Postgres for persistence
- Simple API endpoint for CLI queries
- Slack webhook for alerts
- CLI tool on PATH that queries the API

**Why not other approaches:**

- Pure Claude Code skill: flaky, no persistence, no scheduling, can't run in background
- MCP server: unnecessary complexity when CLI via Bash works fine
- Piggyback on geo-dashboard: couples unrelated concerns

## Reference Implementations

### Geo Dashboard (our own)

- Proven pipeline: ingest → dedup → classify → score → alert → deliver
- ARQ workers with cron scheduling
- 3-layer deduplication (URL hash → content fingerprint → embedding similarity)
- LLM classification with Anthropic Batch API (50% cost reduction)
- Signal detection with confidence scoring
- Multi-user with profile-based relevance scoring
- Daily briefings and alert system
- Source: `./geo-dashboard/`

### last30days-skill (mvanhorn)

- 10+ source adapters (Reddit, X, YouTube, HN, Polymarket, TikTok, Instagram, Bluesky, web)
- Entity-driven supplemental search (Phase 2: extract handles/hashtags → targeted follow-up)
- Platform-tuned engagement scoring (Reddit values comments, YouTube values views, etc.)
- Cross-source triangulation (Jaccard similarity for topic dedup across platforms)
- Vendored Bird Twitter GraphQL client (free X access, MIT licensed)
- Watchlist mode with SQLite + FTS5 persistence
- Morning briefing synthesis from accumulated data
- Source: https://github.com/mvanhorn/last30days-skill

## Design Principles (from Overdrive DNA)

- Zero unnecessary dependencies where possible
- Efficient and cost-conscious (Haiku for classification, batch where possible)
- Structured over vague (specific sources > "search the web")
- Server-side intelligence (don't waste client-side context on noise)
- CLI-first interface (Claude Code calls it like any other tool)
- Alert on signal, not noise (prefiltering is the core value)

## Open Questions for Brainstorm

1. What engagement/quality signals should boost a finding's score?
2. How should user workflow profiles be structured? (list of skills? CLAUDE.md upload? stack tags?)
3. Should there be a "community curation" layer where users can upvote/flag findings?
4. What's the right alert threshold — too many alerts = noise, too few = missed value?
5. Should the service also track deprecations and breaking changes?
6. Could this eventually feed back into Overdrive's update mechanism?
7. What about monitoring for security issues in skills/hooks people are using?
8. Should it support custom source lists per user (beyond the defaults)?
