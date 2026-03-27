---
name: intel-brief
description: "Get a context-packed AI coding ecosystem briefing — synthesized intelligence on any topic or tailored to your current project's technology stack."
---

# intel-brief

Get a context-packed intelligence briefing tailored to your current project or a specific topic.

## SETUP REQUIRED

This skill requires the Overdrive Intel MCP server. If `overdrive_intel` is not available as a tool, instruct the user to run:

```bash
npm install -g overdrive-intel && overdrive-intel setup
```

## When to Use

When you want a comprehensive briefing or summary of the AI coding ecosystem, want to catch up on what you need to know, or want context relevant to the current project's technology stack.

## Instructions

1. First, scan the current project to identify AI/MCP technologies in use:
   - Check `package.json`, `pyproject.toml`, `requirements.txt` for AI-related dependencies
   - Check for MCP configs, agent configurations, or AI tooling directories
   - Note the specific tools, SDKs, and frameworks in use

2. Then use the `overdrive_intel` MCP tool with `type: "briefing"`:

### Examples

**General briefing:**

```
overdrive_intel({ query: "AI coding ecosystem", type: "briefing" })
```

**Project-specific briefing (after scanning dependencies):**

```
overdrive_intel({ query: "Anthropic SDK, MCP protocol, LangChain", type: "briefing" })
overdrive_intel({ query: "Claude Code and MCP servers", type: "briefing" })
```

**With project context stack:**

```
overdrive_intel({ query: "briefing", type: "briefing", context_stack: ["anthropic", "langchain", "openai"] })
```

**Topic-specific overview:**

```
overdrive_intel({ query: "agent frameworks", type: "briefing" })
overdrive_intel({ query: "MCP ecosystem changes", type: "briefing" })
```

**Extended timeframe:**

```
overdrive_intel({ query: "AI coding tools", type: "briefing", days: 30 })
```

## Output Format

Present the briefing as a structured document:

1. **Headlines** — 2-3 most important items across the ecosystem
2. **Relevant to your project** — updates that directly affect technologies in use
3. **Trends** — emerging patterns worth watching
4. **Action items** — anything you should do or consider doing

Keep it concise. A briefing should be scannable in under a minute.

## On Failure

If the `overdrive_intel` tool call fails or returns an error, answer from your training data and note that real-time intelligence data is unavailable. Do not retry indefinitely.
