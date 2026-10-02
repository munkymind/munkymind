#!/usr/bin/env bash
# test-mcp.sh — Validate Munkymind MCP server is reachable before setup
# Usage: bash test-mcp.sh [--quiet]
# Exit 0 = healthy, Exit 1 = not reachable

set -euo pipefail

QUIET=false
[[ "${1:-}" == "--quiet" ]] && QUIET=true

ok()   { $QUIET || echo "✓ $*"; }
fail() { $QUIET || echo "✗ $*"; exit 1; }
info() { $QUIET || echo "  $*"; }

API_URL="${MM_API_URL:-http://localhost:8000}"
MCP_URL="${MM_MCP_URL:-http://localhost:8001}"

$QUIET || echo ""
$QUIET || echo "Munkymind — MCP pre-flight check"
$QUIET || echo "─────────────────────────────────"

# ── Check API health ──────────────────────────────────────────────────────────
info "Checking API at $API_URL/health ..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$API_URL/health" 2>/dev/null || echo "000")

if [[ "$HTTP_STATUS" != "200" ]]; then
  fail "API not reachable at $API_URL (HTTP $HTTP_STATUS). Is the stack running?
  
  Start it with:
    docker compose up -d
  
  Or check status with:
    docker compose ps"
fi

BODY=$(curl -s --max-time 5 "$API_URL/health" 2>/dev/null || echo "{}")
if echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
  ok "API is healthy ($API_URL)"
else
  fail "API responded but status is not 'ok'. Response: $BODY"
fi

# ── Check MCP port ────────────────────────────────────────────────────────────
info "Checking MCP server at $MCP_URL ..."
MCP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$MCP_URL/" 2>/dev/null || echo "000")

# MCP server may return 404 or 200 at root — either means it's up
if [[ "$MCP_STATUS" == "000" ]]; then
  fail "MCP server not reachable at $MCP_URL. 

  Check docker compose ps — the 'mcp' container should be running.
  Logs: docker compose logs mcp"
fi
ok "MCP server is reachable at $MCP_URL (HTTP $MCP_STATUS)"

# ── Summary ───────────────────────────────────────────────────────────────────
$QUIET || echo ""
$QUIET || echo "✓ All checks passed. Ready to run setup-mcp.sh"
$QUIET || echo ""
exit 0
