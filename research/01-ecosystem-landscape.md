# Ecosystem Landscape Research

Research into what already exists for monitoring the AI coding agent ecosystem.

## Existing Tools & Services

### Skill/Tool Registries

- **SkillsMP** (skillsmp.com) — 351K+ skills, community-run, crawls GitHub for SKILL.md files, AI-powered semantic search
- **skills.sh** (Vercel) — 83K+ skills, 8M+ installs, supports 18 agents, tracks actual install counts
- **Official MCP Registry** (registry.modelcontextprotocol.io) — REST API at `/v0/servers`, cursor-based pagination, search, filtering. API frozen at v0.1
- **PulseMCP** (pulsemcp.com) — 10,400+ MCP servers, updated daily, maintained by MCP Steering Committee member
- **mcpservers.org**, **mcp.so**, **mcpmarket.com**, **Glama** — community directories with varying coverage

### Release/Changelog Tracking

- **Releasebot** (releasebot.io) — tracks release notes from hundreds of vendors. Already tracks Anthropic, Claude Code, OpenAI, GitHub. Has API + webhooks. Closest existing tool to "intelligence feed for releases"
- **Claude Code changelog** — GitHub releases at `/releases.atom` (Atom feed available)

### GitHub Monitoring

- **GitHub Search API** — 30 req/min (authenticated), 1000 results/query max. Qualifiers: `created:>date`, `topic:`, `language:`, `stars:>N`, `sort:stars`
- **GH Archive + BigQuery** — all public GitHub events, updated hourly. Can run SQL to find repos matching criteria
- **Changelog Nightly** — free nightly email of hottest new/starred repos (open source)
- **github-trending-repos** — tracks GitHub trending page daily/weekly via native notifications
- **n8n workflow templates** — self-hosted automation for GitHub trending scraping

### Developer Intelligence Services

- **Console.dev** — weekly newsletter, manually reviews 2-3 dev tools. Editorial/manual pipeline
- **DevHunt** (devhunt.org) — open-source, PR-based submission, community voting. No automated discovery
- **daily.dev** — aggregates from 600+ sources via RSS, Feed Algorithm v3.0 with personalization. Article-focused, not tool-focused
- **Product Hunt** — GraphQL API available, can query by category. Not developer-tool-specific
- **LogRocket AI Dev Tool Power Rankings** — monthly comparison of 18 AI models and 11 dev tools across 50+ features. Manual editorial

### Landscape Maps (Point-in-time, not updating)

- **StackOne** — 120+ tools mapped across 11 categories
- **ToolShelf** — 204 tools analyzed with trends and pricing
- **AI Agents Directory** — interactive landscape map

## Gap Analysis

**Nothing exactly like Overdrive Intel exists.** The clear white space is a continuously-updating, filtered, personalized intelligence feed specifically for the AI coding agent ecosystem combining:

1. **Automated discovery** — GitHub Search API polling, MCP Registry API monitoring, skill registry tracking. Nobody does this comprehensively
2. **Multi-source aggregation** — existing tools each cover one source
3. **AI-powered filtering and relevance scoring** — daily.dev does this for articles, nobody does it for tools/repos
4. **Structured CLI output** — Releasebot has this model but only for releases

## Free API Sources Available

| Source                | API                     | Cost      | Rate Limit     |
| --------------------- | ----------------------- | --------- | -------------- |
| GitHub Search         | REST API                | Free      | 30 req/min     |
| GitHub Releases       | Atom feed               | Free      | None           |
| GH Archive            | BigQuery public dataset | Free tier | N/A            |
| Hacker News           | Algolia API             | Free      | None           |
| Official MCP Registry | REST v0                 | Free      | Unknown        |
| Claude Code changelog | GitHub releases Atom    | Free      | None           |
| Reddit                | Public RSS              | Free      | Polite polling |
| npm registry          | Public API              | Free      | None           |
| Releasebot            | Data API                | Free?     | Unknown        |
