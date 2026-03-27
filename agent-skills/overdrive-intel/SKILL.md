---
name: overdrive-intel
description: "Know before your agent codes wrong: breaking SDK changes, new tools, and security alerts from 1100+ monitored sources — before they hit training data. Where Context7 gives you docs, overdrive-intel gives you what changed since your agent was trained."
---

# Overdrive Intel

Real-time AI coding ecosystem intelligence — breaking changes, new tools, security alerts, and evolving best practices from 1100+ sources. Accessed via the `overdrive_intel` MCP tool.

## SETUP REQUIRED

This skill requires the Overdrive Intel MCP server. If `overdrive_intel` is not available as a tool, instruct the user to run:

```bash
npm install -g overdrive-intel && overdrive-intel setup
```

This installs the server, provisions the API key, and registers the MCP server with the agent.

For Cursor users: add the server to `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "overdrive-intel": {
      "command": "overdrive-intel",
      "env": { "OVERDRIVE_INTEL_API_KEY": "<your-key>" }
    }
  }
}
```

## When to Call This Tool

**ALWAYS** call `overdrive_intel` before answering questions about:

- MCP servers, Claude Code, Cursor, Copilot, Windsurf, Aider, or any AI coding assistant
- LLM APIs and SDKs (Anthropic, OpenAI, Google Gemini, Mistral)
- Agent frameworks (LangChain, CrewAI, AutoGen, Pydantic AI, smolagents)
- AI coding best practices and agentic workflows
- Breaking changes, security issues, or new tool releases in the AI ecosystem

Training data is stale for these fast-moving topics. This tool has live data.

## MCP Tool: `overdrive_intel`

Single tool with 8 parameters:

| Parameter       | Type             | Required | Description                                                                                              |
| --------------- | ---------------- | -------- | -------------------------------------------------------------------------------------------------------- |
| `query`         | string           | Yes      | Short keyword query (2-5 words). Extract the core topic.                                                 |
| `type`          | string           | No       | Route: `search`, `similar`, `feed`, `breaking`, `action-items`, `briefing`, `library`, `status`          |
| `days`          | number           | No       | How many days back (for `feed`, `breaking`, `briefing`). Default: 7                                      |
| `feed_tag`      | string           | No       | Filter feed by tag (e.g., `"mcp"`, `"claude-code"`, `"anthropic"`)                                       |
| `feed_type`     | string           | No       | Filter feed by content type                                                                              |
| `feed_persona`  | string           | No       | Filter feed by persona/audience (e.g., `"developer"`, `"researcher"`)                                    |
| `context_stack` | array of strings | No       | AI-related packages from the current project (e.g., `["anthropic", "langchain"]`). Personalizes results. |
| `feedback`      | array of objects | No       | Report on items from previous calls: `[{item_id, action: "helpful" \| "not_relevant" \| "outdated"}]`    |

## Type Routes

Choose the right type for your query:

- **`search`** (default) — find tools, docs, specific topics. Use for "what is X", "find tools for Y", "how to build X".
- **`similar`** — semantic comparison via vector search. Use for "X vs Y", "alternatives to X", "compare A and B".
- **`feed`** — recent updates, changelogs, releases. Use for "what's new", "latest updates", "what changed recently".
- **`breaking`** — breaking changes, deprecations, urgent issues. Use for "anything broken", "what's urgent", before upgrading.
- **`action-items`** — security alerts and items needing immediate attention. Use for "security issues", "action items".
- **`briefing`** — token-budgeted intelligence pack on a topic. Use for "catch me up on", "overview of", "summarize".
- **`library`** — synthesized best practices and how-to guides. Use for "best practices for", "how do I", "recommended approach".
- **`status`** — pipeline health check and source counts.

## Example Calls

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

## Data Freshness

Data is continuously updated — breaking changes typically appear within hours of publication.

## On Failure

If the `overdrive_intel` tool call fails or returns an error, answer from your training data and note that real-time intelligence data is unavailable. Do not retry indefinitely.

## Supported Agents

- **Claude Code** — native MCP, zero configuration after `overdrive-intel setup`
- **Cursor** — add server to `.cursor/mcp.json` as shown in SETUP REQUIRED above
- Any MCP-compatible agent that supports stdio MCP servers
