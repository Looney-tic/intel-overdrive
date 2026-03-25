# Overdrive Intel — Claude Code Plugin

Live intelligence feed for the AI coding ecosystem. Monitors 550+ sources daily for updates on MCP servers, Claude Code, LLM APIs, agent frameworks, and AI coding tools.

## What it does

- **MCP Server** (`overdrive_intel` tool) — ask any question about the AI coding ecosystem and get curated, up-to-date answers
- **SessionStart Hook** — automatically detects AI/MCP projects and injects relevant context, including any breaking changes
- **Slash Commands** — quick access to feeds, breaking changes, search, and briefings

## Installation

### One-line install (recommended)

Installs the CLI and registers the MCP server at **user scope** (available in all projects):

```bash
curl -fsSL https://raw.githubusercontent.com/Looney-tic/intel-overdrive/main/install.sh | bash
```

### MCP server only

If you just want the MCP tool in Claude Code:

```bash
claude mcp add -t stdio -s user \
  -e OVERDRIVE_API_URL=https://inteloverdrive.com \
  -e OVERDRIVE_API_KEY=dti_v1_YOUR_KEY \
  overdrive-intel -- npx -y overdrive-intel-mcp@latest
```

> **Important:** Use `--scope user` so the tool is available in all your projects, not just the current one.

### From the Claude Code marketplace (coming soon)

```bash
claude plugin install overdrive-intel
```

### Configuration

Set your API key using one of these methods (in priority order):

1. **Key file** (`~/.config/overdrive-intel/key`) — set automatically by the install script:

   ```
   dti_v1_your_key_here
   ```

2. **Environment variable:**

   ```bash
   export OVERDRIVE_INTEL_API_KEY="dti_v1_your_key_here"
   ```

3. **Config file** (`~/.config/overdrive-intel/config.json`):
   ```json
   {
     "api_key": "dti_v1_your_key_here"
   }
   ```

#### Custom API URL

By default, the plugin connects to `https://inteloverdrive.com`. To use a different endpoint:

```bash
export OVERDRIVE_INTEL_API_URL="https://your-instance.example.com"
```

## Slash Commands

| Command           | Description                                            |
| ----------------- | ------------------------------------------------------ |
| `/intel-feed`     | Get the latest AI coding ecosystem updates             |
| `/intel-breaking` | Check for breaking changes that need attention         |
| `/intel-search`   | Search the intelligence database for specific topics   |
| `/intel-brief`    | Get a context-packed briefing tailored to your project |

## MCP Tool

The plugin provides a single MCP tool: `overdrive_intel`

```
overdrive_intel({ query: "best MCP servers for browser automation" })
overdrive_intel({ query: "what changed in the Anthropic SDK recently" })
overdrive_intel({ query: "breaking changes this week" })
overdrive_intel({ query: "briefing on agent frameworks" })
```

The tool automatically routes your query to the right API endpoints (feed, search, library, context packs) based on intent.

## Coverage

The intelligence feed monitors:

- **AI Coding Assistants**: Claude Code, Cursor, Copilot, Windsurf, Codex
- **LLM APIs & SDKs**: Anthropic, OpenAI, Google Gemini, Mistral
- **Agent Frameworks**: LangChain, CrewAI, AutoGen, Pydantic AI, smolagents
- **MCP Ecosystem**: Protocol updates, server registry, best practices
- **AI Coding Patterns**: Prompt engineering, agentic workflows, tool use

## Development

```bash
# Build the MCP server
cd ../overdrive-intel-mcp
npm install
npm run build

# Test the hook locally
echo '{"cwd": "/path/to/ai-project"}' | bash hooks/session-start.sh
```

## License

MIT
