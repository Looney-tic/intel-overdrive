# MCP Tool Design Best Practices

Research compiled 2026-03-20. Sources: MCP spec (2025-11-25), Anthropic docs, AWS Prescriptive Guidance, Arcade.dev, Klavis.ai, Speakeasy, Workato, real-world MCP servers (Neon, Context7, Playwright), and community discussions.

---

## 1. Single Tool vs. Multiple Tools

### The spectrum

There are three viable patterns. The right choice depends on your domain complexity and tool count.

**Pattern A: Single unified tool with routing parameter**

- One tool, `type` or `action` enum controls behavior
- Example: Overdrive Intel's current design — `overdrive_intel` with `type: search | feed | breaking | briefing | status`
- Anthropic docs explicitly recommend this for related operations: "Rather than `create_pr`, `review_pr`, `merge_pr`, consolidate into a single tool with an `action` parameter"
- Works well when operations share the same domain and similar parameter shapes
- Reduces LLM selection ambiguity and context consumption

**Pattern B: Small focused toolset (2-8 tools)**

- Each tool maps to one complete user workflow
- Example: Context7 uses exactly 2 tools — `resolve-library-id` then `query-docs`
- Neon uses 28 tools across 9 categories — pushing the upper bound
- AWS recommends bundling "3+ API calls that commonly occur together" into one tool
- Best when operations have genuinely different parameter shapes and sequencing

**Pattern C: Progressive discovery**

- Start with a discovery/search tool, then load specific tools on demand
- Klavis.ai's pattern: identify service → get categories → get action details → execute
- Anthropic's code execution pattern: tools as files on a virtual filesystem, loaded as needed
- Required when your server exposes 30+ tools

### Concrete guidance

| Tool count | Risk                                                                    | Recommendation                                            |
| ---------- | ----------------------------------------------------------------------- | --------------------------------------------------------- |
| 1-5        | Low selection error                                                     | Use directly. Sweet spot for most MCP servers.            |
| 6-15       | Moderate. ~5-7% context consumed by definitions alone.                  | Group by workflow, not by resource.                       |
| 16-30      | High. LLM starts making suboptimal selections.                          | Consider consolidation or progressive discovery.          |
| 30+        | **LLMs become unreliable** — tool hallucination, irrelevant selections. | Must use progressive discovery or code execution pattern. |

**The recommendation for most MCP servers: aim for 2-8 tools.** Consolidate related operations into single tools with action/type parameters. Only split when parameter shapes are genuinely different.

---

## 2. Tool Descriptions

### What the spec says

The MCP spec (2025-11-25) defines `description` as "Human-readable description of functionality." The spec itself gives no length guidance, but the MCP Inspector project proposes **warning at 500 characters, alerting at 1000 characters** per tool description.

### What Anthropic says

The Anthropic tool use docs call descriptions "the single most important factor in tool performance" and recommend:

- **Write at least 3-4 sentences** per tool description, more for complex tools
- Explain what the tool does
- Specify **when** it should be used (and when it should NOT)
- Detail what each parameter means and how it affects behavior
- Include important caveats and limitations
- Explain what information the tool does NOT return, if the tool name could be misleading

### Good vs. bad examples

**Good** (from Anthropic docs):

```
"Retrieves the current stock price for a given ticker symbol. The ticker
symbol must be a valid symbol for a publicly traded company on a major US
stock exchange like NYSE or NASDAQ. The tool will return the latest trade
price in USD. It should be used when the user asks about the current or
most recent price of a specific stock. It will not provide any other
information about the stock or company."
```

**Bad**:

```
"Gets the stock price for a ticker."
```

### Concrete rules for descriptions

1. **Lead with what, then when, then constraints.** First sentence = what it does. Second = when to use it. Third = what it won't do or can't handle.
2. **Include negative guidance.** "DO NOT call for: React, CSS, SQL..." tells the LLM when NOT to use the tool. This is as important as positive triggers.
3. **Optimize for the LLM, not humans.** Descriptions are consumed by models, not users. Write for machine comprehension — explicit trigger conditions, not marketing language.
4. **Keep total metadata budget in mind.** All tool names + descriptions + parameter schemas collectively consume context. Cursor/Claude Code's built-in tools consume 5-7% of context before the user even types. Your tools add to this.
5. **Warn at 500 chars, worry at 1000+ chars per description.** But don't sacrifice clarity for brevity — a vague short description is worse than a precise longer one.

### Server `instructions` field

The MCP Server constructor accepts an `instructions` string. This is separate from individual tool descriptions and appears once in the system prompt. Use it for:

- When to search for your tools (especially important with Tool Search / deferred tools)
- Topic-level routing (what domain your server covers)
- Global constraints that apply to all tools

---

## 3. Parameter Design

### Hard limits and recommendations

