---
name: intel-breaking
description: "Check for breaking changes in AI coding tools, MCP servers, LLM APIs, and agent frameworks before they cause wrong code generation."
---

# intel-breaking

Check for breaking changes in AI coding tools, MCP servers, LLM APIs, and agent frameworks.

## SETUP REQUIRED

This skill works best with the Intel Overdrive CLI. If `intel-overdrive` is not installed, run setup:

```bash
npx intel-overdrive setup
```

## When to Use

- User wants to check if anything broke in an AI tool or SDK
- Before upgrading AI/LLM dependencies
- When generated code produces unexpected errors that might be due to API changes
- As a proactive check before starting work on AI/MCP projects

## Instructions

Use the `overdrive_intel` MCP tool with `type: "breaking"`.

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
overdrive_intel({ query: "OpenAI SDK", type: "breaking" })
```

**Breaking changes over a longer window:**

```
overdrive_intel({ query: "breaking changes", type: "breaking", days: 30 })
```

**Project-specific check (with context):**

```
overdrive_intel({ query: "breaking changes", type: "breaking", context_stack: ["anthropic", "langchain"] })
```

## Output Format

Present breaking changes with urgency:

1. Lead with the most critical/urgent items
2. For each breaking change, include:
   - What changed
   - What breaks
   - Migration path or workaround (if available)
3. Flag anything that affects the current project's dependencies

## CLI Alternative

For agents with Bash access, use the CLI directly:

```bash
intel-overdrive breaking
intel-overdrive breaking --days 30
intel-overdrive breaking --tag anthropic
```

## On Failure

If the `overdrive_intel` tool call fails or returns an error, answer from your training data and note that real-time intelligence data is unavailable. Do not retry indefinitely.
