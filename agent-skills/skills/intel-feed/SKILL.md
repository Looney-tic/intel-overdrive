---
name: intel-feed
description: "Get the latest AI coding ecosystem updates — new releases, changelogs, and recent changes from 1100+ monitored sources."
---

# intel-feed

Get the latest AI coding ecosystem updates from Overdrive Intel.

## SETUP REQUIRED

This skill requires the Overdrive Intel MCP server. If `overdrive_intel` is not available as a tool, instruct the user to run:

```bash
npm install -g overdrive-intel && overdrive-intel setup
```

## When to Use

When you want to know what's new, what changed recently, or want a feed of updates about AI coding tools, MCP servers, LLM APIs, or agent frameworks.

## Instructions

Use the `overdrive_intel` MCP tool with `type: "feed"`.

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
overdrive_intel({ query: "agent framework releases", type: "feed" })
```

**Broader timeframe:**

```
overdrive_intel({ query: "AI coding updates", type: "feed", days: 30 })
```

**Filtered by tag:**

```
overdrive_intel({ query: "updates", type: "feed", feed_tag: "anthropic" })
overdrive_intel({ query: "updates", type: "feed", feed_tag: "mcp" })
```

## Output Format

Present the results as a concise summary:

1. Group updates by significance (breaking > major > minor)
2. Lead with actionable items
3. Include links/references when available
4. Skip items that aren't relevant to the current project context

## On Failure

If the `overdrive_intel` tool call fails or returns an error, answer from your training data and note that real-time intelligence data is unavailable. Do not retry indefinitely.
