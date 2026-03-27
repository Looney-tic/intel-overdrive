# Intel Overdrive

**Your agent's training data is months old.** Intel Overdrive gives it live intelligence — breaking changes, new tools, security alerts, and best practices from 1,100+ monitored sources.

> Where Context7 gives you docs, Intel Overdrive tells you what changed since your agent was trained.

## Quick Start

Paste into Claude Code:

```
npx intel-overdrive setup
```

That's it. Registers your API key, installs the CLI, and installs the skill. Works immediately — no restart needed.

### Or install the skill first

```
npx skills add Looney-tic/agent-skills --skill intel-overdrive -g -y
```

The skill auto-triggers setup on first use.

## What happens next

Your agent automatically queries Intel Overdrive when you ask about AI tools, MCP servers, SDKs, or frameworks:

```
You: "What MCP servers exist for database access?"
Agent: [calls intel-overdrive search "MCP database"] → ranked results with star counts

You: "Any breaking changes in the Anthropic SDK?"
Agent: [calls intel-overdrive breaking] → breaking changes from the last 7 days

You: "What's new this week?"
Agent: [calls intel-overdrive feed] → curated feed of recent updates
```

You can also use the CLI directly from your terminal:

```bash
intel-overdrive search "MCP servers for auth"
intel-overdrive feed --days 3
intel-overdrive breaking
```

## How it works

1. **Skill** tells your agent when to query (installed to `~/.claude/skills/`)
2. **CLI** does the querying via Bash — fast, authenticated, no MCP server needed
3. **1,100+ sources** monitored: 22k+ GitHub repos, 280+ RSS feeds, npm, PyPI, Reddit, HN, arXiv

The agent uses `intel-overdrive search "query"` via Bash. No MCP server process, no restart, no configuration.

Want the MCP tool in your tool list? Optional:

```bash
intel-overdrive mcp-enable
```

## What it covers

- **AI Coding Assistants** — Claude Code, Cursor, Copilot, Windsurf, Codex, Aider
- **LLM APIs & SDKs** — Anthropic, OpenAI, Gemini, Mistral, Cohere
- **Agent Frameworks** — LangChain, CrewAI, AutoGen, Pydantic AI, smolagents
- **MCP Ecosystem** — servers, protocol updates, best practices, security
- **Breaking Changes** — SDK deprecations, migration guides, security advisories
- **Package Registries** — npm, PyPI, VS Code Marketplace

## Links

- [Website](https://inteloverdrive.com)
- [GitHub](https://github.com/Looney-tic/intel-overdrive)
- [Skills on skills.sh](https://skills.sh/Looney-tic/agent-skills)
- [API docs](https://inteloverdrive.com/v1/guide)

## License

[Elastic License 2.0](https://github.com/Looney-tic/intel-overdrive/blob/main/LICENSE)
