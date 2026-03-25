# Prefiltering, Auth & CLI Patterns Research

## User Workflow Profile Representation

### Recommended: Hybrid (config inference + tags + optional NL)

**Layer A — Config file inference (highest signal, zero friction)**
Tools like Specfy stack-analyser extract 700+ technologies from a repo by reading `package.json`, `go.mod`, `requirements.txt`, etc. The CLI client runs detection locally and sends structured profile (not raw files) to the server. No privacy concern, immediate accuracy.

**Layer B — Tag selection (onboarding supplement)**
Tags fill gaps config files miss: deployment targets (Vercel, AWS, self-hosted), workflow patterns (MCP servers, agentic coding, monorepo), role (full-stack, data engineering). Keep tag set small (20-40 curated tags).

**Layer C — Natural language (future phase)**
Embed user's free-text description, cosine similarity match against items. Adds embedding pipeline dependency — defer until simpler layers are working.

## Relevance Scoring Formula

```
final_score = (content_match * 0.40) + (authority * 0.25) + (engagement * 0.20) + (freshness * 0.15)
```

- **Content match (0.40)**: Tag overlap — `|item_tags INTERSECT user_tags| / |item_tags|`. Simple, explainable, no embedding pipeline needed
- **Authority (0.25)**: Official Anthropic (1.0), verified maintainer (0.7), popular >100 stars (0.5), unverified (0.2)
- **Engagement (0.20)**: `log10(max(1, stars + downloads))` — compressed range
- **Freshness (0.15)**: HN decay formula: `1 / (hours_since_publish + 2) ^ 1.8`

## Alert Threshold Calibration

From Dependabot, DevOps research:

- Teams receiving 50 alerts/week instead of 2,000 actually read them
- 23 minutes to recover focus after each interruption
- 28% of teams report forgetting to review critical alerts due to fatigue

**Recommended defaults:**

1. Daily digest, not real-time push
2. Default threshold: HIGH only (80th percentile relevance)
3. Severity tiers: Critical (immediate), High (daily), Medium (weekly), Low (on-demand only)
4. Auto-dismiss: content_match < 0.2 never shown
5. Volume cap: max 5 items per notification
6. Snooze/mute: per-category muting as negative signal

## Multi-Tenant Architecture

**For 100-1000 users: API keys + single database with tenant_id column.**

- Generate: `crypto.randomBytes(32).toString('hex')` prefixed with `dti_`
- Store: hash with SHA-256, never store plaintext
- Transmit: `Authorization: Bearer dti_abc123...`
- Rate limit: per-key, 100 req/hour

Why API keys over JWT: no token expiry/refresh, per-user revocation, standard for dev tools (Stripe, Vercel, Fly.io all do this).

Database: SQLite (<1000 users, single-server) or Postgres. Single database, row-level isolation via `user_id` FK.

## CLI Auth UX

```bash
# First-time setup
$ dti auth login
  No API key found. Get one at https://dti.example.com/settings/keys
  Paste your API key: dti_abc123...
  Stored in system keychain.

# CI/headless
$ export DTI_API_KEY=dti_abc123...
$ dti check  # works without interactive login

# Query
$ dti feed              # latest relevant findings
$ dti feed --all        # include low-relevance
$ dti profile           # show detected profile
$ dti profile --sync    # re-detect and push to server
```

Token storage priority: (1) env var, (2) system keychain, (3) `~/.config/dti/credentials` plaintext fallback with 0600 permissions.
