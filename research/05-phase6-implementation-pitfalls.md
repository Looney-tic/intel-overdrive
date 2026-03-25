# Phase 6 Implementation Pitfalls

Research memo for the active Phase 6 scope: alerting, quality scoring, and feedback.

Date: 2026-03-14

## Scope

Focus areas requested:

- Webhook retry patterns
- Alert cooldown race conditions
- Quality score computation gotchas
- Feedback-loop implementation traps

This note is intentionally implementation-heavy. It assumes the current repo state:

- `AlertRule` stores `keywords`, `delivery_channels`, and `cooldown_minutes`
- `Feedback` stores `report_type`, `item_id`/`url`, `api_key_id`, and `notes`
- Redis is already in use for atomic cooldowns
- The current scoring service only covers relevance, not repository/package quality

## Executive Summary

The three places most likely to produce silent bad behavior in Phase 6 are:

1. Alert delivery state: retries without idempotency create duplicate Slack posts; cooldowns without a durable delivery record create dropped alerts.
2. Quality inputs: GitHub metadata has several easy-to-misread fields and some statistics endpoints are asynchronous, cached, and sometimes incomplete.
3. Feedback storage: if feedback is keyed to API keys instead of users, normalized inconsistently, or not tied to score/model versions, it becomes hard to trust and nearly impossible to use for calibration.

Recommended Phase 6 defaults:

1. Add a durable `alert_deliveries` outbox table and make Redis cooldowns suppression-only, not the source of truth.
2. Retry only on transport failures, `429`, and `5xx`; treat Slack `400`/`403`/`404` webhook errors as terminal unless explicitly classified otherwise.
3. Build quality scores from low-cost, well-defined signals first: archived status, recent push recency, star/fork counts, GitHub community profile health, release recency, and basic contributor/activity counts.
4. Store feedback by `user_id` plus normalized target hash, keep raw events immutable, and compute aggregates separately.

## 1. Alerting Pitfalls

### 1.1 Webhook retries are not enough; you need delivery state

Incoming Slack webhooks are simple HTTP POST endpoints, but they are not idempotent and they do not provide a message identifier on success. If a worker times out after Slack accepted the message, retrying can create a duplicate post and you cannot delete it via the same webhook API.

Implication:

- A plain "try HTTP, retry on exception" loop is not safe.
- Redis cooldown keys alone are not enough to model delivery truth.

Recommended pattern:

1. Persist one `alert_deliveries` row per `(user_id, rule_id, item_id, channel_fingerprint)`.
2. Put a unique constraint on that tuple.
3. Drive delivery with an outbox state machine:
   - `pending`
   - `sending`
   - `sent`
   - `retryable_failed`
   - `terminal_failed`
4. Claim rows using database coordination (`FOR UPDATE SKIP LOCKED` or an atomic status transition).
5. Only after a confirmed success should the row become `sent` and the long cooldown key be set.

If you skip the outbox and only use Redis cooldown keys:

- Setting cooldown before HTTP send risks losing the alert if Slack returns `429` or `500`.
- Setting cooldown after HTTP send risks duplicates if the worker crashes between send and set.

### 1.2 Retry classification must be explicit

Slack’s docs are unusually clear here: incoming webhooks return expressive status codes and named error strings. That means you should not retry every non-200.

Safe retry bucket:

- Network timeout / connection reset / DNS / transient TLS failures
- HTTP `429` with `Retry-After`
- HTTP `5xx`

Terminal bucket:

- HTTP `400` with `invalid_payload`, `no_text`, `too_many_attachments`
- HTTP `403` with `action_prohibited`
- HTTP `404` / invalid or revoked webhook (`no_active_hooks`, `no_service`, `channel_not_found`, `user_not_found`)
- Archived or disabled destinations (`channel_is_archived`, `team_disabled`)

Operational rule:

- Retryable failures should increment `attempt_count`, capture `next_attempt_at`, and preserve the delivery row.
- Terminal failures should disable or flag the affected destination and surface that in `dti alerts status` later.

Recommended retry policy:

- Max 5 attempts
- Exponential backoff with jitter
- Respect Slack `Retry-After` exactly on `429`
- Per-destination throttling targeting `<= 1` message/second

