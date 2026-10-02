<!-- OpenGraph / social card meta for GitHub link previews -->
<!--
  og:title: Munkymind — Your AI tools forget you. This fixes that.
  og:description: Open-source, self-hosted personal context library. Give Claude, Cursor, and ChatGPT a persistent memory of who you are, what you've built, and what matters to you.
  og:image: https://raw.githubusercontent.com/munkymind/munkymind/main/assets/logo-512.png
  og:url: https://github.com/munkymind/munkymind
  twitter:card: summary_large_image
-->

<p align="center">
  <img src="assets/logo-256.png" alt="Munkymind" width="120" height="120"/>
</p>

<h1 align="center">Munkymind</h1>

<p align="center">
  <strong>Your AI tools forget you. This fixes that.</strong>
</p>

<p align="center">
  <a href="https://github.com/munkymind/munkymind/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-7c3aed?style=flat-square" alt="License"/></a>
  <a href="https://github.com/munkymind/munkymind/actions"><img src="https://img.shields.io/github/actions/workflow/status/munkymind/munkymind/ci.yml?style=flat-square&color=7c3aed" alt="CI"/></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-7c3aed?style=flat-square" alt="Python 3.11+"/>
  <img src="https://img.shields.io/badge/self--hosted-yes-7c3aed?style=flat-square" alt="Self-hosted"/>
</p>

---

Every conversation, Claude forgets you. Every new Cursor session, you re-explain the project. Every ChatGPT window, you paste the same stale context blob and hope for the best.

**Munkymind is a persistent, structured memory for your AI tools** — self-hosted, open source, works with any LLM. It ingests your notes, GitHub repos, and documents, then serves that context to Claude, Cursor, or any MCP-compatible tool. Cross-domain synthesis. Source provenance. Staleness detection. No cloud dependency. Your data never leaves your machine.

```bash
# What your AI can answer once Munkymind is running:
"What should I focus on this week?"
"What are my most active projects right now?"
"What did I decide about the authentication approach?"
"Am I on track with my health goals?"
```

Cross-domain answers. Traced to sources. No hallucination.

---

