# Content Coverage Gap Investigation

**Date**: 2026-03-20
**Database**: Neon project `[REDACTED]`
**Corpus**: 4,191 processed items, 19,029 filtered items, 520 sources

---

## Executive Summary

The pipeline has a **systemic over-filtering problem** caused by the `RELEVANCE_THRESHOLD` being set to 0.8 while the average relevance score for filtered items with any score is 0.58. This single threshold is responsible for 1,265 items with relevance scores above 0.7 being wrongly filtered — including high-value content about OpenAI Codex releases, PydanticAI versions, LangChain releases, CrewAI releases, and Cursor ecosystem updates. The coverage is heavily skewed toward MCP/Claude (2,317 + 526 tags) and OpenAI news blog posts (658 items), while agent frameworks and competing tools have near-zero processed items despite having active sources.

---

## Topics With Good Coverage

| Topic            | Tag Count | Primary Sources                                                                        | Notes                                                       |
| ---------------- | --------- | -------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| MCP              | 2,317     | bulk:awesome-lists (895), bulk:smithery-mcp (684), bulk:npm-mcp (340)                  | Bulk imports dominate; well covered                         |
| Claude Code      | 526       | github:claude-extensions (100), github:claude-code-ecosystem (51), bluesky search (59) | Strong organic coverage                                     |
| OpenAI (general) | 242       | rss:openai-news (658 processed / 229 filtered)                                         | Blog posts pass well, but developer-facing content filtered |
| Security         | 125       | Various                                                                                | Healthy cross-source signal                                 |
| Grok/xAI         | 61        | rss:xai-status (81)                                                                    | Status-heavy but passable                                   |
| Windsurf         | 52        | rss:windsurf-changelog-3p (41)                                                         | Changelog-driven                                            |
| Gemini           | 47        | rss:gemini-cloud-notes (29)                                                            | Adequate                                                    |

**Why these work**: Bulk import sources (awesome lists, Smithery, npm) bypass the relevance gate or produce content that naturally scores high against the MCP/Claude-centric reference set. The OpenAI News RSS blog produces long-form content that embeds well.

---

## Topics With Poor/Zero Coverage

### 1. CrewAI — 2 processed items (100% filter rate on release source)

| Source                       | Total | Processed | Filtered | Filter % |
| ---------------------------- | ----- | --------- | -------- | -------- |
| rss:gh-crewai                | 10    | 0         | 10       | 100%     |
| github-deep:crewAIInc/crewAI | 3     | 0         | 3        | 100%     |
| rss:pypi-crewai              | 1     | 0         | 1        | 100%     |

**Root cause**: All CrewAI release titles are bare version numbers ("1.11.0", "1.10.2rc2") which embed poorly and score 0.53-0.57 against the reference set. The threshold of 0.8 rejects them all. The 2 processed items came via other sources (r/langchain, awesome lists).

### 2. PydanticAI — 2 processed items (90% filter rate)

| Source                           | Total | Processed | Filtered | Filter % |
| -------------------------------- | ----- | --------- | -------- | -------- |
| rss:gh-pydantic-ai               | 10    | 1         | 9        | 90%      |
| github-deep:pydantic/pydantic-ai | 3     | 0         | 3        | 100%     |
| rss:pypi-pydantic-ai             | 1     | 1         | 0        | 0%       |

**Root cause**: Same as CrewAI. Release titles like "v1.69.0 (2026-03-16)" score 0.68-0.72 — closer to threshold but still filtered. These are actively maintained releases with relevance scores of 0.71+ being thrown away.

### 3. LangChain/LangGraph — 8 processed items (massive filtering)

| Source                             | Total | Processed | Filtered | Filter % |
| ---------------------------------- | ----- | --------- | -------- | -------- |
| awesome:langchain                  | 403   | 4         | 400      | 99.3%    |
| rss:gh-langchain                   | 11    | 2         | 9        | 81.8%    |
| github-deep:langchain-ai/langchain | 3     | 0         | 3        | 100%     |
| github-deep:langchain-ai/langgraph | 3     | 0         | 3        | 100%     |
| rss:reddit-langchain               | 37    | 3         | 34       | 91.9%    |
| rss:so-langchain                   | 26    | 4         | 22       | 84.6%    |
| scraper:langchain-blog             | 13    | 2         | 11       | 84.6%    |
| rss:devto-langchain                | 12    | 0         | 12       | 100%     |

**Root cause**: Multiple compounding issues:

- Release titles are bare version strings (same issue as CrewAI)
- awesome:langchain items are tool names without context ("Codel", "AgentRun", "Memary") — high relevance scores (0.72-0.73) but still under 0.8
- Reddit/SO content scored 0.52-0.56 — too conversational for the reference set
- LangChain release items ("langchain==1.2.11") score 0.69 — filtered despite being version-release intel

### 4. AutoGen — 10 processed items (mixed)

| Source                        | Total | Processed | Filtered | Filter % |
| ----------------------------- | ----- | --------- | -------- | -------- |
| rss:gh-autogen                | 10    | 10        | 0        | 0%       |
| github-deep:microsoft/autogen | 3     | 0         | 3        | 100%     |
| rss:pypi-autogen-agentchat    | 2     | 0         | 2        | 100%     |
| rss:pypi-autogen-core         | 1     | 0         | 1        | 100%     |

