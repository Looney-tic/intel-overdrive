# /intel-feed

Get the latest AI coding ecosystem updates from Overdrive Intel.

## When to use

When the user wants to know what's new, what changed recently, or wants a feed of updates about AI coding tools, MCP servers, LLM APIs, or agent frameworks.

## Instructions

Use the `overdrive_intel` MCP tool with a query about recent updates.

### Examples

**Latest updates (default — last 7 days):**

```
overdrive_intel({ query: "what's new this week", type: "feed" })
```

**Updates for a specific tool or topic:**

```
overdrive_intel({ query: "Claude Code updates", type: "feed" })
overdrive_intel({ query: "OpenAI SDK changes", type: "feed" })
overdrive_intel({ query: "new MCP servers", type: "feed" })
```

**Broader timeframe:**

```
overdrive_intel({ query: "AI coding updates", type: "feed", days: 30 })
```

## Output format

Present the results as a concise summary:

1. Group updates by significance (breaking > major > minor)
2. Lead with actionable items
3. Include links/references when available
4. Skip items that aren't relevant to the user's current project context