### 1.3 Slack webhook URLs are secrets

Slack explicitly treats the webhook URL itself as a secret and revokes leaked URLs. That means:

- Never log full webhook URLs
- Never echo them back in API responses
- Redact them in structs, tracing spans, and exception text
- Prefer encrypted-at-rest storage if you keep raw URLs server-side

For UI/CLI status output, store and display only a fingerprint:

- Example: `slack: T123/C456/…/a1f9`

### 1.4 Incoming webhooks are intentionally limited

Incoming webhooks do not let you:

- Delete a posted message
- Override the default channel/user/icon selected at install time
- Obtain the message `ts` from the send response

Implication:

- Duplicate alerts are harder to recover from
- Threaded follow-ups require extra APIs/events
- "Test alert" flows should use a distinct install/channel, not the production channel

For Phase 6 that argues for:

- Very conservative retry rules
- Tight idempotency
- A simple message format with stable blocks/text

## 2. Cooldown Race Conditions

### 2.1 The bad pattern is `EXISTS -> send -> SET`

Any non-atomic read-then-write cooldown flow is race-prone under multiple workers:

1. Worker A checks "no cooldown"
2. Worker B checks "no cooldown"
3. Both send
4. Both set cooldown

You already avoid this for source polling with `SET ... NX EX`. Use the same discipline for alert suppression keys.

### 2.2 Redis cooldowns should be suppression, not ownership

Use two distinct concepts:

1. Delivery ownership / uniqueness: database row
2. Suppression window: Redis TTL key

Suggested key shapes:

- Send lock: `alert:lock:{delivery_id}`
- Suppression key: `alert:cooldown:{user_id}:{rule_id}:{channel_hash}:{topic_hash}`

Where `topic_hash` should be the thing the cooldown actually means. Examples:

- Per exact item: same item should not page twice
- Per keyword cluster: same topic should not spam several alerts in an hour
- Per breaking-change slug: same changelog event should not repeat for every mirror source

Do not make cooldown overly coarse:

- `alert:cooldown:{rule_id}` is usually wrong
- It can suppress unrelated alerts for the same rule

### 2.3 Be careful when updating TTLs

Redis `SET` discards any previous TTL on success. Redis `EXPIRE`/`PEXPIRE` also supports `NX`, `XX`, `GT`, and `LT`, which matters if you later add snooze or cooldown extension behavior.

Concrete trap:

- If you overwrite a cooldown key with plain `SET`, you may accidentally remove or reset its expiry semantics.

Concrete use:

- Fixed cooldown window: `SET key value EX ttl NX`
- Extend only forward: `EXPIRE key ttl GT`
- Shorten only under explicit operator action: `EXPIRE key ttl LT`
- User-timezone digest windows: consider `EXPIREAT`

### 2.4 Crash windows still exist even with Redis

`SET NX EX` prevents concurrent entry, but it does not solve:

- Worker crashes after Slack success but before DB `sent`
- Worker crashes after DB `sending` but before HTTP call
- Retry worker sees stale `sending` rows forever

Recommended handling:

1. `sending` rows need `claimed_at`, `claim_expires_at`, and `worker_id`
2. Reaper job resets stale `sending` rows back to `retryable_failed` or `pending`
3. Delivery attempts should record:
   - attempt number
   - request start/end
   - HTTP status
   - parsed error code/body

Without this, alert duplication and alert loss will look identical in logs: "missing alert."

## 3. Quality Score Computation Gotchas

### 3.1 GitHub "watchers" and "stars" are easy to double-count

GitHub’s docs are explicit:

- `watchers`, `watchers_count`, and `stargazers_count` are star counts
- `subscribers_count` is the watcher count

If you sum stars and watchers naively from repo payloads, you can count the same popularity signal twice.

Recommendation:

- Use `stargazers_count` as the popularity signal
- Use `subscribers_count` separately only if you intentionally want a "people following activity" signal

### 3.2 Repository statistics endpoints are not cheap or synchronous

GitHub repository statistics endpoints have several non-obvious properties:

- They may return `202 Accepted` until GitHub finishes background computation
- Results are cached by the default branch SHA
- A push to the default branch invalidates that cache
- Some endpoints return `422` for large repos
- Statistics exclude merge commits; contributor stats also exclude empty commits
- Contributor activity endpoints can return zeros for additions/deletions in large repos

