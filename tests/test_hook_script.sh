#!/bin/bash
# Tests for the overdrive-intel Claude Code SessionStart hook script.
#
# Run: bash tests/test_hook_script.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK_SCRIPT="$PROJECT_ROOT/scripts/overdrive-intel-hook.sh"

PASS=0
FAIL=0

pass() {
  echo "  PASS: $1"
  PASS=$((PASS + 1))
}

fail() {
  echo "  FAIL: $1"
  FAIL=$((FAIL + 1))
}

# --------------------------------------------------------------------------
# Test 1: Valid bash syntax
# --------------------------------------------------------------------------
test_hook_valid_bash_syntax() {
  if bash -n "$HOOK_SCRIPT" 2>/dev/null; then
    pass "hook script has valid bash syntax"
  else
    fail "hook script has invalid bash syntax"
  fi
}

# --------------------------------------------------------------------------
# Test 2: Exits 0 when overdrive-intel not installed
# --------------------------------------------------------------------------
test_hook_exits_0_when_overdrive_not_installed() {
  # Run hook with a PATH that doesn't include overdrive-intel
  OUTPUT=$(echo '{"cwd":"/tmp"}' | PATH="/usr/bin:/bin" bash "$HOOK_SCRIPT" 2>/dev/null || true)
  EXIT_CODE=$?

  if [ -z "$OUTPUT" ]; then
    pass "hook exits silently when overdrive-intel not on PATH"
  else
    fail "hook produced output when overdrive-intel not on PATH: $OUTPUT"
  fi
}

# --------------------------------------------------------------------------
# Test 3: Exits 0 for non-AI project (no indicators)
# --------------------------------------------------------------------------
test_hook_exits_0_for_non_ai_project() {
  TMPDIR=$(mktemp -d)
  trap "rm -rf $TMPDIR" RETURN

  # Create a non-AI project (no AI/MCP mentions)
  echo "# Simple Python Project" > "$TMPDIR/README.md"
  echo '{"name": "myapp"}' > "$TMPDIR/package.json"

  # Create a fake overdrive-intel command so the first check passes
  FAKE_BIN=$(mktemp -d)
  cat > "$FAKE_BIN/overdrive-intel" << 'FAKEEOF'
#!/bin/bash
echo "fake"
FAKEEOF
  chmod +x "$FAKE_BIN/overdrive-intel"

  OUTPUT=$(echo "{\"cwd\":\"$TMPDIR\"}" | PATH="$FAKE_BIN:/usr/bin:/bin" bash "$HOOK_SCRIPT" 2>/dev/null || true)

  rm -rf "$FAKE_BIN"

  if echo "$OUTPUT" | grep -q "Intel Overdrive"; then
    fail "hook injected context for non-AI project"
  else
    pass "hook is silent for non-AI project"
  fi
}

# --------------------------------------------------------------------------
# Test 4: Outputs context for AI/MCP project
# --------------------------------------------------------------------------
test_hook_outputs_context_for_ai_project() {
  TMPDIR=$(mktemp -d)
  trap "rm -rf $TMPDIR" RETURN

  # Create an AI project with MCP indicators
  echo "# MCP Server for Claude" > "$TMPDIR/README.md"
  echo '{"name": "mcp-server"}' > "$TMPDIR/package.json"

  # Create a fake overdrive-intel command
  FAKE_BIN=$(mktemp -d)
  cat > "$FAKE_BIN/overdrive-intel" << 'FAKEEOF'
#!/bin/bash
echo "fake"
FAKEEOF
  chmod +x "$FAKE_BIN/overdrive-intel"

  OUTPUT=$(echo "{\"cwd\":\"$TMPDIR\"}" | PATH="$FAKE_BIN:/usr/bin:/bin" bash "$HOOK_SCRIPT" 2>/dev/null || true)

  rm -rf "$FAKE_BIN"

  if echo "$OUTPUT" | grep -q "Intel Overdrive Available"; then
    pass "hook outputs context for AI/MCP project"
  else
    fail "hook did not output expected context for AI/MCP project. Output: $OUTPUT"
  fi
}

# --------------------------------------------------------------------------
# Run all tests
# --------------------------------------------------------------------------
echo "=== Hook Script Tests ==="
test_hook_valid_bash_syntax
test_hook_exits_0_when_overdrive_not_installed
test_hook_exits_0_for_non_ai_project
test_hook_outputs_context_for_ai_project

echo ""
echo "Results: $PASS passed, $FAIL failed"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
