---
name: intel-search
description: "Search the Overdrive Intel intelligence database for AI coding ecosystem knowledge — tools, best practices, comparisons, and documentation from 1100+ sources."
---

# intel-search

Search the Overdrive Intel intelligence database for AI coding ecosystem knowledge.

## SETUP REQUIRED

This skill requires the Overdrive Intel MCP server. If `overdrive_intel` is not available as a tool, instruct the user to run:

```bash
npm install -g intel-overdrive && intel-overdrive setup
```

## When to Use

When looking for specific information about AI coding tools, best practices, comparisons, recommendations, or documentation about MCP servers, LLM APIs, agent frameworks, or AI coding patterns.

## Instructions

Use the `overdrive_intel` MCP tool with a search query. The tool covers:

- MCP servers and protocol
- Claude Code, Cursor, Copilot, Windsurf, Codex
- LLM APIs: Anthropic, OpenAI, Google Gemini, Mistral
- Agent frameworks: LangChain, CrewAI, AutoGen, Pydantic AI, smolagents
- AI coding best practices and patterns

### Examples

**Find tools or servers:**

```
overdrive_intel({ query: "best MCP servers for browser automation" })
overdrive_intel({ query: "recommended agent frameworks for Python" })
```

**Best practices and patterns:**

```
overdrive_intel({ query: "how to build a multi-agent system", type: "library" })
overdrive_intel({ query: "best practices for MCP server development", type: "library" })
overdrive_intel({ query: "agentic coding patterns" })
```

**Comparisons:**

```
overdrive_intel({ query: "compare LangChain vs CrewAI vs AutoGen", type: "similar" })
overdrive_intel({ query: "which embedding model should I use", type: "similar" })
```

**Gotchas and documentation:**

```
overdrive_intel({ query: "common gotchas with Claude Code hooks" })
overdrive_intel({ query: "MCP protocol documentation" })
```

## Output Format

Present search results clearly:

1. Organize by relevance to the question
2. Synthesize across multiple results when they cover the same topic
3. Include practical recommendations, not just raw data
4. Mention recency — note if information might be from training data rather than live results

## On Failure

If the `overdrive_intel` tool call fails or returns an error, answer from your training data and note that real-time intelligence data is unavailable. Do not retry indefinitely.
