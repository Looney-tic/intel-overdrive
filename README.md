# Overdrive Intel

**Everything new in AI coding — delivered straight to your agent.**

New MCP servers, Claude Code skills, agent frameworks, SDK updates — the ecosystem moves faster than anyone can track. Most developers piece it together from Twitter, Reddit, newsletters, and Discord. By the time you hear about a tool, you've already built the thing it replaces.

Overdrive Intel plugs directly into Claude Code as an MCP server. It continuously monitors 1,000+ sources and indexes what's new. Your agent automatically knows about tools, features, and changes that aren't in its training data — and surfaces them exactly when they're relevant.

**You're building a feature. Your agent finds the tool you didn't know existed:**

```
You: "I need to add Stripe payments to this app"

Without Overdrive Intel → agent writes raw API integration from scratch

With Overdrive Intel → agent calls overdrive_intel first:
  ⚡ Stripe MCP Server (stripe/ai) — ★ 3.2k · established
    "Official Stripe MCP. Create payment intents, manage subscriptions,
     and query transactions directly from Claude Code."
  → Agent uses the MCP server instead of writing 200 lines of boilerplate
```

**You haven't checked the news in two weeks. One question catches you up:**

```
You: "What did I miss? Anything important in AI coding lately?"

Agent calls overdrive_intel({ type: "feed", days: 14 }):

  🔴 BREAKING  Anthropic SDK v0.86 — streaming API changed, update client code
  🔴 BREAKING  OpenAI dropped prompt_cache_key — remove before upgrading
  🟡 MAJOR     Claude Agent SDK v0.2 — multi-agent orchestration + handoffs
  🟡 MAJOR     Netlify, Stripe, Grafana ship official MCP servers
  🔵 NEW       29 vendor MCP servers added to ecosystem (AWS, Sentry, Terraform...)
  🔵 NEW       ContextCrush CVE in Context7 — patch or disable immediately
```

No newsletters. No Twitter. No "I wish I'd known about that last week."

## Install

Paste this into your Claude Code conversation:

```bash
bash <(curl -s https://inteloverdrive.com/dl/setup.sh)
```

That's it. Claude runs the command, registers anonymously, installs the MCP server, and configures itself. No email, no account, no configuration.

```bash
# or via npm
npm i -g intel-overdrive-mcp
```

Built for **Claude Code**. Also available as a [REST API](https://inteloverdrive.com/v1/guide).

```mermaid
flowchart LR
    S["🔄 1,000+ sources\npolled every 15 min"] --> C["🧠 AI classification\ntype · significance · tags\nHaiku LLM + Voyage embeddings"] --> Q["⭐ Quality scoring\nGitHub stars · maintenance\nmaturity labels"] --> D[("📦 49k+ items\nPostgreSQL + pgvector")] --> M["⚡ overdrive_intel\nMCP tool"] --> A["💻 Claude Code"]
```

## What you can ask

| Question                                          | What your agent finds                             |
| ------------------------------------------------- | ------------------------------------------------- |
| "What MCP servers exist for databases?"           | Quality-ranked list with stars, maturity labels   |
| "Any new agent frameworks worth trying?"          | Latest frameworks, compared by community traction |
| "Did anything break in the OpenAI SDK?"           | Specific version, what broke, how to migrate      |
| "What's the best practice for Claude Code hooks?" | Synthesized patterns from community sources       |
| "Are there security issues with Context7?"        | CVE details, patch status, disclosure timeline    |
| "What's new this week?"                           | Curated feed of the most significant updates      |

Your agent also calls it **automatically** — when you ask it to write code using a library, it checks for breaking changes before writing outdated patterns.

## Why not just let the agent search the web?

|                 | Agent web search                                              | Overdrive Intel                                 |
| --------------- | ------------------------------------------------------------- | ----------------------------------------------- |
| **Speed**       | 10-30s of Googling, scraping, parsing                         | One call, instant                               |
| **Cost**        | Multiple tool calls, burns tokens reading pages               | Single call, pre-compressed                     |
| **Reliability** | Scrapes may fail, results outdated or wrong                   | Pre-indexed, verified, quality-scored           |
| **Quality**     | No ranking — can't tell a 30k-star SDK from a weekend project | Star counts, quality labels, significance tiers |

## Coverage

**1,000+ sources** polled every 15 minutes. **49,000+ items** classified and searchable.

| Source type        | Count | What it covers                                                    |
| ------------------ | ----- | ----------------------------------------------------------------- |
| GitHub repos       | 22k+  | Thousands of repos via search API, 575 tracked with deep analysis |
| RSS / Atom feeds   | 280+  | Anthropic, OpenAI, Vercel, Cloudflare, framework blogs            |
| Vendor MCP servers | 30+   | Netlify, Stripe, Supabase, AWS, Sentry, Grafana, Terraform        |
| Reddit             | 10+   | r/ClaudeAI, r/cursor, r/LocalLLaMA, r/MachineLearning             |
| Hacker News        | 5     | AI, MCP, agent-related discussions                                |
| Bluesky            | 6     | MCP protocol, AI coding community                                 |
| Package registries | 3     | npm, PyPI — new MCP servers, SDK releases                         |
| Other              | 20+   | arXiv, VS Code Marketplace, MCP registries, awesome lists         |

Every item is auto-classified into types (tool, update, practice, security, docs) and significance levels (breaking, major, minor, informational).

## How it works

1. **Install once** — paste the setup command into Claude Code
2. **Agent detects automatically** — when you ask about tools, SDKs, or new features, Claude Code calls `overdrive_intel` before searching the web
3. **Or just ask** — "what's new?", "best MCP for X?", "any breaking changes in Y?"
4. **Results are ranked** — quality-scored with GitHub stars, maintenance status, and maturity labels

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

## Why I built this

I got frustrated. Every morning I was scrolling Twitter, checking Reddit, skimming newsletters — just to stay current on what's new in the AI coding ecosystem. New MCP servers, new Claude Code features, new agent frameworks, breaking SDK changes. It felt like a part-time job.

Then I realized something worse: when I asked Claude Code about recent developments or current best practices, it just started Googling for me. Scraping random pages, burning tokens, returning outdated results. My AI coding agent — the thing that's supposed to make me more productive — couldn't tell me what shipped last week.

So I built Overdrive Intel — with Claude Code. A pipeline that monitors 1,000+ sources, classifies everything with AI, scores it for quality, and feeds it directly into Claude Code as an MCP tool. Now when I ask "what's the best MCP server for databases?" or "did anything break in the Anthropic SDK?", my agent already knows. One call, instant answer, quality-ranked.

It changed how I work. I stopped manually tracking the ecosystem and started just building. Now I'm sharing it with everyone.

— Tijmen

## Contact

Questions, feedback, or ideas: [tijmen.r.devries@gmail.com](mailto:tijmen.r.devries@gmail.com)

## License

[Elastic License 2.0](LICENSE) — free to use, modify, and self-host. Cannot be offered as a competing hosted service.