**Root cause**: GitHub releases pass (likely richer descriptions) but PyPI and deep-scan items filtered. The 10 processed items all come from `rss:gh-autogen` suggesting its release notes contain enough description to score above 0.8.

### 5. Cursor — 33 tag count but significant filtering

| Source                       | Total | Processed | Filtered | Filter % |
| ---------------------------- | ----- | --------- | -------- | -------- |
| rss:cursor-changelog-anyfeed | 56    | 14        | 42       | 75%      |
| rss:reddit-cursor            | 68    | 7         | 56       | 82.4%    |
| rss:reddit-cursorai          | 30    | 3         | 27       | 90%      |
| rss:cursor-status            | 25    | 2         | 23       | 92%      |
| rss:hn-cursor                | 14    | 2         | 12       | 85.7%    |

**Root cause**: Reddit/HN content is too conversational. Changelog entries that are terse get filtered. Several high-value items like "How Cursor uses GPT-5" (score 0.72) and "Trialing Composer 2 w/ Pro+" (score 0.85) are being filtered.

### 6. Copilot — 15 tag count, SO source 100% filtered

| Source                 | Total | Processed | Filtered | Filter % |
| ---------------------- | ----- | --------- | -------- | -------- |
| rss:so-copilot         | 30    | 0         | 30       | 100%     |
| rss:reddit-copilot     | 31    | 3         | 28       | 90.3%    |
| rss:gh-copilot-cli     | 12    | 1         | 10       | 83.3%    |
| rss:hn-copilot         | 19    | 8         | 10       | 52.6%    |
| bluesky:search-copilot | 39    | 5         | 31       | 79.5%    |

**Root cause**: SO copilot questions all have relevance_score = 0 (likely no embedding or extreme mismatch). Reddit and Bluesky over-filtered at 0.8 threshold. HN copilot performs best because HN posts tend to have richer descriptions.

### 7. OpenAI Developer Tools — SDK releases 100% filtered

| Source                                  | Total | Processed | Filtered | Filter % |
| --------------------------------------- | ----- | --------- | -------- | -------- |
| rss:gh-openai-codex                     | 17    | 0         | 17       | 100%     |
| rss:gh-openai-node                      | 10    | 0         | 10       | 100%     |
| rss:gh-openai-python                    | 10    | 1         | 9        | 90%      |
| github-deep:openai/openai-agents-python | 4     | 0         | 4        | 100%     |
| rss:devto-openai                        | 21    | 0         | 21       | 100%     |

**Critical finding**: OpenAI Codex CLI release "0.116.0" scored **0.849** (the second-highest filtered score in the entire database!) and was still filtered. This suggests the filter ran before the score was fully computed, or there's a race condition. Items like "Introducing AgentKit, new Evals, and RFT for agents" (0.72) and "New tools and features in the Responses API" (0.72) are exactly the developer-facing intel the pipeline should capture.

### 8. Embedding/RAG Topics — nearly invisible

| Tag               | Count |
| ----------------- | ----- |
| embedding         | 1     |
| embeddings        | 7     |
| vector-database   | 9     |
| rag               | 23    |
| structured-output | 1     |
| function-calling  | 2     |
| tool-calling      | 4     |

**Root cause**: No dedicated sources for RAG/embedding content. These tags only appear incidentally on items from other sources. There are no feeds for vector DB changelogs (Pinecone, Weaviate, Chroma, Qdrant), no embedding model release feeds, and no RAG-focused community sources.

### 9. Dormant Sources (registered but never ingested)

12 sources relevant to gap topics exist but have produced zero items:

- `sitemap:openai-docs` — OpenAI Documentation
- `sitemap:openai-agents-docs` — OpenAI Agents SDK Docs
- `scraper:openai-changelog` — OpenAI Changelog
- `scraper:cursor-blog` — Cursor Blog
- `scraper:cursor-changelog` — Cursor Changelog
- `rss:gh-cursor-discussions` — Cursor GitHub Discussions
- `rss:cursor-changelog-3p` — Cursor Changelog (3rd party)
- `rss:gh-copilot-changelog` — GitHub Copilot Changelog
- `scraper:aider-history` — Aider Release History
- `rss:openai-research-3p` — OpenAI Research (3rd party RSS)
- `rss:openai-blog` — OpenAI Blog (only 1 filtered item)
- `rss:pypi-langchain-core` — PyPI langchain-core Releases

**Root cause**: These are likely scraper/sitemap sources that either (a) have broken adapters, (b) were never scheduled for ingestion, or (c) produce content that immediately fails parsing.

---

## Root Cause Analysis

### Primary: RELEVANCE_THRESHOLD too aggressive (0.8)

- Average processed item score: **0.71** (min 0.29, max 0.95)
- Average filtered item score (with score > 0): **0.58** (min 0.43, max 0.85)
- **1,265 items scored above 0.7 but were filtered** — this is signal loss
- Items like "Introducing AgentKit, new Evals, and RFT for agents" (0.72) and PydanticAI v1.69.0 (0.72) are clearly relevant

