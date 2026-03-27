---
phase: 35-unified-cli-skills-sh-distribution
plan: "02"
subsystem: overdrive-intel-mcp/cli
tags: [cli, distribution, setup, mcp-registration]
dependency_graph:
  requires: ["35-01"]
  provides: ["DIST-CLI-02", "DIST-CLI-03", "DIST-FIX-01"]
  affects: ["overdrive-intel-mcp/src/index.ts", "static/setup.sh"]
tech_stack:
  added: []
  patterns: ["dynamic-import for CLI isolation", "execFileSync for shell injection prevention", "node:https for key-less registration"]
key_files:
  created:
    - overdrive-intel-mcp/src/shared/api-client.ts
    - overdrive-intel-mcp/src/cli/commands.ts
    - overdrive-intel-mcp/src/cli/setup.ts
  modified:
    - overdrive-intel-mcp/src/index.ts
    - static/setup.sh
    - overdrive-intel-mcp/test.sh (local only, excluded from git by .gitignore)
decisions:
  - "Used dynamic import() for CLI modules in index.ts dispatch — prevents CLI code loading in MCP mode"
  - "Used node:https (not fetch) for key-less registration in setup.ts — avoids dependency on getApiKey()"
  - "Unknown commands exit(1) instead of falling through to MCP stdio mode — prevents terminal hang"
  - "test.sh not committed: intentionally excluded by project .gitignore as internal operational file"
metrics:
  duration: "4 min"
  completed_date: "2026-03-27"
  tasks: 2
  files: 5
---

# Phase 35 Plan 02: CLI Commands and Setup Flow Summary

**One-liner:** CLI layer for overdrive-intel: `setup` (key provisioning + MCP registration + SKILL.md install), `search/feed/breaking` (human-readable output), and npm registry distribution fix.

## What Was Built

### Task 1: CLI modules created

- **`overdrive-intel-mcp/src/shared/api-client.ts`** — Shared HTTP helpers (`apiGet`, `apiPost`) using Node.js `fetch` + config module. Exported for reuse by CLI commands.
- **`overdrive-intel-mcp/src/cli/commands.ts`** — `runSearch`, `runFeed`, `runBreaking` — human-readable numbered list output. Guards missing API key with `process.exit(1)`.
- **`overdrive-intel-mcp/src/cli/setup.ts`** — `runSetup` ports setup.sh logic to Node.js: key provisioning via `node:https`, `chmodSync` after `writeFileSync` (per project CLAUDE.md gotcha), MCP registration via `execFileSync`, SKILL.md bundled as string constant and written to `~/.claude/skills/overdrive-intel/SKILL.md`.

### Task 2: Wiring + distribution fix

- **`overdrive-intel-mcp/src/index.ts`** — Full dispatch block added: `setup`, `search`, `feed`, `breaking`, `--version`/`-v`, `--help`/`-h`. Unknown commands with args exit(1) instead of hanging in MCP stdio mode. Dynamic imports used to avoid loading CLI code in MCP mode. `parseFeedArgs()` parses `--days N` and `--type T`.
- **`static/setup.sh`** — Changed `npm install -g "${API_URL}/dl/intel-overdrive-mcp-0.9.0.tgz"` to `npm install -g overdrive-intel@latest` (DIST-FIX-01). Removed `-e OVERDRIVE_API_KEY` flag from MCP registration (fixes L-2). Added SKILL.md install step via heredoc.

## Verification Results

All 22 test.sh assertions pass:
- `node dist/index.js --version` → `0.9.4`
- `node dist/index.js --help` → prints `Usage:`
- `node dist/index.js unknowncmd` → prints `Unknown command`, exits 1
- `grep 'overdrive-intel@latest' static/setup.sh` → found
- `grep -c 'OVERDRIVE_API_KEY=' static/setup.sh` → 0
- `dist/cli/setup.js`, `dist/cli/commands.js`, `dist/shared/api-client.js` — all exist

## Deviations from Plan

### Out-of-scope discovery

**1. test.sh excluded by .gitignore**

- **Found during:** Task 2 commit
- **Issue:** `overdrive-intel-mcp/test.sh` is explicitly excluded by project `.gitignore` (line 44: `overdrive-intel-mcp/test.sh`) as an internal operational file. File was not previously tracked by git.
- **Fix:** Updated test.sh locally (22 tests pass). Committed index.ts and setup.sh without test.sh. Documented in commit message.
- **Impact:** Nil — test.sh runs and passes, just not tracked. All functionality verified.

## Self-Check

- [x] `dist/cli/setup.js` exists
- [x] `dist/cli/commands.js` exists
- [x] `dist/shared/api-client.js` exists
- [x] Commit 3d52e85 exists (Task 1)
- [x] Commit cb76e1c exists (Task 2)

## Self-Check: PASSED
