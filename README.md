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

> [!TIP]
> Also available via [skills.sh](https://skills.sh/Looney-tic/agent-skills): `npx skills add Looney-tic/agent-skills --skill intel-overdrive -g -y`

## What you can ask

| Question                                | What your agent finds                               |
| --------------------------------------- | --------------------------------------------------- |
| "What MCP servers exist for databases?" | Quality-ranked list with stars and maturity labels  |
| "Did anything break in the OpenAI SDK?" | Specific version, what broke, how to migrate        |
| "What's new this week?"                 | Curated feed of the most significant updates        |
| "Best practice for Claude Code hooks?"  | Synthesized patterns from community sources         |
| "Compare LangChain vs CrewAI"           | Side-by-side with community traction and trade-offs |

Your agent calls it **automatically** — when you ask about AI tools, SDKs, or frameworks, the skill triggers and runs `intel-overdrive search` via Bash. You can also use the CLI directly:

```bash
intel-overdrive search "MCP servers for auth"
intel-overdrive feed --days 7
intel-overdrive breaking
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
