# Value-Add Feature Research

Assessment of features beyond basic "find new repos and tools."

## Feature Matrix

| #   | Feature                     | Value  | Complexity | Version                       |
| --- | --------------------------- | ------ | ---------- | ----------------------------- |
| 1   | Security scanning of skills | HIGH   | MEDIUM     | v1                            |
| 2   | Compatibility tracking      | HIGH   | HIGH       | v1 (declared), v2 (automated) |
| 3   | Breaking change alerts      | HIGH   | MEDIUM     | v1                            |
| 4   | Quality scoring (static)    | HIGH   | MEDIUM     | v1                            |
| 5   | Collaborative intelligence  | HIGH   | HIGH       | v2                            |
| 6   | Automated skill testing     | HIGH   | HIGH       | v2                            |
| 7   | Digest/briefing formats     | MEDIUM | LOW        | v1                            |
| 8   | Overdrive integration       | HIGH   | MEDIUM     | v1 (passive), v2 (active)     |
| 9   | Competitive monitoring      | MEDIUM | LOW-MEDIUM | v2                            |
| 10  | Community contribution      | MEDIUM | LOW        | v1 (GitHub-based)             |

## Detailed Analysis

### 1. Security Scanning (v1, HIGH value)

Skills are markdown prompts injected into a powerful agent. Attack vectors:

- Prompt injection: "ignore previous instructions", hidden Unicode, base64 payloads
- Credential exfiltration: reading SSH keys, env files, credential stores via hooks
- Code threats: execSync with unsanitized input, dynamic code execution, curl-pipe-bash patterns
- Supply chain: external URL references that change post-review

Implementation: Rule-based scanner (regex + AST for JS), curated threat signatures. Think semgrep rules for Claude Code skills.

### 2. Compatibility Tracking (v1 declared, v2 automated)

What breaks: model behavior changes, Claude Code CLI changes, MCP spec changes.

v1: Track which CC version and model each skill was last confirmed working with. Flag stale entries.
v2: Automated testing matrix across models and CC versions (requires sandbox infra).

### 3. Breaking Change Alerts (v1, THE killer feature)

Don't forward changelogs. Parse, classify (breaking/non-breaking), map impact to user's installed skills.

Example: "Claude Code v1.0.32 drops hook_context.session_id. 2 of your skills affected."

Sources: Anthropic changelog, Claude Code GitHub releases, MCP spec, community forums.

### 4. Quality Scoring (v1, static signals)

Automatable signals:

- Has SKILL.md with clear triggers? Has tests? Has README?
- Safe patterns (no dangerous shell execution, no hardcoded paths)?
- Git history: commit frequency, recency, contributor count
- Issue/PR response time

Display as transparent components: [Security: A] [Maintenance: B+] [Compatibility: A]

### 5. Collaborative Intelligence (v2, the moat)

Anonymous, opt-in telemetry:

- Skill co-occurrence: "users who install X also install Y"
- Workflow archetypes: "73% of Next.js users also use frontend-design skill"
- Failure pattern sharing: early warning from aggregated error signatures
- Configuration recommendations

Privacy: opt-in only, differential privacy, local-first computation, transparent data model.

### 6. Automated Skill Testing (v2)

Docker containers: install skill, run smoke tests, verify against current CC versions.
Powers compatibility matrix and "verified working" badges.
Cost concern: LLM API calls per test. Prioritize: new releases > CC releases > rotating schedule.

### 7. Digest/Briefing Formats (v1)

| Urgency                              | Format                 | Latency |
| ------------------------------------ | ---------------------- | ------- |
| Critical (security, breaking change) | Push (Slack DM, email) | Minutes |
| Important (new CC release)           | Daily digest           | Hours   |
| Interesting (new skill, trend)       | Weekly briefing        | Days    |
| Background (quality score changes)   | Monthly report         | Weeks   |

CLI-native format is primary. Also support: JSON, RSS/Atom, Slack webhook.

### 8. Overdrive Integration

v1 (passive): overdrive-intel check as pre-session hook, findings in ~/.claude/intel/latest-briefing.md
v2 (active): Auto-suggest skills for new projects, CLAUDE.md amendment proposals, upgrade orchestration
v3 (deep): Skill composition conflict detection, performance budgeting, CLAUDE.md lint

### 9. Competitive Monitoring (v2)

Track: Cursor, Windsurf, Copilot, Codex, Aider, Continue.dev, Cline/Roo Code.
Structured output: what it does, Overdrive equivalent, gap assessment, relevance.

### 10. Community Contribution (v1 via GitHub)

- Flag false positives, confirm/deny compatibility, upvote items
- Submit skills for review, report issues
- GitHub repo with issue templates gets 80% of the way for v1

## Highest-Impact Insight

The flywheel: security scanning (fear-driven adoption) -> automated testing (verified trust) -> collaborative intelligence (network effects). The feature to nail in v1 is breaking change alerts with impact analysis -- produces immediate "this saved me 2 hours" moment.
