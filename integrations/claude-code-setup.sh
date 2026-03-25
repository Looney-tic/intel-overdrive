#!/bin/bash
# Install the overdrive-intel skill globally for Claude Code.
# After running this, the skill is available in ALL Claude Code projects,
# not just this repo.
#
# Usage: bash integrations/claude-code-setup.sh

set -euo pipefail

SKILL_SRC="$(cd "$(dirname "$0")/.." && pwd)/.claude/skills/overdrive-intel"
SKILL_DST="${HOME}/.claude/skills/overdrive-intel"

if [[ ! -f "$SKILL_SRC/SKILL.md" ]]; then
  echo "Error: SKILL.md not found at $SKILL_SRC"
  echo "Run this script from the overdrive-intel repo root or its integrations/ directory."
  exit 1
fi

mkdir -p "$SKILL_DST"
cp "$SKILL_SRC/SKILL.md" "$SKILL_DST/SKILL.md"

echo "Installed skill to $SKILL_DST/SKILL.md"
echo ""
echo "The overdrive-intel skill is now available globally in Claude Code."
echo "Trigger it by asking Claude about Claude Code tools, MCP servers, or model updates."
echo ""
echo "To update the skill later, re-run this script."
echo "To remove: rm -rf $SKILL_DST"
