# Quickstart Guide

Get from `git clone` to working queries in one sitting.

---

## Before you start (about 10 minutes of setup)

Have these ready before step 1. Path A (Docker, recommended) needs only the first four.

| You need | Why | How to get it |
|---|---|---|
| **A Mac, Windows or Linux machine** with ~4 GB free RAM and ~3 GB free disk | Runs the Munkymind containers | Windows: use Docker Desktop with WSL 2 (Docker's installer sets it up) |
| **Docker Desktop** (Mac/Windows) or **Docker Engine + Compose v2** (Linux) | Runs Munkymind | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/). Check with `docker compose version` |
| **git** | Downloads the code | [git-scm.com/downloads](https://git-scm.com/downloads). Check with `git --version` |
| **One API key: OpenAI** | Turns your notes into searchable vectors and writes answers | [platform.openai.com/api-keys](https://platform.openai.com/api-keys). Add a few dollars of credit; typical personal use costs cents |
| A folder of **your notes** (markdown, text or PDF) | What Munkymind learns from | Start small, e.g. 20–50 files, and add more later |

**Optional, depending on how you want to use it:**

| If you want… | Also need |
|---|---|
| Claude to write the answers (instead of OpenAI) | An Anthropic API key: [console.anthropic.com](https://console.anthropic.com/) |
| Fully local, no API key | [Ollama](https://ollama.com/) with a chat model and `nomic-embed-text` pulled (slower; needs a decent machine) |
| To use it from **Claude Desktop** | [Claude Desktop](https://claude.ai/download) (step 6) |
| To use it from **claude.ai or ChatGPT** | `cloudflared` for a free tunnel, or a hosted deploy (step 7). Connectors need a public HTTPS address |
| Path B (local install, no Docker) | Python 3.11+ (with `pip`) |

Never paste your API keys into a chat or commit them; they go only in `.env` or the setup wizard.

---

## Path A: Docker Compose (recommended for production)

### 1. Clone and configure

```bash
git clone https://github.com/munkymind/munkymind.git
cd munkymind
cp .env.example .env
```

Edit `.env`:
```bash
OPENAI_API_KEY=sk-...         # One key is enough: OpenAI does embeddings and answers
ANTHROPIC_API_KEY=            # Optional: add it if you want Claude to write the answers
MM_USER_ID=yourname           # The username you'll create in the wizard (the MCP container serves it)
```

### 2. Start services

```bash
docker compose up -d
```

Wait ~30 seconds for startup. Check health:
```bash
curl http://localhost:8000/health
# → {"status": "ok"}
```

### 3. Add your notes

The API container reads notes from `./notes` (mounted read-only at `/notes`). Point `MM_NOTES_DIR` in `.env` at another folder if you prefer.

```bash
mkdir -p notes
cp -r ~/path/to/your/notes/* notes/
```

No notes handy, or just kicking the tyres? Set `MM_NOTES_DIR=./examples/sample-notes` in `.env` to use a small made-up library instead.

Domains (health, professional, strategic, projects, temporal, personal) are detected from file and folder names, so `notes/health/sleep.md` lands in *health*. Anything unmatched goes to *personal*.

**Cost and time:** indexing about 50 notes takes a minute or two and a few cents of OpenAI usage. After that, each question costs a fraction of a cent.

### 4. Run the setup wizard

```bash
docker compose exec api munkymind setup
```

Answer the prompts:

| Prompt | Answer |
|--------|--------|
| Username | anything, e.g. `myname` |
| LLM provider | `1` (anthropic) or `2` (openai) |
| API key prompts | press **Enter** — Docker already has the keys from `.env` |
| Where is your context? | `1` (local files) |
| Directory path | `/notes` |

The wizard creates your user, prints your API key (**save it — shown once**), ingests `/notes`, and runs a test query. You should see `🎉 Your context library is ready!`

> The wizard needs an interactive terminal. `docker compose exec` gives you one; don't add `-T`.
> `munkymind user create` only creates a user and key — it does **not** configure a connector, so `ingest` will say "No connector 'files' configured". Use `setup`.

### 5. Query your context

From the CLI:
```bash
docker compose exec api munkymind query --user myname "What should I focus on this week?"
```

Or over REST (note the header is `X-API-Key`, not `Authorization: Bearer`):
```bash
curl -X POST http://localhost:8000/query \
  -H "X-API-Key: mm_sk_ABC123..." \
  -H "Content-Type: application/json" \
  -d '{"query": "What should I focus on this week?"}'
```

Added or edited notes? Re-ingest (existing pages are updated, not duplicated):
```bash
docker compose exec api munkymind ingest --connector files --user myname
```

### See your brains (what's in your library)

Open **http://localhost:8000/ui** and paste your API key. Three tabs:

- **🧠 Brains**: your library by domain. Search titles and tags, open any page to read it, with where it came from and how fresh it is.
- **👀 Peek**: type a question and see exactly which snippets an AI tool would be sent, and how small that is next to your whole library. No AI is called.
- **😋 Moods**: how fed each brain is, freshness per domain (😋 Fed / 😐 Peckish / 😴 Starving) and the feeding log.

The page is read-only. Your key is kept in the browser tab (or on the device, if you tick "remember") and only sent to your own server.

The same from the terminal:
```bash
docker compose exec api munkymind status -u myname          # pages, freshness, recent ingestions
docker compose exec api munkymind pages -u myname --stale   # list pages (filter: --domain, --search, --stale)
docker compose exec api munkymind show <page-id> -u myname  # read one page
docker compose exec api munkymind peek -u myname "What should I focus on this week?"
```

### 6. Connect to Claude Desktop (MCP)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "munkymind": {
      "command": "docker",
      "args": ["compose", "-f", "/path/to/munkymind/docker-compose.yml",
               "exec", "-T", "mcp", "python", "-m", "mm.mcp.server"],
      "env": {
        "USER_ID": "myname"
      }
    }
  }
}
```

Set `MM_USER_ID=myname` in `.env` and run `docker compose up -d mcp` so the MCP container serves your user (it defaults to `default`).

Or for local install (Path B), use the simpler config from the README.

---

### 7. Connect claude.ai or ChatGPT (remote connector)

Claude Desktop (step 6) talks to Munkymind locally. **claude.ai and ChatGPT** connect over the internet, so they need a public HTTPS address for the MCP container (port 8001).

**Quick test (free, no account):** a Cloudflare quick tunnel.

```bash
# Install cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
cloudflared tunnel --url http://localhost:8001
# It prints a URL like https://quiet-river-1234.trycloudflare.com
```

1. In claude.ai (Settings → Connectors → Add custom connector) or ChatGPT (Settings → Connectors), enter **`https://<your-tunnel>.trycloudflare.com/mcp`**.
2. A Munkymind login page opens. Paste the `mm_sk_...` API key the setup wizard printed.
3. Ask: *"Use Munkymind: what am I working on?"*

The tunnel URL changes every time you restart it (re-add the connector), and it only works while your machine is on. For an always-on connector, deploy to Railway (`railway.toml` is included) or any HTTPS host. If your host rewrites the address, set `MCP_BASE_URL=https://your-host` in `.env` and run `docker compose up -d mcp`.

> **Make sure `MM_USER_ID` in `.env` matches the username you created in the wizard**, then `docker compose up -d mcp`. The MCP container serves that one user.

---

### Next: what to try

You're set up. **[What can I do with it?](using-munkymind.md)** walks you through your first 15 minutes, with use cases, example prompts and tester missions.

---

## Path B: Local Install (development / tinkering)

### 1. Clone and install

```bash
git clone https://github.com/munkymind/munkymind.git
cd munkymind
pip install -e ".[dev]"
```

### 2. Run the setup wizard

```bash
munkymind setup
```

The wizard walks you through:
1. Choose a username
2. Configure LLM provider + API key
3. Configure embedding provider + API key
4. Choose connectors (files, GitHub, or both)
5. Run first ingestion
6. Validate with eval suite

Target: **working context library in under 30 minutes.**

Keys you enter in the wizard are saved to `~/.munkymind/.env` and loaded automatically by the CLI and API server.

Query from the CLI straight away:
```bash
munkymind query --user myname "What should I focus on this week?"
```

### 3. Start the API server

```bash
export DATA_ROOT=~/.munkymind
uvicorn mm.api.server:app --host 0.0.0.0 --port 8000
```

### 4. Start the MCP server (separate terminal)

```bash
export DATA_ROOT=~/.munkymind
export USER_ID=myname
python -m mm.mcp.server  # stdio mode for Claude Desktop
```

---

## Running the Eval Suite

Check the quality of your context library:

```bash
munkymind eval --api-url http://localhost:8000 --api-key mm_sk_ABC123...
# Docker: docker compose exec api munkymind eval --api-key mm_sk_ABC123...
```

The key can also come from the `MM_API_KEY` environment variable. Cross-domain scenarios (S2, S8) need notes in at least two domains.

Output:
```
S1 Within-domain retrieval    PASS
S2 Cross-domain synthesis     PASS
S3 Temporal awareness         PASS
S4 Staleness detection        PASS
S5 Knowledge boundary         PASS
S6 Auth boundary              PASS
S7 Source provenance          PASS
S8 Cross-domain insight       PASS
S9 Connector ingestion        PASS

Score: 9/9 ✅
```

For CI / JSON output:
```bash
munkymind eval --api-url http://localhost:8000 --api-key mm_sk_... --output json
```

---

## Managing Domains

```bash
munkymind domain add finances "Finances" --user myname     # Add new domain
munkymind domain rename health "Wellbeing" --user myname   # Rename existing
munkymind domain remove projects --user myname             # Remove domain
```

---

## Troubleshooting

### Known limitations (v0.2.0)

- **One notes folder.** Docker mounts a single folder (`MM_NOTES_DIR`, default `./notes`) at `/notes`. To ingest several folders, put them under one parent folder and point `MM_NOTES_DIR` there, or use the GitHub connector for repos (no mount needed). Changing `MM_NOTES_DIR` needs `docker compose up -d`.
- **Unreadable files are skipped.** A corrupt PDF (or any file that can't be read) is skipped with a warning; the rest of the folder still ingests.
- **Remote connectors need a public URL.** See step 7 (tunnel or a hosted deploy).


**"Collection not found" on first query**
→ You haven't ingested any content yet. Run `munkymind ingest --connector files --user myname`.

**"No connector 'files' configured for user"**
→ The user was made with `user create`, which doesn't set up connectors. Run `munkymind setup` with the same username and choose to reconfigure.

**"Path does not exist" in Docker**
→ Inside the container your notes are at `/notes`, not your host path. Check `MM_NOTES_DIR` in `.env` and restart with `docker compose up -d`.

**Query returns 500**
→ Usually a missing or placeholder LLM key. Check `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` in `.env`, then `docker compose up -d` to reload.

**401 Unauthorized**
→ Check your API key and that you're sending it as `X-API-Key`. Keys are shown once at creation. Rotate with `munkymind user rotate-key myname`.

**Slow embeddings**
→ `text-embedding-3-small` is fast. If using Ollama, ensure the model is pulled: `ollama pull nomic-embed-text`.

**MCP server not connecting**
→ Ensure `USER_ID` and `DATA_ROOT` env vars are set correctly in your MCP config.
