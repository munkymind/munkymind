#!/usr/bin/env bash
# Munkymind setup: from a fresh clone to a running, indexed library in one command.
#
#   ./setup.sh
#
# Asks a few questions (API key, username, where your notes are), checks the key with one
# API call, picks free ports, writes .env, starts Docker, and runs the in-container setup
# with those answers so nothing is asked twice. Safe to re-run: current .env values are
# offered as defaults and an existing API key for your username is kept.
#
# Needs: Docker with Compose v2, curl, bash (macOS, Linux, or WSL / Git Bash on Windows).
set -euo pipefail

cd "$(dirname "$0")"
ENV_FILE=.env

say()  { printf '%s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*" >&2; }
die()  { printf '✗ %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "Docker isn't installed. Get Docker Desktop: https://www.docker.com/products/docker-desktop/"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing (check: docker compose version)."
command -v curl >/dev/null || die "curl is required."
docker info >/dev/null 2>&1 || die "Docker isn't running. Start Docker Desktop (or the docker service) and try again."

[ -f "$ENV_FILE" ] || cp .env.example "$ENV_FILE"

get_env() {  # current value of KEY in .env (without inline comments or quotes)
  local v
  v=$(grep -E "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)
  v=${v%%#*}; v=${v%"${v##*[![:space:]]}"}; v=${v#\"}; v=${v%\"}
  printf '%s' "$v"
}

set_env() {  # set KEY=VALUE in .env, replacing an existing line or appending
  local key=$1 val=$2 tmp
  tmp=$(mktemp)
  # Values go through the environment, not awk -v, so backslashes (e.g. Windows paths) survive
  K="$key" V="$val" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; done=0}
    index($0, k"=") == 1 {print k"="v; done=1; next} {print} END{if(!done) print k"="v}' \
    "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
}

ask() {  # ask "Question" default -> answer (default when Enter)
  local reply
  read -r -p "$1 [${2}]: " reply || true
  printf '%s' "${reply:-$2}"
}

ask_secret() {
  local reply
  read -r -s -p "$1: " reply || true
  printf '\n' >&2
  printf '%s' "$reply"
}

port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

free_port_from() {
  local p=$1
  while ! port_free "$p"; do p=$((p + 1)); done
  printf '%s' "$p"
}

check_openai_key() {  # 200 ok | 401 bad key | other = couldn't tell
  curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.openai.com/v1/models \
    -H "Authorization: Bearer $1" || true
}

check_anthropic_key() {
  curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.anthropic.com/v1/models \
    -H "x-api-key: $1" -H "anthropic-version: 2023-06-01" || true
}

real_key() { [ -n "$1" ] && [ "$1" != "sk-..." ]; }

say ""
say "🐒 Munkymind setup"
say ""

# ── 1. Who writes the answers ────────────────────────────────────────────────
say "Who should write the answers?"
say "  1. OpenAI   (one OpenAI key does everything)"
say "  2. Claude   (needs an Anthropic key as well as the OpenAI key used for indexing)"
current_llm=$(get_env MM_LLM_PROVIDER)
default_choice=1; [ "$current_llm" = "anthropic" ] && real_key "$(get_env ANTHROPIC_API_KEY)" && default_choice=2
choice=$(ask "Choose 1 or 2" "$default_choice")
[ "$choice" = "2" ] && llm=anthropic || llm=openai

# ── 2. OpenAI key (indexing always uses OpenAI embeddings here) ─────────────
existing=$(get_env OPENAI_API_KEY)
openai_key=""
if real_key "$existing"; then
  keep=$(ask "Use the OpenAI key already in .env (…${existing: -4})? y/n" "y")
  [ "$keep" = "y" ] && openai_key=$existing
fi
for attempt in 1 2 3; do
  [ -n "$openai_key" ] || openai_key=$(ask_secret "OpenAI API key (sk-…, input hidden)")
  code=$(check_openai_key "$openai_key")
  case $code in
    200) say "  ✓ OpenAI key works."; break ;;
    401) warn "OpenAI rejected that key (401). Check for a typo, or that it has credit."; openai_key="" ;;
    *)   warn "Couldn't check the OpenAI key (HTTP ${code:-none}; network?). Continuing with it."; break ;;
  esac
  [ "$attempt" = 3 ] && die "No working OpenAI key after 3 tries."
done
set_env OPENAI_API_KEY "$openai_key"

