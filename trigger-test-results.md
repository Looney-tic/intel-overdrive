# Overdrive Intel MCP Tool — Trigger Rate Test Results

**Date:** 2026-03-20
**Test method:** 20 subagents spawned in 4 batches of 5, each answering a prompt naturally (no mention of overdrive_intel or that it was a test). Each subagent read CLAUDE-slim.md and reported tools consulted.

## Results

| #   | Prompt                                                             | Expected | Fired? | Correct? | Notes                                     |
| --- | ------------------------------------------------------------------ | -------- | ------ | -------- | ----------------------------------------- |
| 1   | What MCP servers are best for browser automation?                  | Yes      | Yes    | ✅       | Also used WebSearch, claude-mem, context7 |
| 2   | Any breaking changes in Claude Code or the Anthropic SDK recently? | Yes      | Yes    | ✅       | Also used claude-mem, Bash, WebFetch      |
| 3   | LangChain vs CrewAI — which is better for my use case?             | Yes      | Yes    | ✅       | Also used claude-mem, context7            |
| 4   | What are the best practices for using Claude Code effectively?     | Yes      | Yes    | ✅       | Also used context7                        |
| 5   | What changed in the OpenAI Python SDK recently?                    | Yes      | Yes    | ✅       | Also used WebSearch, context7, WebFetch   |
| 6   | What new AI coding tools have come out?                            | Yes      | Yes    | ✅       | Also used WebSearch                       |
| 7   | How do I build an MCP server in Python?                            | Yes      | Yes    | ✅       | Also used context7                        |
| 8   | How do I set up Cursor for a monorepo?                             | Yes      | Yes    | ✅       | Also used context7                        |
| 9   | Aider vs Continue vs Copilot — which should I use?                 | Yes      | Yes    | ✅       | Also used claude-mem, WebSearch           |
| 10  | What agent frameworks support MCP natively?                        | Yes      | Yes    | ✅       | Also used WebSearch                       |
| 11  | What are the gotchas with Claude Code hooks?                       | Yes      | Yes    | ✅       | Also used context7, claude-mem, Glob      |
| 12  | Best embedding models for code search?                             | Yes      | Yes    | ✅       | Also used claude-mem, WebSearch           |
| 13  | How does React useEffect cleanup work?                             | No       | No     | ✅       | Answered from training data only          |
| 14  | Explain PostgreSQL window functions                                | No       | No     | ✅       | Answered from training data only          |
| 15  | Build me a responsive CSS grid layout                              | No       | No     | ✅       | Answered from training data only          |
| 16  | Write a multi-stage Dockerfile for a Node.js app                   | No       | No     | ✅       | Answered from training data only          |
| 17  | asyncio.gather vs TaskGroup — when to use which?                   | No       | No     | ✅       | Answered from training data only          |
| 18  | How do I set up ESLint for a TypeScript monorepo?                  | No       | No     | ✅       | Used context7 but NOT overdrive_intel     |
| 19  | Write a bash script that monitors disk usage                       | No       | No     | ✅       | Used Write/Bash to create the script      |
| 20  | How do I implement JWT refresh token rotation?                     | No       | No     | ✅       | Answered from training data only          |

## Summary

- **Should-fire hit rate:** 12 / 12 (100%)
- **Should-not-fire hit rate:** 8 / 8 (100%)
- **Overall accuracy:** 20 / 20 (100%)

## Analysis

### No misses or false positives

Every should-fire prompt correctly triggered overdrive_intel. Every should-not-fire prompt correctly avoided it. The MCP server description's topic list and the REQUIRED instruction in the server instructions appear to be working as intended.

### Observations

1. **Topic boundary is clean.** The tool correctly distinguished between AI/MCP ecosystem questions (fire) and general programming questions (don't fire), even for edge cases like ESLint setup (#18) which touches tooling but is not AI-specific.

2. **Subagents respect the MCP server instructions.** Despite only receiving CLAUDE-slim.md (not the full CLAUDE.md), subagents still followed the MCP server's "REQUIRED" instruction to call overdrive_intel for matching topics. This confirms the MCP server instructions in the system prompt are sufficient for triggering.

3. **Complementary tool usage is healthy.** Subagents that fired overdrive_intel also frequently used context7 (for library docs), WebSearch (for recent data), and claude-mem (for past research). The tool is being used as one input among many, not as a sole source.

4. **Token cost varies significantly.** Should-fire prompts averaged ~33K tokens and ~15 tool calls. Should-not-fire prompts averaged ~19K tokens and ~1-2 tool calls. The overdrive_intel enrichment roughly doubles the cost per query.

### Potential improvements to test

- Test with prompts that are borderline (e.g., "How do I use WebSockets in Python?" — general topic but could touch AI SDK transports)
- Test with prompts that mention AI tools tangentially (e.g., "Write a GitHub Action that runs ESLint and Copilot review")
- Test at different model temperatures or with different subagent types
