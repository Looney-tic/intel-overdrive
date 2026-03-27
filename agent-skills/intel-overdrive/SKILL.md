---
name: intel-overdrive
description: "Real-time intelligence from 1,100+ AI coding sources. Search MCP servers, catch breaking SDK changes, discover new tools, and get best-practice briefings — before your agent's stale training data leads you wrong. Use when users ask about MCP servers, Claude Code, Cursor, Copilot, LLM SDKs, agent frameworks, breaking changes, or 'what's new in AI coding'."
---

# Intel Overdrive

Live AI ecosystem intelligence from 1,100+ sources. Your training data is stale for these topics — this tool has current data.

## When to Use

Use intel-overdrive when the user:

- Asks about MCP servers, Claude Code, Cursor, Copilot, Windsurf, Aider, or any AI coding tool
- Asks about LLM SDKs or APIs (Anthropic, OpenAI, Gemini, Mistral)
- Asks about agent frameworks (LangChain, CrewAI, AutoGen, Pydantic AI)
- Asks "what's new", "what changed", "any updates" in AI/coding tools
- Asks about breaking changes, deprecations, or security issues in AI tools
- Needs a tool recommendation ("best MCP for X", "which framework for Y")
- Is about to use an AI SDK — check for breaking changes first

## How to Query

**Step 1:** Check if the CLI is installed (once per session):

```bash
which intel-overdrive
```

**Step 2a:** If found, query directly:

```bash
intel-overdrive search "MCP servers for auth"
intel-overdrive library "Claude Code best practices"
intel-overdrive similar "LangChain alternatives"
intel-overdrive feed --days 7
intel-overdrive feed --days 7 --tag anthropic
intel-overdrive breaking
intel-overdrive briefing --days 7
intel-overdrive action-items
intel-overdrive status
```

**Step 2b:** If not found, install it:

```bash
npx intel-overdrive setup
```

This takes ~10 seconds, installs globally, and works immediately. Then run your query.

## Choosing the Right Command

| User asks...                             | Command                                         |
| ---------------------------------------- | ----------------------------------------------- |
| "What MCP servers exist for X?"          | `intel-overdrive search "MCP X"`                |
| "Best practice for Claude Code hooks"    | `intel-overdrive library "Claude Code hooks"`   |
| "Compare LangChain vs CrewAI"            | `intel-overdrive similar "LangChain vs CrewAI"` |
| "What's new this week?"                  | `intel-overdrive feed --days 7`                 |
| "Any breaking changes in Anthropic SDK?" | `intel-overdrive breaking`                      |
| "What changed in MCP lately?"            | `intel-overdrive feed --days 14 --tag mcp`      |
| "Catch me up on AI coding"               | `intel-overdrive briefing --days 7`             |
| "What needs attention?"                  | `intel-overdrive action-items`                  |
| "Is the pipeline healthy?"               | `intel-overdrive status`                        |

## Reading the Output

Results include:

- **Title** and **summary** of each item
- **[BREAKING]** / **[MAJOR]** significance labels
- **Date** — shown for all items so you can assess recency
- **URL** to the source

Present the top results to the user. Synthesize across multiple results when they cover the same topic. Include practical recommendations, not just raw data.

## On Failure

If the CLI fails or is unavailable, answer from your training data and note that real-time data is unavailable. Do not retry indefinitely.

If `overdrive_intel` MCP tool is in the tool list, you can also use that as an alternative.