Implication:

- Do not build Phase 6 quality scoring in the hot ingest path around `/stats/*`
- Treat those endpoints as optional enrichment, not core truth

Safer first-pass signals:

- `archived`
- `fork`
- `pushed_at`
- `stargazers_count`
- `forks_count`
- `open_issues_count` only with care
- release/tag recency
- community profile health

### 3.3 Issue counts can be polluted by pull requests

GitHub’s Issues API treats pull requests as issues. If you measure open issue load, maintainer responsiveness, or comment activity from issue endpoints without filtering PRs, the metric is wrong.

Recommendation:

- If you query issue lists/events/comments, explicitly inspect the `pull_request` marker
- Keep issue responsiveness and PR responsiveness as separate components

### 3.4 Community profile health is a good cheap signal

GitHub exposes a `community/profile` endpoint with:

- `health_percentage`
- detected license
- code of conduct
- README / CONTRIBUTING / ISSUE_TEMPLATE / PULL_REQUEST_TEMPLATE presence

That is a much cleaner v1 input than scraping repository contents ad hoc.

Recommended component split for v1 quality:

- Maintenance: recent push, recent release, archived status
- Community hygiene: community profile `health_percentage`
- Popularity: stars/forks, log-scaled
- Build hygiene: CI/check signal only if cheap and available
- Safety flags: archived, disabled, or obviously broken install metadata

### 3.5 Missing data must not mean bad quality

This is the most common scoring mistake.

Examples:

- A new but legitimate repo may have no releases yet
- A skill repo may not use GitHub Actions
- A package may mirror elsewhere and have limited GitHub activity

If you encode every missing field as `0.0`, quality scores collapse into "we failed to enrich it."

Recommendation:

- Track both `component_score` and `component_confidence`
- Use null-aware weighting:
  - score over available components
  - reduce confidence when components are missing
- Expose both `quality_score` and `quality_confidence`

### 3.6 Archived repositories should be a hard penalty

GitHub archived repos are read-only and explicitly indicate they are no longer actively maintained.

Recommended rule:

- Archived repo: cap maintenance component very low or mark as ineligible for "recommended" unless the item is purely historical documentation

### 3.7 Rate limits and concurrency shape the scoring design

GitHub advises against concurrent REST requests and recommends serial requests to avoid secondary rate limits. It also recommends conditional requests using `ETag`/`Last-Modified`, with `304` responses not counting against the primary limit when properly authorized.

Implication:

- Quality enrichment needs its own queue, separate from ingest
- Batch and cache aggressively
- Prefer one repository metadata fetch plus one optional community profile fetch
- Delay expensive per-repo enrichment instead of blocking feed availability

## 4. Feedback Pitfalls

### 4.1 `api_key_id` is not the same as user identity

The current `Feedback` model ties feedback to `api_key_id`. That creates fragmentation:

- one human using two keys becomes two "users"
- key rotation breaks longitudinal analysis
- server-side profile feedback and CLI feedback do not naturally unify

Recommendation:

- Store `user_id` on feedback
- Optionally also store `api_key_id` for audit/tracing

### 4.2 Feedback needs idempotency and normalization

Two failure modes:

1. Client retries create duplicate rows
2. The same target is reported through slightly different URLs

Recommended schema additions:

- `normalized_target_hash`
- `user_id`
- `report_type`
- `item_id` nullable
- `raw_url` nullable
- `url_normalized` nullable

Recommended unique constraint:

- `(user_id, report_type, normalized_target_hash)`

For `miss` reports, normalize URLs with the same canonicalization path used in ingestion/dedup.

### 4.3 Keep raw feedback immutable; compute aggregates separately

Do not directly mutate item scores when feedback arrives.

Instead:

1. Insert immutable raw feedback events
2. Aggregate them in a periodic job into an `item_feedback_stats` table/materialized view
3. Apply conservative scoring rules from those aggregates

Why:

- You preserve auditability
- You can recalculate as your weighting logic changes
- Abuse handling is easier

### 4.4 One loud user should not globally tank an item

If a single user submits repeated `noise` reports, that is a user-preference signal first, not a global truth signal.

