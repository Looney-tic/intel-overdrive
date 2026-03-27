---
phase: 35-unified-cli-skills-sh-distribution
plan: "03"
subsystem: distribution
tags: [skills-sh, agent-skills, mcp, skill-md, agent-neutral]

requires: []
provides:
  - agent-skills/ directory with agent-neutral SKILL.md files for skills.sh distribution
  - Unified overdrive-intel SKILL.md (1 file covers all 8 type routes)
  - Four focused skills: intel-search, intel-breaking, intel-feed, intel-brief
  - README.md with install instructions for Looney-tic/agent-skills repo
  - Updated internal .claude/skills/overdrive-intel/SKILL.md with failure guidance
affects:
  - Looney-tic/agent-skills GitHub repo (content ready for push)
  - skills.sh submission
  - Agent onboarding experience for Claude Code, Cursor

tech-stack:
  added: []
  patterns:
    - "SETUP REQUIRED block in every agent-skills SKILL.md closes C-1 distribution gap"
    - "On Failure section prevents agent retry loops when MCP server unavailable"
    - "Agent-neutral SKILL.md: no ToolSearch, no deferred-tool, no Claude Code-internal APIs"

key-files:
  created:
    - agent-skills/overdrive-intel/SKILL.md
    - agent-skills/skills/intel-search/SKILL.md
    - agent-skills/skills/intel-breaking/SKILL.md
    - agent-skills/skills/intel-feed/SKILL.md
    - agent-skills/skills/intel-brief/SKILL.md
    - agent-skills/README.md
  modified:
    - .claude/skills/overdrive-intel/SKILL.md

key-decisions:
  - "Unified SKILL.md covers all 8 type routes; specialized skills focus on one type each for minimal installs"
  - "SETUP REQUIRED block in every SKILL.md directs users to npm install -g overdrive-intel && overdrive-intel setup"
  - "Data Freshness polling intervals removed from internal SKILL.md (backend detail per L-3); replaced with one-line freshness statement"
  - "Agent-neutral means: no ToolSearch, no session-start hooks, no Claude Code-internal API references"

patterns-established:
  - "skills.sh SKILL.md pattern: frontmatter + SETUP REQUIRED + When to Use + tool docs + examples + On Failure"

requirements-completed:
  - DIST-SKILLS-01
  - DIST-SKILLS-02

duration: 18min
completed: 2026-03-27
---

# Phase 35 Plan 03: Agent-Neutral SKILL.md Files for Skills.sh Distribution

**Agent-neutral overdrive-intel SKILL.md suite (1 unified + 4 specialized) with SETUP REQUIRED blocks, ready for Looney-tic/agent-skills GitHub push and skills.sh submission**

## Performance

- **Duration:** 18 min
- **Started:** 2026-03-27T12:11:51Z
- **Completed:** 2026-03-27T12:30:13Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Created `agent-skills/` directory tree with 5 SKILL.md files (1 unified + 4 focused) and README.md for `Looney-tic/agent-skills` GitHub repo
- All SKILL.md files are agent-neutral — zero ToolSearch, deferred-tool, or Claude Code-internal API references
- SETUP REQUIRED block in every file closes C-1 (MCP server not installed gap) by directing users to `npm install -g overdrive-intel && overdrive-intel setup`
- On Failure section in every file prevents agent retry loops (addresses M-2)
- Updated internal `.claude/skills/overdrive-intel/SKILL.md` with improved description (leads with value vs training data), concise freshness statement, and On Failure section

## Task Commits

Each task was committed atomically:

1. **Task 1: Create agent-neutral SKILL.md files for skills.sh distribution** - `b1506d1` (feat)
2. **Task 2: Update internal SKILL.md and verify agent-neutrality** - `7184785` (feat)

**Plan metadata:** (docs commit below)

## Files Created/Modified

- `agent-skills/overdrive-intel/SKILL.md` - Unified meta-skill: all 8 type routes, SETUP REQUIRED, On Failure, Cursor config, Supported Agents
- `agent-skills/skills/intel-search/SKILL.md` - Focused search skill with search/similar type routes
- `agent-skills/skills/intel-breaking/SKILL.md` - Focused breaking changes skill with context_stack examples
- `agent-skills/skills/intel-feed/SKILL.md` - Focused feed/updates skill with feed_tag filter examples
- `agent-skills/skills/intel-brief/SKILL.md` - Focused briefing skill with project-context scan instructions
- `agent-skills/README.md` - skills.sh install instructions, prerequisites, available skills table
- `.claude/skills/overdrive-intel/SKILL.md` - Updated description, simplified freshness, added On Failure

## Decisions Made

- Used `npm install -g overdrive-intel && overdrive-intel setup` as the canonical install command in all SETUP REQUIRED blocks (matches DIST-CLI-01/02 target)
- Removed polling-interval details from Data Freshness (backend implementation detail per L-3 review finding); replaced with one-line freshness statement
- Kept Claude Code-specific trigger language in the internal SKILL.md (it's the internal version); only the agent-skills versions are agent-neutral

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- `agent-skills/` content is ready for `git push` to `Looney-tic/agent-skills` repo
- Content is ready for skills.sh submission
- Phase 35-01 (CLI setup) and 35-02 (package rename) are the other deliverables in this phase

---
*Phase: 35-unified-cli-skills-sh-distribution*
*Completed: 2026-03-27*
