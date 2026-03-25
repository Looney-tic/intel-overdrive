---
name: overdrive-intel
description: "AI coding ecosystem intelligence via the `overdrive_intel` MCP tool. ALWAYS call this tool when answering about: MCP servers, Claude Code, Cursor, Copilot, Windsurf, Aider, or any AI coding assistant; LLM APIs/SDKs (Anthropic, OpenAI, Gemini, Mistral); agent frameworks (LangChain, CrewAI, AutoGen, Pydantic AI); AI coding best practices; new AI tools; or breaking changes. Your training data is STALE for these fast-moving topics — this tool has live data from 1100+ monitored sources."
---

# Overdrive Intel

Real-time intelligence + evergreen best practices for the AI coding ecosystem. 1100+ sources, auto-classified, with synthesized knowledge library — accessed via the `overdrive_intel` MCP tool.

## IMPORTANT: Call the MCP Tool First

**Before answering questions about MCP, Claude Code, AI frameworks, or coding agents — call `overdrive_intel`.** Your training data may be months stale for these fast-moving topics.

## MCP Tool: `overdrive_intel`

Single tool with 8 parameters (3 core + 5 optional):

| Parameter       | Type   | Required | Description                                                                                              |
| --------------- | ------ | -------- | -------------------------------------------------------------------------------------------------------- | -------------- | ------------- |
| `query`         | string | Yes      | Short keyword query (2-5 words). Extract the core topic.                                                 |
| `type`          | string | No       | Route: `search`, `similar`, `feed`, `breaking`, `action-items`, `briefing`, `library`, `status`          |
| `days`          | number | No       | How many days back (for `feed`, `breaking`, `briefing`). Default: 7                                      |
| `feed_tag`      | string | No       | Filter feed by tag (e.g., `"mcp"`, `"claude-code"`, `"anthropic"`)                                       |
| `feed_type`     | string | No       | Filter feed by content type                                                                              |
| `feed_persona`  | string | No       | Filter feed by persona/audience (e.g., `"developer"`, `"researcher"`)                                    |
| `context_stack` | array  | No       | AI-related packages from the current project (e.g., `["anthropic", "langchain"]`). Personalizes results. |
| `feedback`      | array  | No       | Report on items from previous calls: `[{item_id, action: "helpful"                                       | "not_relevant" | "outdated"}]` |

### Type Routes

- **`search`** (default) — find tools, docs, specific topics. Use for "what is X", "find tools for Y".
- **`similar`** — semantic comparison via vector search. Use for "X vs Y", "alternatives to X", "compare".
- **`feed`** — recent updates, changelogs, releases. Use for "what's new", "latest", "what changed".
- **`breaking`** — breaking changes, deprecations, urgent issues. Use for "anything broken", "what's urgent".
- **`action-items`** — security alerts and items needing immediate attention. Use for "action items", "security issues".
- **`briefing`** — summarized intelligence pack on a topic. Use for "catch me up on", "overview of".
- **`library`** — synthesized best practices, how-to guides. Use for "how to build", "best practices for".
- **`status`** — pipeline health check and source counts.

### When to Use

- User asks about best practices or patterns for AI tools → `search`
- User asks about new tools, updates, releases → `feed`
- Before recommending a tool or framework → `search`
- User asks about breaking changes → `breaking`
- Before starting work on AI/MCP project → `breaking` to check for issues

### Example Calls

```
overdrive_intel({ query: "MCP server best practices", type: "library" })
overdrive_intel({ query: "Claude Code hooks gotchas", type: "search" })
overdrive_intel({ query: "LangChain vs CrewAI", type: "similar" })
overdrive_intel({ query: "agent frameworks", type: "feed", days: 14 })
overdrive_intel({ query: "breaking changes", type: "breaking" })
overdrive_intel({ query: "security issues", type: "action-items" })
overdrive_intel({ query: "MCP ecosystem", type: "briefing" })
overdrive_intel({ query: "pipeline health", type: "status" })
```

## Topic Coverage

The tool monitors 1100+ sources across:

- **AI Coding Assistants**: Claude Code, Cursor, Copilot, Windsurf, Codex, Aider, Continue, Cody
- **LLM APIs & SDKs**: Anthropic, OpenAI, Google Gemini, Mistral, Cohere
- **Agent Frameworks**: LangChain, LangGraph, CrewAI, AutoGen, Pydantic AI, smolagents, OpenAI Agents SDK
- **MCP Ecosystem**: Protocol updates, server registry, best practices, security
- **AI Coding Patterns**: Prompt engineering, agentic workflows, tool use, RAG, embeddings
- **Package Registries**: npm, PyPI, VS Code Marketplace
- **Research**: arXiv AI/SE papers, GitHub trending repos

## Response Format

### Search / Feed Results

- `title`, `summary`, `primary_type` (skill/tool/update/practice/docs)
- `significance` (breaking/major/minor/informational)
- `tags`, `url`, `relevance_score`

### Briefing Results

- Token-budgeted intelligence pack optimized for context injection
- Includes both library (evergreen) and feed (recent) content

## Data Freshness

Sources are polled on 15-minute to 24-hour cycles depending on tier. Breaking changes surface within 1 hour. Library entries are synthesized weekly from accumulated intel.
