#!/bin/bash
# Install overdrive-intel — CLI + MCP server (user-scoped)
# Usage: curl -fsSL https://raw.githubusercontent.com/Looney-tic/intel-overdrive/main/install.sh | bash

set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
BINARY_URL="https://raw.githubusercontent.com/Looney-tic/intel-overdrive/main/bin/overdrive-intel"
CONFIG_DIR="${HOME}/.config/overdrive-intel"

# ---------------------------------------------------------------------------
# 1. Install CLI binary
# ---------------------------------------------------------------------------

mkdir -p "$INSTALL_DIR"

echo "Downloading overdrive-intel CLI..."
curl -fsSL "$BINARY_URL" -o "$INSTALL_DIR/overdrive-intel"
chmod +x "$INSTALL_DIR/overdrive-intel"
echo "  Installed CLI to $INSTALL_DIR/overdrive-intel"

# ---------------------------------------------------------------------------
# 2. API key setup
# ---------------------------------------------------------------------------

mkdir -p "$CONFIG_DIR"

API_KEY="${OVERDRIVE_API_KEY:-}"

# Check existing key file
if [ -z "$API_KEY" ] && [ -f "$CONFIG_DIR/key" ]; then
  API_KEY=$(cat "$CONFIG_DIR/key" 2>/dev/null || true)
fi

if [ -z "$API_KEY" ]; then
  echo ""
  echo "Enter your API key (get one at https://inteloverdrive.com/keys):"
  printf "  dti_v1_"
  read -r KEY_SUFFIX
  if [ -n "$KEY_SUFFIX" ]; then
    API_KEY="dti_v1_${KEY_SUFFIX}"
    echo "$API_KEY" > "$CONFIG_DIR/key"
    chmod 600 "$CONFIG_DIR/key"
    echo "  Saved to $CONFIG_DIR/key"
  else
    echo "  Skipped — set later with: echo 'dti_v1_...' > $CONFIG_DIR/key"
  fi
else
  echo "  Using existing API key"
fi

# ---------------------------------------------------------------------------
# 3. Register MCP server at USER scope (available in all projects)
# ---------------------------------------------------------------------------

if command -v claude >/dev/null 2>&1; then
  echo ""
  echo "Registering MCP server (user scope — available in all projects)..."

  # Remove existing entry if present (avoid duplicates)
  claude mcp remove overdrive-intel -s user 2>/dev/null || true

  if [ -n "$API_KEY" ]; then
    claude mcp add -t stdio -s user \
      -e OVERDRIVE_API_URL=https://inteloverdrive.com \
      -e OVERDRIVE_API_KEY="$API_KEY" \
      overdrive-intel -- npx -y overdrive-intel-mcp@latest
  else
    claude mcp add -t stdio -s user \
      -e OVERDRIVE_API_URL=https://inteloverdrive.com \
      overdrive-intel -- npx -y overdrive-intel-mcp@latest
  fi

  echo "  MCP server registered at user scope"
else
  echo ""
  echo "Claude Code CLI not found — skipping MCP registration."
  echo "Install Claude Code, then run:"
  echo "  claude mcp add -t stdio -s user \\"
  echo "    -e OVERDRIVE_API_URL=https://inteloverdrive.com \\"
  echo "    -e OVERDRIVE_API_KEY=dti_v1_YOUR_KEY \\"
  echo "    overdrive-intel -- npx -y overdrive-intel-mcp@latest"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "Setup complete!"
echo ""

# PATH reminder if needed
case ":$PATH:" in
  *":$INSTALL_DIR:"*) ;;
  *)
    echo "Add to PATH (add to your shell profile):"
    echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
    echo ""
    ;;
esac

echo "Try it:"
echo "  overdrive-intel feed --type tool --limit 5"
echo ""
echo "In Claude Code, the overdrive_intel MCP tool is now available in all projects."
