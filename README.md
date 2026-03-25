# Overdrive Intel

Real-time AI coding ecosystem intelligence. An MCP server that monitors 900+ sources and gives your AI coding agent fresh data about breaking changes, new tools, security vulnerabilities, and best practices.

## Install

```bash
bash <(curl -s https://inteloverdrive.com/dl/setup.sh)
```

Or via npm:

```bash
npm i -g intel-overdrive-mcp
```

Works with Claude Code, Cursor, Copilot, Windsurf, and any MCP-compatible client.

## What it does

Your AI agent's training data is months old. This tool gives it live intelligence:

- **Breaking changes** — SDK updates, API removals, protocol changes before you ship broken code
- **Tool discovery** — Find MCP servers, frameworks, and extensions with star counts and quality labels
- **Security alerts** — CVEs, supply-chain attacks, MCP vulnerabilities within hours of disclosure
- **Best practices** — Synthesized patterns from 50+ topics, updated from community signals
- **Quality signals** — GitHub stars, maintenance status, maturity labels to distinguish proven tools from experiments

## How it works

1. Run the setup command — registers an API key, installs the MCP server, configures your tool
2. Your agent automatically calls `overdrive_intel` when you ask about AI tools, SDK changes, or best practices
3. Results are classified, quality-ranked, and include star counts so the agent can make informed recommendations

## Coverage

909 sources across: GitHub releases, RSS/Atom feeds, Reddit, Hacker News, Bluesky, npm, PyPI, arXiv, VS Code Marketplace, MCP registries, and more.

Items are auto-classified into 5 types (tool, update, practice, skill, docs) and 4 significance levels (breaking, major, minor, informational).

## API

Also available as a REST API with 44 endpoints. See the [API guide](https://inteloverdrive.com/v1/guide).

## License

[Elastic License 2.0](LICENSE) — free to use, modify, and self-host. Cannot be offered as a competing hosted service.