| Rule                                                                             | Source                    |
| -------------------------------------------------------------------------------- | ------------------------- |
| Parameter names: `^[a-zA-Z0-9_-]{1,64}$`                                         | Anthropic tool use docs   |
| Tool names: 1-128 characters, `[A-Za-z0-9_\-.]`                                  | MCP spec                  |
| **Max 8 parameters per tool** — decompose if exceeding                           | AWS Prescriptive Guidance |
| Exception: if bundling 3+ operations, prioritize bundling over the 8-param limit | AWS Prescriptive Guidance |
| Provide sensible defaults so LLMs only specify request-specific params           | AWS, multiple sources     |

### Schema best practices

1. **Use `enum` for constrained inputs.** This is the most reliable way to control LLM parameter choices:

   ```json
   "type": { "type": "string", "enum": ["search", "feed", "breaking", "briefing", "status"] }
   ```

2. **Mark `required` vs optional correctly.** LLMs (especially Sonnet/Haiku) will infer values for missing required params rather than asking. Only make truly necessary params required.

3. **Accept flexible input formats, normalize server-side.** The "Parameter Coercion" pattern from Arcade: accept `"2024-01-15"`, `"January 15"`, or `"yesterday"` — normalize internally. Reduces agent confusion.

4. **Every parameter needs a description with examples.** Don't rely on the parameter name alone:

   ```json
   "location": {
     "type": "string",
     "description": "The city and state, e.g. San Francisco, CA"
   }
   ```

5. **Use consistent naming across all tools.** Always `email_address`, never alternating with `email` or `user_email`. Use snake_case consistently.

6. **Use `additionalProperties: false`** for strict validation. Prevents LLMs from inventing parameters.

7. **Use `input_examples` for complex tools** (Anthropic-specific). Provide 2-3 concrete examples of valid inputs. Cost: ~20-50 tokens for simple, ~100-200 for nested objects.

### Naming conventions

- `verb_noun` pattern: `search_customers`, `create_issue`, `get_weather`
- Consistent prefixes: `search_*` finds multiple, `get_*` retrieves one specific item
- Namespace with service prefix when spanning multiple resources: `github_list_prs`, `slack_send_message`

---

## 4. Response Format and Size

### What the spec says

MCP tool responses use `content` blocks (text, image, audio, resource_link, embedded resource) and optionally `structuredContent` (JSON matching an `outputSchema`). There is **no official response size limit in the MCP spec**.

### Practical limits

