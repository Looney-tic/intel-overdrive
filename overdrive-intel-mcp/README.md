# Intel Overdrive

[![npm version](https://img.shields.io/npm/v/intel-overdrive.svg)](https://www.npmjs.com/package/intel-overdrive)
[![Node version](https://img.shields.io/node/v/intel-overdrive.svg)](https://nodejs.org)
[![License](https://img.shields.io/badge/license-ELv2-blue.svg)](LICENSE)

> Live AI ecosystem intelligence for your coding agent — breaking changes, new tools, and security alerts from 1,100+ sources, before they hit training data.

![demo](https://inteloverdrive.com/dl/demo.gif)

## Quick start

Paste into Claude Code:

```
npx intel-overdrive setup
```

Registers your API key, installs the CLI, and adds the skill. Works immediately — no restart needed.

> [!TIP]
> You can also install via [skills.sh](https://skills.sh/Looney-tic/agent-skills): `npx skills add Looney-tic/agent-skills --skill intel-overdrive -g -y` — setup auto-triggers on first use.

## What it does

Your agent's training data is months old. Intel Overdrive monitors 1,100+ sources (22k+ GitHub repos, 280+ RSS feeds, npm, PyPI, Reddit, HN, arXiv) and makes the latest developments queryable in a single call.

**Your agent queries automatically** when you ask about AI tools, MCP servers, SDKs, or frameworks:

```
You: "What MCP servers exist for database access?"
      → intel-overdrive search "MCP database"
      → ranked results with star counts, quality labels

You: "Any breaking changes in the Anthropic SDK?"
      → intel-overdrive breaking
      → breaking changes from the last 7 days

You: "What's new this week?"
      → intel-overdrive feed
      → curated feed sorted by significance
```

Use the CLI directly from your terminal too:

```bash
intel-overdrive search "MCP servers for auth"
intel-overdrive feed --days 3
intel-overdrive breaking
```

## How it works

Intel Overdrive installs two things:

1. **Skill** — tells your agent when and how to query (`~/.claude/skills/intel-overdrive/`)
2. **CLI** — does the actual querying via Bash, fast and authenticated

No background process. No MCP server. No restart. The agent runs `intel-overdrive search "query"` via Bash whenever the skill triggers.

> [!NOTE]
> Want the structured MCP tool in your tool list? Run `intel-overdrive mcp-enable` — this is optional and requires a Claude Code restart.

## Coverage

| Domain               | What's tracked                                             |
| -------------------- | ---------------------------------------------------------- |
| AI coding assistants | Claude Code, Cursor, Copilot, Windsurf, Codex, Aider, Cody |
| LLM APIs and SDKs    | Anthropic, OpenAI, Gemini, Mistral, Cohere                 |
| Agent frameworks     | LangChain, CrewAI, AutoGen, Pydantic AI, smolagents        |
| MCP ecosystem        | Servers, protocol updates, best practices, security        |
| Breaking changes     | SDK deprecations, migration guides, security advisories    |
| Package registries   | npm, PyPI, VS Code Marketplace                             |

## CLI reference

| Command                               | Description                                       |
| ------------------------------------- | ------------------------------------------------- |
| `intel-overdrive setup`               | Register API key, install CLI globally, add skill |
| `intel-overdrive search "query"`      | Search for tools, docs, best practices            |
| `intel-overdrive feed [--days N]`     | Recent updates sorted by significance             |
| `intel-overdrive breaking [--days N]` | Breaking changes and deprecations                 |
| `intel-overdrive mcp-enable`          | Optional: register as MCP server in Claude Code   |
| `intel-overdrive --version`           | Show version                                      |

## Links

- [Website](https://inteloverdrive.com) — live demo and install
- [Skills on skills.sh](https://skills.sh/Looney-tic/agent-skills) — install via skills ecosystem
- [GitHub](https://github.com/Looney-tic/intel-overdrive) — source code
- [API docs](https://inteloverdrive.com/v1/guide) — REST API reference
