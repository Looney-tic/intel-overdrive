# Geo Dashboard Reusable Patterns

Analysis of patterns from ./geo-dashboard/ that can be reused or adapted.

## Directly Reusable Code

### Copy-Paste Ready

1. **URL normalization + hashing**: `backend/app/workers/ingest_rss.py` — strips UTM params, lowercases host, removes www, SHA-256 hash
2. **Content fingerprinting**: same file — SHA-256 of title+excerpt with HTML entity unescape, NFKD normalization
3. **Spend tracking**: `backend/app/services/spend_tracker.py` — Redis INCRBYFLOAT with 24h TTL, model pricing table
4. **Spend gating**: `backend/app/services/llm_client.py` — pre-flight check against daily hard limit ($50) and alert threshold ($20)
5. **Keyword matching**: `backend/app/services/alert_matcher.py` — simple case-insensitive substring matching (no FTS needed)
6. **Cooldown pattern**: Redis SET NX with TTL — atomic, race-condition-safe rate limiting
7. **Enum coercion**: `backend/app/workers/nlp_process.py` — gracefully handles LLM enum hallucinations

### Adapt for Smaller Scale

1. **ARQ worker dispatch**: simplify from 7 source types to 3-4
2. **Status transitions**: `raw → processing → processed | filtered | failed` — idempotent, safe retries
3. **Relevance scoring**: replace Voyage AI embeddings with keyword heuristic + source authority
4. **Alert rule evaluation**: keep keyword matching, add stdout delivery for CLI

### Don't Reuse (Domain-Specific)

- GEO taxonomy (geo_lever, ai_engine, pm_pillar)
- Vertical classification LLM call
- Personalized feed ranking (no user model needed in CLI initially)
- Brand config, infographic generation

## Architecture Simplifications

| Feature          | Geo-Dashboard                   | Overdrive-Intel                                          |
| ---------------- | ------------------------------- | -------------------------------------------------------- |
| Sources          | 7+ types                        | 3-4 (RSS, GitHub API, HN, MCP Registry)                  |
| Embedding        | Voyage AI 1024-dim              | Keyword heuristic (no embeddings)                        |
| Dedup layers     | 3 (URL, fingerprint, embedding) | 2 (URL, fingerprint)                                     |
| LLM taxonomy     | 6-level                         | 3-level (signal_type, category, confidence)              |
| User preferences | 8 fields                        | 2-3 fields (muted sources, muted keywords, profile tags) |
| Alerts           | Rule-based + anomaly detection  | Rule-based only                                          |
| API endpoints    | 20+ routes                      | 3-4 routes                                               |
| Delivery         | Slack, email, in-app SSE        | CLI stdout, optional Slack webhook                       |

## Recommended Architecture

```
Ingestion Layer (ARQ fast queue)
  ├─ poll_rss_feeds (cron every 2h)
  ├─ poll_github (cron every 4h — rate limit conscious)
  ├─ poll_hackernews (cron every 1h)
  └─ poll_mcp_registry (cron every 6h)

Filtering Layer (ARQ fast queue)
  └─ score_article_relevance (keyword heuristic, no embedding)

Processing Layer (ARQ slow queue)
  └─ process_article (Haiku, max_tokens=200, ~$0.0003/article)

Alert Layer (ARQ fast queue)
  └─ evaluate_alert_rules (keyword match + cooldown)

CLI API (FastAPI, minimal)
  ├─ GET /articles (with filtering)
  ├─ GET /signals (with date range)
  ├─ GET /status (spend, counts)
  └─ POST /profile (sync user profile)
```

## Cost Estimate

- Haiku classification: ~$0.0003/article
- Expected volume: 50-100 raw items/day, 5-10 after filtering
- Daily LLM cost: ~$0.003-0.03/day
- Monthly: <$1 in LLM costs
- Recommended daily limit: $5 (generous safety margin)
