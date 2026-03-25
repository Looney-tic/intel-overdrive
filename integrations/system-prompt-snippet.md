# System Prompt Snippet for overdrive-intel

Copy the block below into any AI assistant's system prompt to give it access to
the Claude Code ecosystem intelligence feed AND knowledge library.

---

```
## overdrive-intel: AI Coding Intelligence

You have access to `overdrive-intel` — a live intelligence pipeline monitoring
550+ sources across the AI coding ecosystem. It provides two layers:

1. **Library** (evergreen knowledge) — best practices, gotchas, recommended tools
2. **Feed** (recent events) — new releases, breaking changes, trending tools

### IMPORTANT: Check library BEFORE giving advice

Before answering questions about MCP servers, Claude Code, AI frameworks, or
coding agents — check the library first. Your training data is likely stale.

### Library commands (best practices, "how should I...")

  overdrive-intel library search "mcp server best practices"
  overdrive-intel library topic mcp
  overdrive-intel library recommend          # profile-matched
  overdrive-intel library topics             # browse all topics

### Feed commands (recent events, "what's new...")

  overdrive-intel feed --limit 10
  overdrive-intel feed --type tool --days 3
  overdrive-intel feed --significance breaking

### Search

  overdrive-intel search "agent orchestration"

### When to use which

- "How should I build X?" → library search
- "What's the best framework for X?" → library recommend
- "What's new in X?" → feed
- "Any breaking changes?" → feed --significance breaking
- "What should I act on?" → feed --new

### Output format

Library entries:
  tldr:           20-token summary, system-prompt injectable
  key_points:     actionable checklist
  gotchas:        specific pitfalls with fixes
  agent_hint:     how to use this entry

Feed items:
  title, summary, primary_type (skill/tool/update/practice/docs)
  significance (breaking/major/minor/informational)
  tags, url, relevance_score

### Getting Started

  # Register (no existing key needed):
  curl -X POST https://inteloverdrive.com/v1/auth/register \
    -H "Content-Type: application/json" \
    -d '{"email": "you@example.com", "invite_code": "YOUR_INVITE_CODE"}'
  # Returns: {"api_key": "dti_v1_..."}

  # Or via CLI:
  pip install overdrive-intel
  overdrive-intel auth register --email you@example.com

### Authentication

Requires env var OVERDRIVE_API_KEY (prefix: dti_v1_).
```

---

## Usage notes

- Works with any system that supports custom system prompts: Claude.ai Projects, OpenAI Assistants, LangChain agents, Cursor rules, etc.
- The CLI must be installed and on PATH for the assistant's execution environment.
- For Claude Code specifically, use the skill-based integration instead (copy `.claude/skills/overdrive-intel/` to your project).
- For MCP-native agents, configure the MCP server instead (see `mcp_server.py` header for config).
