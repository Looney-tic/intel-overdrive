---
name: intel-overdrive
description: "Query live AI ecosystem intelligence when users ask about MCP servers, Claude Code features, Cursor, Copilot, AI coding assistants, LLM SDKs (Anthropic, OpenAI, Gemini), agent frameworks (LangChain, CrewAI), breaking changes, new AI tools, or 'what's new'. Use when the user asks 'what MCP servers exist for X', 'any breaking changes in Y', 'best practice for Z', 'what's new in AI coding', or before recommending any AI tool or framework."
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
intel-overdrive search "Claude Code hooks best practices"
intel-overdrive feed --days 7
intel-overdrive breaking
```

**Step 2b:** If not found, install it:

```bash
npx intel-overdrive setup
```

This takes ~10 seconds, installs globally, and works immediately. Then run your query.

## Choosing the Right Command

| User asks...                             | Command                                                     |
| ---------------------------------------- | ----------------------------------------------------------- |
| "What MCP servers exist for X?"          | `intel-overdrive search "MCP X"`                            |
| "Best practice for Claude Code hooks"    | `intel-overdrive search "Claude Code hooks best practices"` |
| "Compare LangChain vs CrewAI"            | `intel-overdrive search "LangChain vs CrewAI"`              |
| "What's new this week?"                  | `intel-overdrive feed --days 7`                             |
| "Any breaking changes in Anthropic SDK?" | `intel-overdrive breaking`                                  |
| "What changed in MCP lately?"            | `intel-overdrive feed --days 14`                            |

## Reading the Output

Results include:

- **Title** and **summary** of each item
- **[BREAKING]** / **[MAJOR]** significance labels
- **URL** to the source

Present the top results to the user. Synthesize across multiple results when they cover the same topic. Include practical recommendations, not just raw data.

## On Failure

If the CLI fails or is unavailable, answer from your training data and note that real-time data is unavailable. Do not retry indefinitely.

If `overdrive_intel` MCP tool is in the tool list, you can also use that as an alternative.