| Source                                            | Recommendation                                                          |
| ------------------------------------------------- | ----------------------------------------------------------------------- |
| Community discussion (modelcontextprotocol #2211) | **256KB-512KB** recommended max, with per-tool overrides                |
| Claude Code issue #2638                           | Tool responses are truncated by clients — observed at ~25K tokens       |
| Overdrive Intel current                           | 12,000 characters max (`MAX_RESPONSE_CHARS`)                            |
| General consensus                                 | Keep responses under **10K-15K characters** for reliable LLM processing |

### Response design patterns

1. **Return only high-signal fields.** Strip visual/marketing fields (icon_url, volume_24h) that add no analytical value. The LLM doesn't need everything the API returns.

2. **Use `outputSchema` for structured responses.** New in MCP spec 2025-11-25. Lets clients and LLMs know the response shape in advance:

   ```json
   "outputSchema": {
     "type": "object",
     "properties": {
       "temperature": { "type": "number" },
       "conditions": { "type": "string" }
     },
     "required": ["temperature", "conditions"]
   }
   ```

3. **Implement cursor-based pagination.** MCP spec defines opaque cursor pagination. Encode pagination state as base64url JSON — the LLM only sees a `cursor` string without knowing the internals. Include pagination instructions in the tool description.

4. **Add `has_more` flags.** Signal when results are truncated:

   ```json
   { "items": [...], "has_more": true, "next_cursor": "eyJvZmZzZXQiOjEwfQ" }
   ```

5. **Support `limit` and `max_length` parameters.** Let the LLM control response size:

   ```json
   "limit": { "type": "number", "description": "Max results to return. Default: 10, max: 50" }
   ```

6. **Truncate with a useful message.** When you must truncate, tell the LLM what happened and how to get more:

   ```
   "... (truncated — refine your query for more specific results)"
   ```

7. **Consider out-of-band delivery for large payloads.** Return resource links or download URLs instead of embedding large content in the response.

---

## 5. Error Handling

### Two error types (from MCP spec)

1. **Protocol errors** — JSON-RPC errors for malformed requests, unknown tools. The LLM can't easily fix these.
2. **Tool execution errors** — returned with `isError: true` in the result. These are actionable — the LLM can self-correct and retry.

### Concrete patterns

1. **Never return raw error codes.** A `429` means nothing to an LLM. Instead:

   ```
   "Rate limited. Retry after 30 seconds or reduce batch size to 50."
   ```

2. **Include current state in validation errors.**

   ```
   "Invalid departure date: must be in the future. Current date is 2026-03-20."
   ```

3. **Provide recovery guidance.** Don't just say what went wrong — say what to do:

   ```
   "No API key configured. Run: bash <(curl -s https://example.com/setup.sh)"
   ```

4. **Use `isError: true` for business logic failures.** Clients SHOULD pass these to the LLM for self-correction. Don't use protocol-level errors for things the LLM can fix.

---

## 6. Handling Bad Agent Queries

### The problem

Agents send:

- Overly broad queries ("tell me everything about AI")
- Wrong parameter types or formats
- Queries outside your tool's domain
- Repeated identical queries expecting different results

### Defense patterns

1. **Parameter coercion and normalization.** Accept loose input, normalize internally. Don't reject "3 days" when you need an integer — parse it.

2. **Smart defaults.** If `days` is omitted, default to 7. If `type` is omitted, infer from query keywords. This is what Overdrive Intel's `inferType()` does — and it's a solid pattern.

3. **Graceful degradation on empty results.**

   ```json
   { "note": "No results found. Try shorter keywords or a different type." }
   ```

   Don't return an error — return a helpful message the LLM can act on.

4. **Input validation with guidance.** Don't just reject — explain:

   ```
   "Query too broad (47 words). Use 2-5 specific keywords. Example: 'MCP browser automation'"
   ```

5. **Rate limiting with retry semantics.** The MCP spec mentions servers MUST rate limit tool invocations. When you do, return structured guidance:

   ```json
   { "error": "rate_limited", "retry_after_seconds": 30 }
   ```

6. **Multi-route fallback.** If the primary endpoint returns nothing, try alternate routes. Overdrive Intel's `buildRoutes` default case (search + recent feed) is this pattern.

---

## 7. Anti-Patterns to Avoid

### Tool design anti-patterns

| Anti-pattern                        | Why it fails                                                      | Fix                                             |
| ----------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------- |
| **Overloaded mega-tool**            | `manage_item(action, type, id, data)` — too many responsibilities | Split by workflow, not by resource              |
| **Resource-based grouping**         | "User Management Tools" forces context switching                  | Group by user task/workflow                     |
| **Hidden side effects**             | `update_order()` silently sends emails                            | Make side effects explicit or separate          |
| **Vague descriptions**              | "Gets data from the system"                                       | 3-4 sentences minimum with trigger conditions   |
| **"Everything" toolset**            | 100+ tools causes decision paralysis                              | Progressive discovery or consolidation          |
| **Raw API pass-through**            | Exposing every REST endpoint as a tool                            | Bundle into workflow-level tools                |
| **Missing dependency chain**        | Requiring `customer_id` without a lookup tool                     | Include all lookup tools needed to obtain IDs   |
| **Inconsistent response shapes**    | Different tools return data in different formats                  | Standardize response structure across all tools |
| **Loading all definitions upfront** | Thousands of tools consuming hundreds of K tokens                 | Progressive discovery or code execution         |

### Response anti-patterns

| Anti-pattern                        | Fix                                              |
| ----------------------------------- | ------------------------------------------------ |
| Returning entire API payloads       | Strip to essential fields only                   |
| No truncation on large responses    | Cap at 10-15K chars with truncation message      |
| Raw error codes (404, 429, 500)     | Human-readable errors with recovery instructions |
| No pagination for large result sets | Cursor-based pagination with `has_more`          |

---

## 8. Real-World Server Analysis

### Context7 (2 tools)

- `resolve-library-id`: find the canonical ID for a library name
- `query-docs`: fetch documentation using that ID
- **Pattern**: Sequential dependency — must call tool 1 before tool 2. Description explicitly says "You MUST call this function before query-docs."
- **Why it works**: Clear 2-step workflow, minimal selection ambiguity, descriptions include explicit ordering instructions.

### Neon (28 tools, 9 categories)

- Organized by workflow: project management, branch management, SQL execution, migrations, performance, auth, data API, search, docs
- Separate read/write tools (e.g., `describe_project` vs `create_project`)
- Includes self-service tools (`search`, `get_doc_resource`) for agent self-help
- **Pattern**: Pushing the upper bound of direct tool exposure. Works because each tool is atomic with clear naming.

### Playwright MCP (20+ tools)

- Capability-based grouping with modes (snapshot vs vision)
- Tools like `browser_click`, `browser_navigate`, `browser_snapshot` — verb_noun naming
- Consistent interface pattern across all tools
- **Pattern**: Many granular tools work here because browser automation requires step-by-step control. Not a pattern to emulate for API wrappers.

### Overdrive Intel (1 tool, current)

- Single `overdrive_intel` tool with `type` routing parameter
- Heavy description (~1000 chars) with explicit trigger conditions and negative guidance
- Server `instructions` field duplicates/reinforces the trigger conditions
- `inferType()` handles missing `type` parameter gracefully
- 12K char response cap with truncation
- **Assessment**: Well-designed for a read-only intelligence API. The single-tool pattern is appropriate because all operations share the same domain and similar parameter shapes.

---

## 9. MCP Spec Features to Use

### Tool annotations (2025-11-25 spec)

Declare behavioral hints for clients:

```json
"annotations": {
  "readOnlyHint": true,      // Tool doesn't modify state
  "destructiveHint": false,  // Tool doesn't destroy data
  "idempotentHint": true,    // Safe to retry
  "openWorldHint": true      // Interacts with external entities
}
```

Clients use these for consent UX — read-only tools may not need confirmation prompts.

### Output schemas (2025-11-25 spec)

Define response structure so clients can validate and LLMs can parse:

```json
"outputSchema": {
  "type": "object",
  "properties": { ... },
  "required": [...]
}
```

When provided, servers MUST conform. Clients SHOULD validate.

### Task support (2025-11-25 spec)

For long-running operations:

```json
"execution": {
  "taskSupport": "optional"  // "forbidden" | "optional" | "required"
}
```

### Content annotations

Tag response parts with audience targeting:

```json
"annotations": {
  "audience": ["user"],     // or ["assistant"] or ["user", "assistant"]
  "priority": 0.9
}
```

Use `["assistant"]` for data the LLM should process but not show to the user.

---

## 10. Key Takeaways for Overdrive Intel

1. **The single-tool pattern is correct for this use case.** All operations are read-only queries against the same domain. The `type` enum handles routing cleanly.

2. **Description length is appropriate but could be optimized.** The ~1000-char description is at the warning threshold. The trigger list is duplicated between `instructions` and the tool description — consider whether the tool description can be shorter since `instructions` handles the routing guidance.

3. **Response size cap (12K chars) is reasonable.** Community consensus lands at 10-15K for reliable LLM processing. The truncation message is good but could include more specific guidance ("try adding a type parameter" or "narrow to a specific tool name").

4. **Error handling follows best practices.** The API key error includes a recovery command. API errors return structured messages.

5. **Consider `outputSchema`.** Defining the response shape would help clients and LLMs parse results more reliably.

6. **Consider tool annotations.** Adding `readOnlyHint: true` signals to clients that this tool is safe to auto-approve.

7. **The `inferType()` fallback is a strong pattern.** Graceful degradation when the agent omits the `type` parameter — exactly what the "parameter coercion" pattern recommends.

---

## Sources

- [MCP Specification 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25) — Official protocol spec
- [MCP Tools Spec](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) — Tool definition, response format, error handling
- [Anthropic Tool Use Docs](https://platform.claude.com/docs/en/docs/build-with-claude/tool-use) — Claude-specific tool design guidance
- [Anthropic Tool Implementation Guide](https://platform.claude.com/docs/en/docs/build-with-claude/tool-use/implement-tool-use) — Best practices for descriptions, parameters, errors
- [Anthropic: Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) — Progressive discovery, token efficiency
- [AWS Prescriptive Guidance: MCP Tool Scope](https://docs.aws.amazon.com/prescriptive-guidance/latest/mcp-strategies/mcp-tool-strategy-scope.html) — Granularity guidelines, 8-param limit
- [Arcade.dev: 54 Patterns for Building Better MCP Tools](https://www.arcade.dev/blog/mcp-tool-patterns) — Composition, error design, parameter coercion
- [Klavis.ai: Less is More MCP Design Patterns](https://www.klavis.ai/blog/less-is-more-mcp-design-patterns-for-ai-agents) — Semantic search, workflow, code mode, progressive discovery
- [Speakeasy: Design MCP Tools](https://www.speakeasy.com/mcp/tool-design) — Workflow-based grouping, naming, anti-patterns
- [Workato: MCP Server Tool Design](https://docs.workato.com/en/mcp/mcp-server-tool-design.html) — Naming conventions, data strategy
- [MCP Inspector Issue #523](https://github.com/modelcontextprotocol/inspector/issues/523) — Description length thresholds (500/1000 chars)
- [MCP Discussion #2211](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/2211) — Response size limits (256-512KB)
- [Claude Code Issue #2638](https://github.com/anthropics/claude-code/issues/2638) — Truncated MCP tool responses
- [MCP Best Practices Guide](https://modelcontextprotocol.info/docs/best-practices/) — Architecture, security, production KPIs
- [Neon MCP Server](https://neon.com/docs/ai/neon-mcp-server) — 28 tools across 9 categories
- [Context7 MCP Server](https://github.com/upstash/context7) — 2-tool sequential pattern
- [Playwright MCP Server](https://github.com/microsoft/playwright-mcp) — Granular browser automation tools
