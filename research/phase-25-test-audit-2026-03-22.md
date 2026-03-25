# Phase 25: Search Quality & Pipeline Fixes — Research

**Date:** 2026-03-22
**Source:** 12-agent comprehensive test audit + 2 deep research agents
**Status:** Research complete, ready for planning

## Issues Found (12-Agent Test Audit)

### P0: Pipeline Stall

- 20,778 items stuck at `embedded` status
- `gate_relevance` is FREE (pgvector cosine) but not processing
- `DAILY_SPEND_LIMIT` set to $2.00 (testing leftover) — only affects classify step
- Root cause: slow-worker may have silent gate_relevance failure, NOT a budget issue
- Budget math: $2/day can classify ~5,300 items; raising to $5 clears backlog in half a day

### P1: Noise Filter Too Permissive

- "recipe for chocolate cake" returns 5 results — 1 irrelevant SEO post contains all 3 stems
- Binary `_rrf_has_fulltext` check defeated by single match
- websearch_to_tsquery('english', 'recipe for chocolate cake') = `'recip' & 'chocol' & 'cake'`
- "claude code hooks" has 150 fulltext matches vs 1 for chocolate cake
- Fix: cosine distance threshold (>0.50) + minimum fulltext count (>=3)

### P1: Low-Quality Bulk Imports

- 252 Smithery items with <100 chars content have 0.736 avg relevance_score
- Smithery avg content: 231 chars vs 2,074 for non-Smithery
- No content-length filter in search queries
- No quality_score floor in search queries
- Fix: `AND LENGTH(COALESCE(content,'')) >= 100` + `AND quality_score >= 0.40`

### P1: Library Embeddings Missing

- **0 of 45 active library items have embeddings** — embedding generation silently fails
- `/library/search` ILIKE uses exact phrase matching (requires full substring match)
- "MCP best practices" ILIKE returns 0; individual words ("mcp") return 28
- Inline library in `/search` endpoint works (splits query into words)
- Fix: debug embedding generation + change ILIKE to per-word matching

### P2: Cross-Source Duplicates

- Same incident from 3 sources (Anthropic Status + 2 Reddit mirrors)
- Clustering detects them (same cluster_id) but doesn't collapse in feed
- 9,113 items have cluster_ids; top cluster has 117 items from 23 sources
- Fix: collapse by cluster_id in feed endpoint (search already does this)

### P2: Source Hygiene

- 32 deactivated sources cycling through recovery loop
- High-value sources (Vercel, Next.js, Copilot) may have changed URLs
- Fix: check URLs, add permanent death logic after 3 recovery cycles

### Not Issues (resolved during research)

- DB at 512MB → already on Neon paid tier (514MB of 10GB)
- /v1/breaking 404 → by design, MCP routes to feed?significance=breaking
- Unnecessary TS imports → cosmetic

## Production Data Points

| Metric              | Value                     |
| ------------------- | ------------------------- |
| Total items         | 29,831                    |
| Processed           | 8,676                     |
| Embedded (stuck)    | 20,778                    |
| Failed              | 293                       |
| Filtered            | 84                        |
| With embeddings     | 26,689                    |
| With clusters       | 9,113                     |
| Active sources      | 850                       |
| Erroring sources    | 40                        |
| Deactivated sources | 32                        |
| Daily spend limit   | $2.00 (needs increase)    |
| Library items       | 45 active                 |
| Library embeddings  | 0 (!)                     |
| Smithery items      | 539 (252 with <100 chars) |
| DB size             | 514 MB                    |