The threshold was raised from the research-recommended 0.5 to 0.8, which is too aggressive for sources that produce terse titles (release feeds, PyPI, GitHub deep scans).

### Secondary: Reference set biased toward MCP/Claude

- 125 reference items (95 positive, 30 negative)
- Reference set was likely seeded with MCP server descriptions and Claude Code content
- This causes the gate to systematically favor MCP-adjacent content and penalize everything else
- Agent framework releases and competing tool content score lower because the positive exemplars don't cover these topics

### Tertiary: Release-feed titles lack context

- GitHub Releases and PyPI feeds produce titles like "1.11.0" or "langchain==1.2.12"
- These embed as near-meaningless vectors
- Even with a lower threshold, bare version strings won't score well
- The pipeline needs to use the release body/description, not just the title, for embedding

### Quaternary: 12+ registered sources never producing items

- Scraper and sitemap adapters appear to be broken or unscheduled
- This represents lost coverage for OpenAI docs, Cursor blog, Copilot changelog, and Aider history

---

## Recommendations

### Priority 1: Lower RELEVANCE_THRESHOLD to 0.65

**Impact**: Immediate. Recovers ~1,000+ wrongly filtered items.
**Risk**: Some noise increase. Mitigate by expanding the negative reference set.
**Action**: Change `RELEVANCE_THRESHOLD` in `src/core/config.py` from 0.8 to 0.65. Alternatively, set via env var `RELEVANCE_THRESHOLD=0.65` in production.

### Priority 2: Expand the positive reference set

**Impact**: Improves scoring accuracy for agent frameworks and competing tools.
**Action**: Add 20-30 positive reference items covering:

- Agent framework releases (CrewAI, LangChain, PydanticAI, AutoGen, OpenAI Agents SDK)
- Competing IDE tools (Cursor updates, Copilot features, Aider releases)
- RAG/embedding topics (vector DB updates, embedding model releases)
- Developer SDK releases (openai-python, openai-node)

### Priority 3: Enrich release-feed items before embedding

**Impact**: Fixes the bare-version-title problem for all release sources.
**Action**: In the ingestion pipeline, prepend the source name to the title before embedding. E.g., "1.11.0" from `rss:gh-crewai` becomes "CrewAI Release 1.11.0". Or fetch the release body and use it as the embed text.

### Priority 4: Fix dormant sources

**Impact**: Unlocks 12+ registered sources that produce zero items.
**Action**: Investigate and fix in order of value:

1. `scraper:openai-changelog` — high-value developer intel
2. `rss:gh-copilot-changelog` — competitor tracking
3. `scraper:cursor-blog` — competitor tracking
4. `scraper:cursor-changelog` — competitor tracking
5. `sitemap:openai-docs` / `sitemap:openai-agents-docs` — reference documentation
6. `rss:openai-blog` — only 1 item ever ingested
7. `scraper:aider-history` — tool tracking
8. `rss:cursor-changelog-3p` — alternate feed
9. `rss:pypi-langchain-core` — release tracking

### Priority 5: Add dedicated RAG/embedding sources

**Impact**: Fills the embedding/RAG content gap.
**Action**: Add new sources:

- Pinecone blog/changelog RSS
- Weaviate blog RSS
- Chroma releases (GitHub)
- Qdrant blog RSS
- Voyage AI changelog
- OpenAI embedding model announcements (filter from existing openai-news)
- dev.to tags: `#rag`, `#vectordatabase`, `#embeddings`

### Priority 6: Reprocess high-score filtered items

**Impact**: Recovers historical content already in the database.
**Action**: After lowering the threshold, run a one-time migration:

```sql
UPDATE intel_items
SET status = 'queued'
WHERE status = 'filtered'
  AND relevance_score >= 0.65;
```

This would recover items that were scored but rejected at the old 0.8 threshold.

### Priority 7: Per-source threshold overrides

**Impact**: Long-term fix for source-type variance.
**Action**: Allow sources to declare a custom relevance threshold. Release feeds (PyPI, GitHub Releases) could use 0.5 since they're pre-qualified by source. Community sources (Reddit, SO, Bluesky) could keep a higher threshold of 0.7 to filter noise.

---

## Anomalies Found

1. **Items with relevance_score = 0**: All `rss:so-copilot` items (30) and several `github-deep:` items have `relevance_score = 0`. This suggests they were filtered before embedding, or their embeddings failed silently.

2. **OpenAI Codex 0.116.0 scored 0.849 but was filtered**: This is the second-highest relevance score in the entire filtered set. Something beyond the threshold may be filtering this item — possibly a duplicate check or a secondary filter stage.

3. **rss:openai-news has 658 processed / 229 filtered**: A 26% filter rate on this source seems reasonable, but items like "Introducing AgentKit" and "New tools and features in the Responses API" being filtered indicates the threshold is too high even for this well-performing source.

4. **awesome:langchain has 400 filtered / 4 processed**: 99.3% filter rate on what should be a curated list of LangChain ecosystem tools. The awesome-list items (tool names without descriptions) don't embed well against the reference set.
