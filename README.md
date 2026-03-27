# Intel Overdrive

[![npm version](https://img.shields.io/npm/v/intel-overdrive.svg)](https://www.npmjs.com/package/intel-overdrive)
[![Node version](https://img.shields.io/node/v/intel-overdrive.svg)](https://nodejs.org)
[![License](https://img.shields.io/badge/license-ELv2-blue.svg)](LICENSE)

Live AI ecosystem intelligence for your coding agent. Breaking changes, new tools, and security alerts from 1,100+ sources — before they hit training data.

## Get started

Paste into Claude Code:

```
npx intel-overdrive setup
```

No email, no account, no restart. Works immediately.

Then ask your agent anything about the AI ecosystem:

```
You: "What did I miss this week in AI coding?"

Agent runs intel-overdrive feed --days 7:

  Claude Code now supports scheduled tasks and multi-agent code review
  Gemini 2.5 Flash — new "thinking budget" for fine-grained reasoning control
  MCP slash-commands workflow migration — update branch protection rules
  OpenClaw 2026.3.24 — breaking changes to Gateway/OpenAI compatibility
  How to split work between Claude Code and Codex in real projects
```

No more scrolling Twitter, Reddit, and newsletters. Your agent already knows.

> [!TIP]
> Also available via [skills.sh](https://skills.sh/Looney-tic/agent-skills): `npx skills add Looney-tic/agent-skills --skill intel-overdrive -g -y`

## What you can ask

| Instead of...                        | Ask your agent                                  |
| ------------------------------------ | ----------------------------------------------- |
| Scrolling Twitter for AI news        | "What's new in AI coding this week?"            |
| Checking changelogs before upgrading | "Any breaking changes I should know about?"     |
| Googling "best tool for X"           | "What's the best MCP server for databases?"     |
| Reading newsletters you're behind on | "Catch me up on Claude Code — what changed?"    |
| Wondering if your SDK is outdated    | "Is the Anthropic SDK I'm using still current?" |

Your agent calls it **automatically** when you ask about AI tools, SDKs, or frameworks. Or use the CLI directly:

```bash
intel-overdrive feed --days 7           # what's new this week
intel-overdrive breaking                # breaking changes to watch for
intel-overdrive search "Claude Code hooks best practices"
```

## How it works

1. **Skill** tells your agent when to query (installed to `~/.claude/skills/`)
2. **CLI** does the querying via Bash — fast, authenticated, no background process
3. **1,100+ sources** monitored: 22k+ GitHub repos, 280+ RSS feeds, npm, PyPI, Reddit, HN, arXiv

No MCP server required. No restart. The agent runs `intel-overdrive search "query"` whenever the skill triggers.

> [!NOTE]
> Want the structured MCP tool in your tool list? Run `intel-overdrive mcp-enable` — optional, requires a Claude Code restart.

## Why not web search?

|                 | Agent web search                                 | Intel Overdrive                            |
| --------------- | ------------------------------------------------ | ------------------------------------------ |
| **Speed**       | 10-30s of Googling and scraping                  | One call, instant                          |
| **Cost**        | Multiple tool calls, burns tokens                | Single call, pre-compressed                |
| **Reliability** | Scrapes fail, results outdated                   | Pre-indexed, quality-scored                |
| **Quality**     | Can't rank a 30k-star SDK from a weekend project | Stars, maturity labels, significance tiers |

## Coverage

**1,100+ sources** polled every 15 minutes. **49,000+ items** classified and searchable.

Sources include GitHub repos (22k+), RSS/Atom feeds (280+), vendor MCP servers (30+), Reddit, Hacker News, Bluesky, npm, PyPI, arXiv, VS Code Marketplace, and MCP registries. Every item is auto-classified by type and significance level.

## CLI reference

| Command                               | Description                              |
| ------------------------------------- | ---------------------------------------- |
| `intel-overdrive setup`               | Register API key, install CLI, add skill |
| `intel-overdrive search "query"`      | Search for tools, docs, best practices   |
| `intel-overdrive feed [--days N]`     | Recent updates sorted by significance    |
| `intel-overdrive breaking [--days N]` | Breaking changes and deprecations        |
| `intel-overdrive mcp-enable`          | Optional: register as MCP server         |
| `intel-overdrive --version`           | Show version                             |

## Self-host

```bash
git clone https://github.com/Looney-tic/intel-overdrive.git && cd intel-overdrive
docker compose up -d && cp .env.example .env && alembic upgrade head
```

Requires Python 3.12+, PostgreSQL with pgvector, Redis, Voyage AI key, Anthropic key. [API docs →](https://inteloverdrive.com/v1/guide)

## Why I built this

I got frustrated. Every morning I was scrolling Twitter, checking Reddit, skimming newsletters — just to stay current on what's new in the AI coding ecosystem. It felt like a part-time job.

Then I realized: when I asked Claude Code about recent developments, it just started Googling for me. Scraping random pages, burning tokens, returning outdated results. My AI coding agent couldn't tell me what shipped last week.

So I built Intel Overdrive. 1,100+ sources, AI-classified, quality-scored, queryable in a single call. Now when I ask "what's the best MCP server for databases?" — my agent already knows.

— [Tijmen](mailto:tijmen.r.devries@gmail.com)

## License

[Elastic License 2.0](LICENSE) — free to use, modify, and self-host.
