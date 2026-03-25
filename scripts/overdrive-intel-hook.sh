#!/bin/bash
# Overdrive Intel -- Claude Code SessionStart hook
# Detects AI/MCP projects and injects context about available intelligence
#
# Installed via: overdrive-intel hook install
# Registered in: ~/.claude/settings.json under hooks.SessionStart

# Exit silently if overdrive-intel not installed
if ! command -v overdrive-intel &>/dev/null; then exit 0; fi

# Read stdin (Claude Code passes session context as JSON on stdin)
INPUT=$(cat)

# Extract CWD from input or use current directory
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd','.'))" 2>/dev/null || echo ".")

# Check for AI/MCP project indicators
AI_INDICATORS=0
for f in "$CWD"/*.md "$CWD"/*.toml "$CWD"/*.json "$CWD"/.claude* "$CWD"/CLAUDE.md; do
  if [ -f "$f" ] && grep -qil 'mcp\|claude\|anthropic\|agent\|llm\|langchain\|openai' "$f" 2>/dev/null; then
    AI_INDICATORS=$((AI_INDICATORS + 1))
  fi
done

# Need at least 1 indicator to inject
if [ "$AI_INDICATORS" -lt 1 ]; then exit 0; fi

# Inject context
echo ""
echo "## Overdrive Intel Available"
echo ""
echo "This project uses AI/MCP patterns. Overdrive Intel provides curated ecosystem intelligence."
echo ""
echo "Quick commands:"
echo "- \`overdrive-intel feed --days 7 --type update\` -- recent updates"
echo "- \`overdrive-intel library recommend\` -- best practices for your stack"
echo "- \`overdrive-intel search \"mcp server\"\` -- search intelligence"
echo ""
echo "MCP tool: \`intel_library\` for best practices, \`intel_feed\` for what's new"
