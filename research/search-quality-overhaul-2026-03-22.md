# Search Quality Overhaul — Research Summary

**Date:** 2026-03-22
**Status:** Research complete, ready for implementation

## Problem Statement

Pipeline output quality graded C+ across 25 test queries. Core issues:

- Full-text search only, no semantic understanding — "MCP authentication" returns generic MCP tutorials
- No result diversification — 5 identical "build first MCP server" tutorials in top results
- Out-of-scope queries return garbage instead of "no results" (physics papers, Reddit jokes)
- MCP responses waste ~40% tokens on redundant JSON fields
- No TL;DR or confidence signal — LLM can't frame answers or detect low-quality results
- Library endpoint returns topic index stubs, not synthesized content

## Architecture Decision: Two-Pass Hybrid Search with RRF

Validated against Perplexity AI, Exa AI, ParadeDB, Supabase, and 8 production RAG systems.

### The Pattern

```
Query → Voyage embed (cached 24h) → Parallel: vector top-50 + fulltext top-50
    → Reciprocal Rank Fusion (semantic 0.40 + text 0.35 + quality 0.15 + freshness 0.10)
    → Cluster dedup (one per cluster_id)
    → Minimum confidence threshold
    → Structured markdown response with TL;DR
```

### Why RRF Over Other Approaches

- **RRF handles different score scales** without normalization (key advantage over linear combination)
- **Weights are tunable per query type** without query classification overhead
- **Pure SQL** — no external reranking API, no Python model dependency
- **Production numbers:** hybrid search improves precision from ~62% to ~84% (ParadeDB benchmark)

### What Was Considered and Rejected

| Approach                                 | Why Rejected                                                                                      |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Self-hosted cross-encoder (BGE reranker) | +130ms latency, Python model dependency, marginal gain over RRF for pre-curated corpus            |
| LLM query expansion (Haiku)              | 10x more expensive than Voyage embed, +200-500ms, expansion can hurt precision                    |
| ParadeDB BM25                            | Neon managed Postgres compatibility concern. Native ts_rank_cd adequate at this scale             |
| Query intent classifier                  | Hybrid search auto-balances — vector handles conceptual, text handles exact. No classifier needed |
| ColBERT late interaction                 | Overkill for 10K items. ColBERT shines at >1M scale                                               |
| MMR diversification                      | Cluster dedup handles 80% of problem with 20% effort. MMR is Phase 2 if needed                    |

## Implementation Plan (7 Changes, 3 Phases)

### Phase 1: Quick Wins (ship immediately, ~3 hours)

1. **Cluster dedup in search** — add cluster_id to SQL, apply existing \_collapse_clusters(). 30 min.
2. **Phrase proximity boost** — add phraseto_tsquery to ranking formula. 1 hour.
3. **Minimum confidence threshold** — return "no results" when best score < threshold. 1 hour.

### Phase 2: Hybrid Search Architecture (~6 hours)

4. **Two-pass hybrid search with RRF** — wire \_embed_concept() into search, write RRF SQL. 4-6 hours.

### Phase 3: Response Format (~6 hours)

5. **Drop redundant fields in MCP** — summary over excerpt, round scores, shorten dates. 2 hours.
6. **TL;DR + response quality signal** — generate summary header for all response types. 2-3 hours.
7. **Structured markdown format** — replace JSON with token-efficient markdown. 3-4 hours.

### Cost Impact

- Ongoing: ~$0.00002 per unique search query (Voyage embed, cached 24h). Negligible.
- No re-embedding, re-classification, or re-gating required.
- All changes are in the search/response layer.

## Test Queries and Expected Outcomes

| Query                        | Current Grade       | Expected After                               |
| ---------------------------- | ------------------- | -------------------------------------------- |
| MCP OAuth authentication     | B+                  | A (vector similarity to auth concept)        |
| SQLAlchemy 2.0 migration     | F (noise)           | "No results" (honest)                        |
| Claude Code hooks examples   | A-                  | A (phrase boost for "hooks examples")        |
| Tailwind v4 breaking changes | F (noise)           | "No results" (honest)                        |
| pgvector HNSW performance    | F (physics papers!) | "No results" or D (limited content)          |
| best MCP servers             | B+                  | A (diversified results across subtopics)     |
| Claude Code vs Cursor        | C-                  | B+ (vector similarity to comparison concept) |
| LangChain vs CrewAI          | D                   | B- (vector similarity + diversified)         |
| agent orchestration patterns | A                   | A (stays strong)                             |
| breaking changes (feed)      | A-                  | A (TL;DR + confidence signal)                |

## Key Files to Modify

- `src/api/v1/search.py` — main search endpoint (hybrid SQL, cluster dedup, confidence threshold)
- `src/api/v1/similar.py` — import \_embed_concept() from here
- `src/api/v1/feed.py` — apply cluster dedup consistency, response format
- `overdrive-intel-mcp/src/index.ts` — response format, TL;DR, field filtering, markdown output
- `src/api/v1/context_pack.py` — response format consistency

## Research Sources

- Perplexity AI architecture (multi-stage retrieval, sub-document indexing)
- Exa AI (end-to-end neural search, next-link prediction)
- ParadeDB (hybrid search in PostgreSQL, RRF implementation)
- Supabase (hybrid search docs, RRF with k=50)
- Elastic (MMR diversification, relevance thresholds)
- Qdrant (MMR diversity-aware reranking)
- arXiv 2408.04887 (Cosine Adapter for relevance filtering)
- Anthropic (context engineering for AI agents)
- Speakeasy/Cloudflare (MCP token reduction, code mode)
