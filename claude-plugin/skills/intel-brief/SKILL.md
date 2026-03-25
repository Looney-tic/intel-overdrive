# /intel-brief

Get a context-packed intelligence briefing tailored to your current project.

## When to use

When the user wants a comprehensive briefing or summary of the AI coding ecosystem, wants to catch up on what they need to know, or wants context relevant to their current project's technology stack.

## Instructions

1. First, scan the current project to identify AI/MCP technologies in use:
   - Check `package.json`, `pyproject.toml`, `requirements.txt` for AI-related dependencies
   - Check for `.claude/` directory, MCP configs, agent configurations
   - Note the specific tools, SDKs, and frameworks in use

2. Then use the `overdrive_intel` MCP tool to get a targeted briefing:

### Examples

**General briefing:**

```
overdrive_intel({ query: "briefing on AI coding ecosystem" })
```

**Project-specific briefing (after scanning dependencies):**

```
overdrive_intel({ query: "briefing on Anthropic SDK, MCP protocol, LangChain" })
overdrive_intel({ query: "catch me up on Claude Code and MCP servers" })
```

**Topic-specific overview:**

```
overdrive_intel({ query: "overview of agent frameworks" })
overdrive_intel({ query: "summarize MCP ecosystem changes" })
```

## Output format

Present the briefing as a structured document:

1. **Headlines** — 2-3 most important items across the ecosystem
2. **Relevant to your project** — updates that directly affect technologies you're using
3. **Trends** — emerging patterns worth watching
4. **Action items** — anything you should do or consider doing

Keep it concise. A briefing should be scannable in under a minute.
