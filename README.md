# Overdrive Intel

**Stop manually tracking the AI ecosystem. Let your agent do it.**

New MCP servers, SDK breaking changes, security vulnerabilities, framework releases — the AI coding ecosystem moves so fast that staying current is a full-time job. Newsletters pile up, Reddit threads scroll by, release notes go unread.

Overdrive Intel is an [MCP server](https://modelcontextprotocol.io) that monitors 1,000+ sources for you and feeds the knowledge directly into your AI coding agent. When you ask a question, your agent already knows what shipped last week.

```
You: "Is there a good PostgreSQL MCP server?"

Agent calls overdrive_intel → gets live, quality-ranked results:

  1. timescale/pg-aiguide      ★ 1,640 · established  — EXPLAIN analysis, index tuning
  2. supabase-community/mcp    ★ 814   · emerging     — full Supabase DB + auth access
  3. postgres_mcp              ★ 6     · new           — lightweight, readonly modes
```

No newsletters. No manual research. Your agent surfaces the right answer with star counts, quality labels, and significance tiers — in the moment you need it.

## Install

```bash
bash <(curl -s https://inteloverdrive.com/dl/setup.sh)
```

One command. Registers anonymously, installs the MCP server, configures your tool. No email, no account, no configuration files.

```bash
# or via npm
npm i -g intel-overdrive-mcp
```

Works with **Claude Code** · **Cursor** · **GitHub Copilot** · **Windsurf** · **Claude Desktop** · **Aider** · **Cody** · **Continue** · any MCP client

## What it catches

| Your agent asks about...                                  | Overdrive Intel returns                         |
| --------------------------------------------------------- | ----------------------------------------------- |
| "Any breaking changes in the OpenAI SDK?"                 | Specific version, what broke, migration steps   |
| "Best MCP server for Postgres?"                           | Quality-ranked options with star counts         |
| "Is Context7 safe to use?"                                | CVE details, disclosure timeline, patch status  |
| "What's the current best practice for Claude Code hooks?" | Synthesized patterns from 50+ community sources |
| "Alternatives to LangChain?"                              | Semantic search across the full corpus          |

## How it works

1. **You install once** — setup script registers the MCP server globally
2. **Your agent calls it automatically** — when you ask about tools, SDKs, or breaking changes, the agent recognizes the topic and queries `overdrive_intel`
3. **Results are ranked** — every item is auto-classified and quality-scored with GitHub stars, maintenance status, and maturity labels

## Coverage

**1,000+ sources** polled every 15 minutes. **49,000+ items** classified and searchable.

| Source type        | Count | What it covers                                             |
| ------------------ | ----- | ---------------------------------------------------------- |
| GitHub repos       | 570+  | Release feeds, deep repo analysis, trending projects       |
| RSS / Atom feeds   | 280+  | Anthropic, OpenAI, Vercel, Cloudflare, framework blogs     |
| Vendor MCP servers | 30+   | Netlify, Stripe, Supabase, AWS, Sentry, Grafana, Terraform |
| Reddit             | 10+   | r/ClaudeAI, r/cursor, r/LocalLLaMA, r/MachineLearning      |
| Hacker News        | 5     | AI, MCP, agent-related discussions                         |
| Bluesky            | 6     | MCP protocol, AI coding community                          |
| Package registries | 3     | npm, PyPI — new MCP servers, SDK releases                  |
| Other              | 20+   | arXiv, VS Code Marketplace, MCP registries, awesome lists  |

Every item is auto-classified into types (tool, update, practice, security, docs) and significance levels (breaking, major, minor, informational).

## API

Also available as a REST API with 44 endpoints for scripts, CI/CD, and custom integrations.

[API documentation →](https://inteloverdrive.com/v1/guide)

## Self-host

```bash
git clone https://github.com/Looney-tic/intel-overdrive.git
cd intel-overdrive
docker compose up -d        # Postgres (pgvector) + Redis
cp .env.example .env        # Add your API keys
alembic upgrade head        # Run migrations
python -m src.mcp_server    # Start MCP server
```

Requires Python 3.12+, PostgreSQL with pgvector, Redis, Voyage AI key, Anthropic key.

## License

[Elastic License 2.0](LICENSE) — free to use, modify, and self-host. Cannot be offered as a competing hosted service.