**Stay in the loop:** optional release emails at [munkymind.dev](https://munkymind.dev/#updates). Not needed to use Munkymind.

## Quickstart

**Prerequisites:** Docker + Docker Compose and **one API key**: an OpenAI key covers both embeddings and answers. (Prefer Claude for answers? Add an Anthropic key too. Want it fully local? Use Ollama and no key.) About 10–15 minutes. Full checklist: [Before you start](docs/quickstart.md#before-you-start-about-10-minutes-of-setup).

```bash
# 1. Clone and configure
git clone https://github.com/munkymind/munkymind.git
cd munkymind
cp .env.example .env
# Edit .env — add OPENAI_API_KEY (and ANTHROPIC_API_KEY only if you want Claude for answers)

# 2. Put some notes in ./notes (markdown, text or PDF)
mkdir -p notes && cp -r ~/path/to/your/notes/* notes/

# 3. Start
docker compose up -d

# 4. Run the setup wizard (creates your user, connects /notes, ingests, test-queries)
docker compose exec api munkymind setup
#    - username: pick one
#    - API key prompts: press Enter (Docker already has them from .env)
#    - "Where is your context?": 1 (local files), path: /notes
#    - SAVE the mm_sk_... API key it prints — it is shown once

# 5. Ask a question
docker compose exec api munkymind query --user yourname "What should I focus on this week?"

# ...or over the REST API
curl -X POST http://localhost:8000/query \
  -H "X-API-Key: mm_sk_YOUR_KEY_HERE" \
  -H "Content-Type: application/json" \
  -d '{"query": "What should I focus on this week?"}'
```

Added more notes later? `docker compose exec api munkymind ingest --connector files --user yourname`

That's it. Full setup guide: **[docs/quickstart.md](docs/quickstart.md)**

---

## Why it works

| The problem | What Munkymind does |
|------------|----------------------|
| AI tools forget you every conversation | Persists your context across all tools, forever |
| Context scattered across notes, repos, files | One structured library, multiple sources |
| You paste the same stale blob every time | Staleness detection flags outdated content automatically |
| AI hallucinates answers about you | Every response traced to a source document |
| Locked to one AI provider | Works with OpenAI, Anthropic, Ollama — swap any time |
| Your data in someone else's cloud | Fully self-hosted. Your machine, your data. |

**In one line:** Google Personal Intelligence, but open source, self-hosted, and it actually works today.

---

## Features

- **Domain-structured context** — 6 life domains out of the box (health, professional, personal, strategic, temporal, projects). Add your own.
- **Two-tier retrieval** — summary + detail embeddings for fast, precise answers
- **Cross-domain synthesis** — a single query draws from health, work, and calendar simultaneously
- **Provenance tracking** — every fact points back to the source file
- **Staleness detection** — configurable per domain; stale sources flagged in responses
- **Knowledge boundary** — says "I don't know" instead of inventing an answer
- **MCP server** — connects to Claude Desktop, Cursor, and any MCP-compatible tool
- **REST API** — documented, authenticated, OpenAPI spec at `/docs`
- **Pluggable connectors** — `files` and `github` built-in; build your own
- **Self-hosted** — your data never leaves your machine
- **Model-agnostic** — bring your own keys for OpenAI, Anthropic, or Ollama

---

## Connectors

| Connector | Status | What it ingests |
|-----------|--------|----------------|
| **files** | ✅ Built-in | Markdown, text, PDF from any local directory |
| **github** | ✅ Built-in | Profile, repos, READMEs, contribution patterns |
| Obsidian | 🔜 Phase 2 | Vault notes and links |
| Gmail | 🔜 Phase 2 | Email threads (OAuth) |
| Community | 🤝 Build one | See [connector dev guide](docs/connector-dev-guide.md) |

---

## MCP integration (Claude Desktop / Cursor)

### Quickest path — run the installer (macOS)

```bash
# 1. Make sure the stack is running
docker compose up -d

# 2. Run the installer
bash setup-mcp.sh
```

The installer detects your Claude Desktop config, injects the MCP server entry, and tells you exactly what to do next.

**Then fully quit and relaunch Claude Desktop** — use Cmd+Q or quit from the menu bar icon. Closing the window is not enough. Claude Desktop only loads MCP servers on a full restart.

To verify the stack is reachable before running the installer:

```bash
bash test-mcp.sh
```

---

### Manual config (if you prefer)

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "munkymind": {
      "command": "docker",
      "args": ["compose", "-f", "/path/to/munkymind/docker-compose.yml",
               "exec", "-T", "mcp", "python", "-m", "mm.mcp.server"],
      "env": {
        "USER_ID": "yourname"
      }
    }
  }
}
```

With Docker, also set `MM_USER_ID=yourname` in `.env` and run `docker compose up -d mcp` so the MCP container serves your user.

> **npx / PATH gotcha:** Claude Desktop launches with a minimal PATH — it may not find `docker` even if your terminal can. If you get a connection error, use the full path to docker: run `which docker` in your terminal and paste that path into the `"command"` field (e.g. `"/usr/local/bin/docker"`).

Then ask Claude: *"What should I focus on this week?"* — and it will draw from your health, work, and strategic domains simultaneously.

---

### Remote connectors (Claude web/mobile, ChatGPT) — OAuth

For hosted deployments (e.g. Railway), Munkymind's MCP server supports OAuth 2.1 Authorization Code + PKCE (S256) with Dynamic Client Registration (RFC 7591), so it works as a standard remote connector for **claude.ai** and **ChatGPT** — no manual config file editing needed.

claude.ai and ChatGPT can only reach a **public HTTPS** address, so a laptop install needs one first:

- **Quickest (free, no account):** a Cloudflare quick tunnel to the MCP container.
  ```bash
  # install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
  cloudflared tunnel --url http://localhost:8001
  # → prints https://<random-words>.trycloudflare.com  — your connector URL is that + /mcp
  ```
  The URL changes each time you restart the tunnel (re-add the connector), and it only works while your machine and the tunnel are running.
- **Always on:** deploy to Railway (`railway.toml` is included) or any host with HTTPS.

Then:

1. In Claude or ChatGPT's connector settings, add a custom connector pointing at your MCP URL (e.g. `https://<random-words>.trycloudflare.com/mcp` or `https://your-mm-instance.up.railway.app/mcp`).
2. The platform auto-discovers OAuth endpoints via `/.well-known/oauth-authorization-server` and registers itself via `/register`.
3. You'll be redirected to a login page — enter your `mm_sk_` API key once to authorize the connection.
4. The platform stores a session token; no key re-entry needed until you revoke access.

Backward-compatible: the legacy OAuth Client Credentials grant and direct `X-API-Key` header still work for existing integrations. stdio transport (local Claude Desktop config above) is completely unaffected — it never touches the OAuth layer.

---

## CLI reference

```bash
munkymind setup                                   # Interactive setup wizard (start here)
munkymind query --user <name> "<question>"        # Ask your context library
munkymind ingest --connector files --user <name>  # Re-ingest after adding notes
munkymind ingest --connector github --user <name> # Ingest from GitHub connector
munkymind eval --api-key mm_sk_...                # Run quality eval suite (9 scenarios)
munkymind user create <name>                      # Create a user + API key only (no connector)
munkymind user rotate-key <name>                  # Issue a new API key
munkymind domain add <id> <label> --user <name>   # Add a domain
munkymind domain rename <id> <label> --user <name>
munkymind domain remove <id> --user <name>
munkymind user delete <name> --confirm            # Delete all user data
```

---

## Architecture

```
Source (files / GitHub)
  → Connector → ConnectorPage
  → Two-tier embedding (summary + detail)
  → ChromaDB (per-user collection)
  → REST API / MCP server
  → Claude, Cursor, ChatGPT, or any tool
```

Full technical design: **[docs/architecture.md](docs/architecture.md)**

---

## Contributing

Apache 2.0. Contributions welcome.

**Best first contribution:** Build a connector. The interface is clean and documented — 200 lines, one class to implement.

```bash
git clone https://github.com/munkymind/munkymind.git
cd munkymind
pip install -e ".[dev]"
python -m pytest tests/ -v
```

See [docs/connector-dev-guide.md](docs/connector-dev-guide.md) to get started.

---

## License

[Apache 2.0](LICENSE)

---

<p align="center">
  <img src="assets/logo-128.png" alt="Munkymind" width="48" height="48"/>
  <br/>
  <sub>Context is the moat.</sub>
</p>
