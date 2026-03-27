---
phase: 36-cli-completeness-result-quality
plan: 01
subsystem: cli
tags: [typescript, cli, intel-overdrive, mcp, context-pack, library-search, vector-similarity]

requires:
  - phase: 05-cli
    provides: Initial CLI with runSearch, runFeed, runBreaking and shared api-client

provides:
  - All 8 CLI commands for full API coverage (briefing, library, similar, action-items, status added)
  - Date output on all CLI items (published_at/created_at)
  - Smart best-practice query routing to library endpoint with fallback
  - Feed ranking fix using sort=score for >7 day windows
  - --tag flag on feed and breaking commands
  - parseBriefingArgs helper for --days/--topic flags

affects:
  - 36-02 (skill updates depend on CLI completeness)
  - agent-skills (skill docs reference CLI commands)

tech-stack:
  added: []
  patterns:
    - "CLI command pattern: requireApiKey() guard, apiGet() call, formatItem() output loop"
    - "Query routing: keyword detection before API dispatch with fallback to default endpoint"
    - "Sort strategy: use score sort for longer time windows to mix recency + significance"

key-files:
  created: []
  modified:
    - overdrive-intel-mcp/src/cli/commands.ts
    - overdrive-intel-mcp/src/index.ts

key-decisions:
  - "Used sort=score (not sort=significance) for feed windows >7 days — backend combines recency + significance to prevent monotony"
  - "Smart routing: 7 keyword triggers for library endpoint with zero-results fallback to /v1/search"
  - "Date formatting: extract published_at or created_at, format as YYYY-MM-DD, show on same line as significance when both present"
  - "parseBriefingArgs added as separate function (not merged with parseFeedArgs) to keep concerns clear"

patterns-established:
  - "New CLI commands follow: requireApiKey → apiGet → null check → header → item loop pattern"
  - "All formatItem calls pass showSignificance=true for new commands (status, similarity are important signals)"

requirements-completed: [CLI-01, CLI-02, CLI-03, CLI-04, CLI-05, CLI-06, QUAL-01, QUAL-02, QUAL-03]

duration: 12min
completed: 2026-03-27
---

# Phase 36 Plan 01: CLI Completeness + Result Quality Summary

**Added 5 missing CLI commands (briefing, library, similar, action-items, status), date output on all items, best-practice query routing, and --tag flag — bringing the CLI from 3 commands to full API coverage**

## Performance

- **Duration:** 12 min
- **Started:** 2026-03-27T14:00:00Z
- **Completed:** 2026-03-27T14:12:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Added runBriefing, runLibrary, runSimilar, runActionItems, runStatus — full /v1/* endpoint coverage
- formatItem now extracts published_at/created_at and shows YYYY-MM-DD date on every output item
- runSearch detects "best practice", "how to", "gotcha", "pattern", "guide", "tutorial", "recommend" and routes to /v1/library/search with fallback to /v1/search
- runFeed uses sort=score for days>7 to break ranking monotony (QUAL-01)
- runFeed and runBreaking accept --tag option passed through to API
- printHelp updated to show all 8 commands with full option syntax

## Task Commits

Each task was committed atomically:

1. **Task 1: Add 5 new command functions and enhance formatting in commands.ts** - `959304e` (feat)
2. **Task 2: Wire dispatch, update args parsing, and update help text in index.ts** - `7414b3a` (feat)

**Plan metadata:** (pending final commit)

## Files Created/Modified

- `overdrive-intel-mcp/src/cli/commands.ts` - Added 5 new exported async functions, formatDate helper, enhanced formatItem with dates, smart routing in runSearch, score sort in runFeed, tag support
- `overdrive-intel-mcp/src/index.ts` - Added 5 dispatch cases, parseBriefingArgs(), updated parseFeedArgs() with --tag, updated printHelp() with all 8 commands

## Decisions Made

- Used `sort=score` for >7 day feed windows — the backend's score field mixes recency and significance, breaking the monotony where only the highest-significance items show regardless of recency
- Smart routing uses 7 trigger keywords rather than fuzzy matching — keeps it deterministic and zero-cost
- Library results use a custom formatter (showing entry_type, tldr, topic_path, confidence) not the generic formatItem — library schema has no url/significance/published_at fields

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- CLI is now feature-complete for all 8 API endpoint types
- SKILL.md files in agent-skills/ still reference `--type briefing` (false promise) — Phase 36-02 addresses this
- Build passes clean with `npm run build` (zero TypeScript errors)

---
*Phase: 36-cli-completeness-result-quality*
*Completed: 2026-03-27*
