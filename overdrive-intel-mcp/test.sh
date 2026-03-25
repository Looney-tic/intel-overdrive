#!/usr/bin/env bash
# Test script for overdrive-intel-mcp package
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

echo "=== overdrive-intel-mcp tests ==="

# 1. Build
echo ""
echo "--- Build ---"
if npm run build --silent 2>&1; then
  pass "TypeScript compiles"
else
  fail "TypeScript compile error"
fi

# 2. Shebang
echo ""
echo "--- Shebang ---"
FIRST_LINE=$(head -1 dist/index.js)
if [ "$FIRST_LINE" = "#!/usr/bin/env node" ]; then
  pass "Shebang present"
else
  fail "Shebang missing (got: $FIRST_LINE)"
fi

# 3. dist/index.js non-empty
echo ""
echo "--- Output size ---"
SIZE=$(wc -c < dist/index.js | tr -d ' ')
if [ "$SIZE" -gt 0 ]; then
  pass "dist/index.js is ${SIZE} bytes"
else
  fail "dist/index.js is empty"
fi

# 4. npm pack includes only dist files
echo ""
echo "--- npm pack --dry-run ---"
PACK_OUTPUT=$(npm pack --dry-run 2>&1)
if echo "$PACK_OUTPUT" | grep -q "dist/index.js"; then
  pass "npm pack includes dist/index.js"
else
  fail "npm pack missing dist/index.js"
fi

# Check no src files leaked into pack
if echo "$PACK_OUTPUT" | grep -q "src/index.ts"; then
  fail "npm pack leaks src/index.ts"
else
  pass "npm pack excludes source files"
fi

# 5. Single unified tool present in compiled output
echo ""
echo "--- Tool registration ---"
if grep -q "\"overdrive_intel\"" dist/index.js; then
  pass "Tool overdrive_intel registered"
else
  fail "Tool overdrive_intel missing"
fi

# 6. Verify bin field
echo ""
echo "--- Package config ---"
BIN=$(node -e "console.log(require('./package.json').bin['overdrive-intel-mcp'])")
if [ "$BIN" = "./dist/index.js" ]; then
  pass "bin field correct"
else
  fail "bin field wrong (got: $BIN)"
fi

TYPE=$(node -e "console.log(require('./package.json').type)")
if [ "$TYPE" = "module" ]; then
  pass "type: module"
else
  fail "type field wrong (got: $TYPE)"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