Recommended split:

- Per-user suppression: immediate, strong
- Global score penalty: only after a threshold of distinct users

Example v1 rule:

- Immediately hide from the reporting user
- Apply global penalty only after `>= 3` distinct users or a strong ratio threshold

### 4.5 Feedback without context is hard to use for calibration

To improve ranking/classification later, feedback rows need the context of what the system believed at the time.

Recommended feedback snapshot fields:

- `relevance_score_at_feedback`
- `quality_score_at_feedback`
- `confidence_score_at_feedback`
- `ranking_position`
- `score_version`
- `classifier_version`
- `gate_version`
- `profile_hash`

Without this, you can count complaints but not explain or learn from them.

### 4.6 Abuse controls need to exist before public rollout

The current endpoint is rate-limited, which is necessary but not sufficient.

Add before wider usage:

- per-user/day caps
- note length limits
- duplicate suppression
- source IP / auth anomaly logging
- moderation path for obviously malicious notes

## 5. Recommended Phase 6 Implementation Order

### Step 1: Alert delivery foundation

Ship first:

- `alert_deliveries` table
- worker state machine
- Slack destination fingerprints
- retry classifier
- suppression keys in Redis

Do not ship first:

- direct webhook POST from rule evaluation code

### Step 2: Minimal quality score

Start with cheap, reliable inputs:

- archived status
- pushed-at recency
- recent release/tag recency if present
- stars/forks log-scale
- community profile health

Delay until later:

- repo statistics endpoints
- deep CI pass-rate analytics
- complex maintainer responsiveness calculations

### Step 3: Feedback as data pipeline, not just endpoint

Ship:

- normalized target hashing
- `user_id` on feedback
- immutable raw events
- per-user suppression
- aggregate job for global penalty candidates

Delay:

- automatic model retraining
- complex collaborative intelligence

## 6. Concrete Schema Additions

### `alert_deliveries`

Suggested fields:

- `id`
- `user_id`
- `rule_id`
- `item_id`
- `channel_type`
- `channel_fingerprint`
- `delivery_key`
- `status`
- `attempt_count`
- `claimed_at`
- `claim_expires_at`
- `sent_at`
- `last_error_code`
- `last_error_status`
- `last_error_body`
- `next_attempt_at`
- `created_at`
- `updated_at`

Important constraints:

- unique `(user_id, rule_id, item_id, channel_fingerprint)`

### `feedback`

Add:

- `user_id`
- `normalized_target_hash`
- `url_normalized`
- `relevance_score_at_feedback`
- `quality_score_at_feedback`
- `confidence_score_at_feedback`
- `score_version`
- `classifier_version`
- `gate_version`

Constraint:

- unique `(user_id, report_type, normalized_target_hash)`

### `intel_items`

Add for quality work:

- `quality_score_version`
- `quality_confidence`
- `quality_components` JSON

## 7. Sources

Primary sources used:

- Slack incoming webhooks: https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/
- Slack rate limits: https://docs.slack.dev/apis/web-api/rate-limits/
- Redis `SET`: https://redis.io/docs/latest/commands/set/
- Redis `EXPIRE`: https://redis.io/docs/latest/commands/expire/
- Redis `EXPIREAT`: https://redis.io/docs/latest/commands/expireat
- Redis `PTTL`: https://redis.io/docs/latest/commands/pttl/
- GitHub REST API best practices: https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- GitHub repository statistics: https://docs.github.com/en/rest/metrics/statistics
- GitHub watching vs starring: https://docs.github.com/en/rest/activity/watching
- GitHub community profile metrics: https://docs.github.com/en/rest/metrics/community
- GitHub issues endpoints: https://docs.github.com/en/rest/issues/issues
- GitHub archived repositories: https://docs.github.com/en/repositories/archiving-a-github-repository/archiving-repositories

## 8. Bottom Line

The main implementation decision is this:

- Treat alert delivery as a durable workflow with Redis-assisted suppression, not as "evaluate rule -> POST webhook".

If that is done correctly, the rest of Phase 6 becomes much easier:

- quality scoring can be gradual and low-risk
- feedback can be stored cleanly and used later
- duplicate and missing alerts become observable bugs instead of guesswork
