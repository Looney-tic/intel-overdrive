# /intel-breaking

Check for breaking changes in AI coding tools, MCP servers, LLM APIs, and agent frameworks.

## When to use

When the user wants to check if anything broke, needs to know about urgent updates, or before upgrading dependencies related to AI/LLM tools.

## Instructions

Use the `overdrive_intel` MCP tool with a breaking changes query.

### Examples

**All recent breaking changes:**

```
overdrive_intel({ query: "breaking changes", type: "breaking" })
```

**Breaking changes for a specific tool:**

```
overdrive_intel({ query: "Anthropic SDK", type: "breaking" })
overdrive_intel({ query: "MCP protocol", type: "breaking" })
overdrive_intel({ query: "Claude Code", type: "breaking" })
```

**Breaking changes over a longer window:**

```
overdrive_intel({ query: "breaking changes", type: "breaking", days: 30 })
```

## Output format

Present breaking changes with urgency:

1. Lead with the most critical/urgent items
2. For each breaking change, include:
   - What changed
   - What breaks
   - Migration path or workaround (if available)
3. Flag anything that affects the user's current project dependencies
