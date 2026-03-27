# Overdrive Intel — Agent Skills

AI coding ecosystem intelligence for any MCP-enabled agent. Know about breaking SDK changes, new tools, and security alerts before they cause wrong code generation.

## What It Provides

Real-time intelligence from 1100+ monitored sources covering:

- **Breaking changes** in LLM SDKs, MCP servers, and agent frameworks — before they cause wrong code
- **New tools and MCP servers** across the AI coding ecosystem — quality-ranked with adoption signals
- **Security alerts** and action items requiring immediate attention
- **Best practices** and evolving patterns for AI-assisted development
- **Feed updates** from Claude Code, Cursor, OpenAI, Anthropic, LangChain, and more

This fills the gap between Context7 (static docs) and your agent's stale training data. One MCP call replaces 10-30 seconds of unreliable web search.

## Install

### All Skills

```bash
npx skills add Looney-tic/agent-skills
```

### Individual Skills

```bash
# Unified skill (recommended — covers all use cases)
npx skills add Looney-tic/agent-skills/overdrive-intel

# Focused skills (pick what you need)
npx skills add Looney-tic/agent-skills/skills/intel-search
npx skills add Looney-tic/agent-skills/skills/intel-breaking
npx skills add Looney-tic/agent-skills/skills/intel-feed
npx skills add Looney-tic/agent-skills/skills/intel-brief
```

## Prerequisites

The MCP server must be installed and configured before agents can use these skills:

```bash
npm install -g overdrive-intel && overdrive-intel setup
```

The `setup` command provisions an API key, registers the MCP server with your agent, and installs the skill.

## Available Skills

| Skill             | Description                                   |
| ----------------- | --------------------------------------------- |
| `overdrive-intel` | Unified skill — all query types in one file   |
| `intel-search`    | Find tools, docs, best practices, comparisons |
| `intel-breaking`  | Breaking changes and deprecation alerts       |
| `intel-feed`      | Recent updates, releases, changelogs          |
| `intel-brief`     | Token-budgeted topic briefings                |

## Supported Agents

- **Claude Code** — native MCP support, zero configuration after `overdrive-intel setup`
- **Cursor** — add the server to `.cursor/mcp.json` (see SKILL.md for config)
- Any MCP-compatible agent supporting stdio MCP servers

## Links

- **Main project**: [github.com/Looney-tic/overdrive-intel](https://github.com/Looney-tic/overdrive-intel)
- **npm package**: [npmjs.com/package/overdrive-intel](https://www.npmjs.com/package/overdrive-intel)
- **skills.sh**: [skills.sh](https://skills.sh)