if [ "$llm" = "anthropic" ]; then
  anth=$(get_env ANTHROPIC_API_KEY)
  real_key "$anth" || anth=$(ask_secret "Anthropic API key (input hidden)")
  code=$(check_anthropic_key "$anth")
  case $code in
    200) say "  ✓ Anthropic key works." ;;
    401) die "Anthropic rejected that key (401)." ;;
    *)   warn "Couldn't check the Anthropic key (HTTP ${code:-none}). Continuing with it." ;;
  esac
  set_env ANTHROPIC_API_KEY "$anth"
fi
set_env MM_LLM_PROVIDER "$llm"
set_env MM_EMBED_PROVIDER openai

# ── 3. Username ──────────────────────────────────────────────────────────────
default_user=$(get_env MM_USER_ID)
if [ -z "$default_user" ] || [ "$default_user" = "default" ]; then
  default_user=$(printf '%s' "${USER:-me}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | sed 's/-*$//')
fi
username=$(ask "Username" "$default_user")
printf '%s' "$username" | grep -Eq '^[A-Za-z0-9-]+$' || die "Username can only use letters, numbers and hyphens."
set_env MM_USER_ID "$username"

# ── 4. Notes folder (mounted read-only; an Obsidian vault works in place) ───
default_notes=$(get_env MM_NOTES_DIR); default_notes=${default_notes:-./notes}
notes=$(ask "Where are your notes? (folder of .md/.txt/.pdf; an Obsidian vault works as is)" "$default_notes")
notes=${notes/#\~/$HOME}
if [ "$notes" = "./notes" ] && [ ! -d notes ]; then mkdir -p notes; fi
[ -d "$notes" ] || die "Not a folder: $notes"
notes=$(cd "$notes" && pwd)
count=$(find "$notes" -type f \( -name '*.md' -o -name '*.txt' -o -name '*.pdf' \) 2>/dev/null | wc -l | tr -d ' ')
say "  ✓ $notes ($count notes found; mounted read-only, never modified)"
[ "$count" -gt 0 ] || warn "No .md/.txt/.pdf files there yet. Add some, then run: docker compose exec api munkymind ingest -c files --user $username"
set_env MM_NOTES_DIR "$notes"

# ── 5. Ports ─────────────────────────────────────────────────────────────────
running=$(docker compose ps -q api 2>/dev/null || true)
api_port=$(get_env API_PORT); api_port=${api_port:-8000}
mcp_port=$(get_env MCP_PORT); mcp_port=${mcp_port:-8001}
if [ -z "$running" ]; then
  new_api=$(free_port_from "$api_port")
  [ "$new_api" = "$api_port" ] || say "  Port $api_port is in use by another app; using $new_api for the API."
  new_mcp=$(free_port_from "$mcp_port")
  [ "$new_mcp" = "$new_api" ] && new_mcp=$(free_port_from $((new_api + 1)))
  [ "$new_mcp" = "$mcp_port" ] || say "  Port $mcp_port is in use; using $new_mcp for MCP."
  api_port=$new_api; mcp_port=$new_mcp
fi
set_env API_PORT "$api_port"
set_env MCP_PORT "$mcp_port"

# ── 6. Start and wait for health ─────────────────────────────────────────────
say ""
say "Starting Munkymind (the first build takes a few minutes)…"
docker compose up -d --build
for _ in $(seq 1 90); do
  if curl -fs "http://127.0.0.1:$api_port/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "${ok:-}" != 1 ]; then
  docker compose ps
  docker compose logs --tail 30 api
  die "The API didn't become healthy on port $api_port. See the logs above."
fi
say "  ✓ API is up on port $api_port."

# ── 7. In-container setup with the answers above (nothing asked twice) ──────
say ""
tty_flag=(); [ -t 0 ] || tty_flag=(-T)   # no terminal (piped/CI): don't ask Docker for one
docker compose exec "${tty_flag[@]}" api munkymind setup --username "$username" --llm "$llm" --embed openai --notes /notes --yes
docker compose up -d mcp >/dev/null 2>&1 || true

say ""
say "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
say "  Library UI:    http://localhost:$api_port/ui   (sign in with the API key above, or the link below)"
say "  MCP endpoint:  http://localhost:$mcp_port/mcp  (Claude Desktop: see docs/quickstart.md, step 3)"
say "  Edited notes are re-indexed automatically every few minutes. To do it now:"
say "    docker compose exec api munkymind ingest -c files --user $username"
say "  Something not working later? Check everything with:"
say "    docker compose exec api munkymind doctor"
say "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
say ""
# A one-time sign-in link, so the key can go straight into a password manager
docker compose exec "${tty_flag[@]}" api munkymind login-link --user "$username" || true
