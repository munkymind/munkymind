#!/usr/bin/env bash
# setup-mcp.sh — Munkymind MCP installer for Claude Desktop (Mac-first)
# Usage: bash setup-mcp.sh
# Detects Claude Desktop config, injects the Munkymind MCP server entry.

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
fail() { echo -e "${RED}✗${RESET} $*"; exit 1; }
hdr()  { echo -e "\n${BOLD}$*${RESET}"; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "┌─────────────────────────────────────────┐"
echo "│   Munkymind — Claude Desktop Setup    │"
echo "└─────────────────────────────────────────┘"
echo -e "${RESET}"

# ── Platform check ────────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  fail "This script is Mac-only for now. Windows/Linux setup is coming."
fi

# ── Detect Claude Desktop config path ────────────────────────────────────────
hdr "Step 1: Finding Claude Desktop config..."
CONFIG_DIR="$HOME/Library/Application Support/Claude"
CONFIG_FILE="$CONFIG_DIR/claude_desktop_config.json"

if [[ ! -d "$CONFIG_DIR" ]]; then
  fail "Claude Desktop config directory not found at:\n  $CONFIG_DIR\n\nIs Claude Desktop installed? Download from https://claude.ai/download"
fi
ok "Config directory found: $CONFIG_DIR"

# ── Check Docker is available ─────────────────────────────────────────────────
hdr "Step 2: Checking Docker..."
if ! command -v docker &>/dev/null; then
  fail "Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
fi
if ! docker compose version &>/dev/null; then
  fail "'docker compose' not available. Make sure Docker Desktop is running and up to date."
fi
ok "Docker and Docker Compose are available."

# ── Verify Docker is running ───────────────────────────────────────────────────
if ! docker info &>/dev/null; then
  fail "Docker daemon is not running. Please start Docker Desktop and try again."
fi
ok "Docker daemon is running."

# ── Prompt for project path ───────────────────────────────────────────────────
hdr "Step 3: Project configuration..."
DEFAULT_PATH="$(cd "$(dirname "$0")" && pwd)"
echo -e "  Where is your munkymind directory?"
echo -e "  Press Enter to use: ${BOLD}$DEFAULT_PATH${RESET}"
read -r -p "  Path: " PROJECT_PATH
PROJECT_PATH="${PROJECT_PATH:-$DEFAULT_PATH}"

# Expand ~ if present
PROJECT_PATH="${PROJECT_PATH/#\~/$HOME}"

if [[ ! -f "$PROJECT_PATH/docker-compose.yml" ]]; then
  fail "No docker-compose.yml found at: $PROJECT_PATH\nCheck that you're pointing to the munkymind root directory."
fi
ok "Project path: $PROJECT_PATH"

# ── Prompt for username ───────────────────────────────────────────────────────
DEFAULT_USER="$(whoami)"
echo -e "\n  What username did you create with 'munkymind user create'?"
echo -e "  Press Enter to use: ${BOLD}$DEFAULT_USER${RESET}"
read -r -p "  Username: " MM_USER
MM_USER="${MM_USER:-$DEFAULT_USER}"
ok "Username: $MM_USER"

# ── Run test-mcp.sh first to confirm stack is up ─────────────────────────────
hdr "Step 4: Checking MCP server is reachable..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_SCRIPT="$SCRIPT_DIR/test-mcp.sh"

if [[ -f "$TEST_SCRIPT" ]]; then
  if ! bash "$TEST_SCRIPT" --quiet 2>/dev/null; then
    warn "MCP server health check failed. Make sure the stack is running:"
    echo ""
    echo "    cd $PROJECT_PATH"
    echo "    docker compose up -d"
    echo ""
    read -r -p "  Continue anyway? (y/N): " CONTINUE
    [[ "${CONTINUE,,}" == "y" ]] || fail "Aborted. Start the stack and re-run setup-mcp.sh."
  else
    ok "MCP server is reachable."
  fi
else
  warn "test-mcp.sh not found — skipping pre-flight check."
fi

# ── Build the MCP config block ────────────────────────────────────────────────
hdr "Step 5: Injecting MCP config..."

MCP_ENTRY=$(cat <<EOF
{
  "command": "docker",
  "args": ["compose", "-f", "$PROJECT_PATH/docker-compose.yml", "exec", "-T", "mcp", "python", "-m", "mm.mcp.server"],
  "env": {
    "USER_ID": "$MM_USER"
  }
}
EOF
)

# Create config file if it doesn't exist
if [[ ! -f "$CONFIG_FILE" ]]; then
  warn "Config file not found — creating a new one."
  echo '{}' > "$CONFIG_FILE"
fi

# Backup existing config
BACKUP="$CONFIG_FILE.bak.$(date +%Y%m%d-%H%M%S)"
cp "$CONFIG_FILE" "$BACKUP"
ok "Backed up existing config to: $(basename "$BACKUP")"

# Merge using Python (ships with macOS, handles JSON safely)
python3 - "$CONFIG_FILE" "$MCP_ENTRY" <<'PYEOF'
import json, sys

config_path = sys.argv[1]
new_entry_str = sys.argv[2]

with open(config_path) as f:
    config = json.load(f)

new_entry = json.loads(new_entry_str)

if "mcpServers" not in config:
    config["mcpServers"] = {}

if "munkymind" in config["mcpServers"]:
    print("  Existing munkymind entry found — updating.")
else:
    print("  Adding new munkymind entry.")

config["mcpServers"]["munkymind"] = new_entry

with open(config_path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")

print("  Config written successfully.")
PYEOF

ok "MCP config injected into: $CONFIG_FILE"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}✓ Setup complete!${RESET}"
echo ""
echo -e "  ${BOLD}Next step (important):${RESET}"
echo "  Fully quit Claude Desktop and relaunch it."
echo "  'Close window' is not enough — use Cmd+Q or quit from the menu bar icon."
echo ""
echo "  Then ask Claude: \"What do you know about my work?\""
echo "  It should draw from your Munkymind library."
echo ""
echo -e "  ${YELLOW}Tip:${RESET} If Claude shows a connection error, check the stack is running:"
echo "    cd $PROJECT_PATH && docker compose ps"
echo ""
