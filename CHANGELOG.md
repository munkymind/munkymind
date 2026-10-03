# Changelog

All notable changes to Monkey Mind will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/)

---

## [0.2.4] — 2026-10-03

Ready for testers.

### Added
- **[What can I do with it?](docs/using-munkymind.md)**: a 15-minute tour, use cases with example prompts, tester missions and quick fixes. Linked from the README, the quickstart, the setup wizard, the viewer and the website
- **Sample library** in `examples/sample-notes` (a made-up person across all six domains): set `MM_NOTES_DIR=./examples/sample-notes` to try Munkymind without your own notes
- Website: "Once it's running" (viewer, Peek, freshness) and use cases

### Fixed
- **Domains now follow folder names**, as the docs promised: `notes/strategic/plan.md` goes to *strategic* even without a keyword match. Keywords only look inside the notes folder, so a notes folder under e.g. `~/projects/` no longer puts everything in *projects*. If you ingested before, re-ingest to re-sort pages
- Page titles come from the note's first `# heading` (or `title:` front matter) instead of the file name
- The setup wizard's summary counted domains wrongly ("across 1 domain")
- MCP `get_page` returned empty content for uploaded files or moved sources; it now rebuilds the text from the library. It no longer exposes the local file path

## [0.2.3] — 2026-10-03

### Changed
- **Messages moved into `mm/voice/`.** `plain.json` (Apache 2.0) holds the plain-language fact or fix for every event; `catalogue.json` holds the jokes and labels and is licensed **CC BY-NC-ND 4.0** (see `mm/voice/LICENSE`). The CLI and `/ui` both read from here; API and MCP output stays plain
- **Brains have names:** built-in domains appear as their cast (Health → Body Brain, Professional → Business Brain, Personal → Personal Brain, Strategic → Big Picture Brain, Temporal → Calendar Brain, Projects → Builder Brain); other domains are "<Label> Brain". Name your own with `munkymind domain add ... --brain-name "Garden Brain"` (or `brain:` in config.yaml). Plain mode shows the plain domain label
- Freshness badges show the brain's mood (😋 Fed, 😐 Peckish, 😴 Starving) instead of bananas
- `MM_VOICE=plain` turns the jokes off everywhere. Munkymind also runs normally without `catalogue.json`

## [0.2.2] — 2026-10-03

See what's in your library.

### Added
- **Library viewer** at `http://localhost:8000/ui` (read-only, no external fonts or scripts):
  - **Brains**: your library by domain, with search, and a page view showing sections, source, confidence, size and freshness
  - **Peek**: type a question and see exactly which snippets an AI tool would be sent, plus the share of your library that represents. Retrieval only; no LLM call
  - **Pulse**: freshness per domain (😋 Fed / 😐 Peckish / 😴 Starving) and the feeding log
- CLI: `munkymind status`, `munkymind pages` (`--domain`, `--search`, `--stale`), `munkymind show <page>`, `munkymind peek "<question>"`
- API: `GET /status`, `POST /preview`, `GET /pages?q=&stale=`, `GET /pages/{id}?content=true`
- Each domain ("brain") has an icon: built-in defaults, an automatic pick for new domains, or your own with `munkymind domain add ... --icon 🌱` (or `icon:` in config.yaml)
- Every page now reports its freshness (`fresh` / `ripening` / `stale`) against its domain's staleness threshold

### Changed
- `GET /` redirects to `/ui`. Page responses no longer include the local file path (`raw_path`)

## [0.2.1] — 2026-10-02

Security hardening before the first tester round. See [SECURITY.md](SECURITY.md).

### Security
- **Local only by default:** Docker publishes the API and MCP ports on `127.0.0.1`, so other devices on your network can't reach them (`MM_BIND=0.0.0.0` to opt out)
- **Prompt-injection fencing:** retrieved notes are passed to the answer model inside `<context>` tags as data, with a rule never to follow instructions found in them; fence tags inside content are stripped. MCP tools label returned text as user content
- **Rate limits** on the connector login and token endpoints (10 attempts a minute per IP, `MM_AUTH_RATE_LIMIT`)
- **Supply chain:** the Docker image pins its base image by digest, pins `uv`, installs dependencies from `uv.lock` with hash checking, and ships no dev/test tools
- **Dependencies:** upgraded `pyjwt`, `urllib3`, `httpx2` and `oauthlib` to fix known vulnerabilities. New weekly **Dependency Audit** (`pip-audit`) and Dependabot. Four `chromadb` advisories that only affect Chroma's own HTTP server are documented as not applicable (Munkymind embeds Chroma; no fixed release yet)
- **SECURITY.md:** exactly what leaves your machine, the threat model, and private vulnerability reporting

### Changed
- Honest privacy wording: your library lives on your machine; note text goes only to the AI provider you choose (or nowhere, with Ollama)
- Website: a "How it works" diagram

## [0.2.0] — 2026-10

**Monkey Mind is now Munkymind.** New home: [munkymind.dev](https://munkymind.dev) and `github.com/munkymind/munkymind` (old links redirect). The CLI is `munkymind`; `monkey-mind` still works as an alias, and an existing `~/.monkey-mind` data folder is picked up automatically.

### Added
- Remote MCP connectors for **claude.ai** and **ChatGPT**: OAuth 2.1 Authorization Code + PKCE (S256) with Dynamic Client Registration (#32)
- MCP HTTP transport auth (`X-API-Key`), `/sse` endpoint and OAuth discovery for hosted deployments
- Railway deployment (`railway.toml`, startup scripts) plus `/bootstrap` and `/ingest/files` API endpoints
- macOS Claude Desktop installer (`setup-mcp.sh`) and stack check (`test-mcp.sh`)
- Integration test gate in CI (full Docker stack) and smoke test workflow
- Logo and README rewrite
- Docs: connecting claude.ai / ChatGPT to a self-hosted install via a free Cloudflare quick tunnel; known limitations (one notes folder, skipped files)
- `monkey-mind query --user <name> "<question>"` — query from the CLI (was a stub)
- Docker Compose mounts your notes folder (`MM_NOTES_DIR`, default `./notes`) at `/notes`
- CLI and API server load keys the wizard saved to `<DATA_ROOT>/.env`

### Fixed
- **Remote connectors:** OAuth discovery now advertises the authorization-code login (`authorization_endpoint`, `registration_endpoint`, PKCE) and serves `/.well-known/oauth-protected-resource`, so claude.ai and ChatGPT can start the connector login. URLs follow the address the server was reached on (tunnel or proxy), or `MCP_BASE_URL`
- **One API key is enough:** the setup wizard defaults the answer model to the provider you have a key for (OpenAI-only works end to end); docs and `.env.example` no longer say both keys are required
- **A corrupt or unreadable file no longer stops ingestion:** it is skipped with a warning and the rest of the folder ingests
- `MCP_BASE_URL` is passed through Docker Compose, and an empty value counts as unset
- `monkey-mind ingest` now embeds and stores pages; previously it read files, reported success, and wrote nothing
- `monkey-mind eval` runs the suite (`--api-url`, `--api-key` / `MM_API_KEY`, `--output`); previously always "not available"
- Query sources cite the actual file and ingest time instead of just `files`, so provenance (S7) and staleness warnings work
- Setup wizard: actually configures connectors, shows the API key, respects `DATA_ROOT`
- Chunker: max chunk size enforced, at least one chunk per page
- Files connector skips unreadable directories instead of crashing
- Docker build (source copied before install)
- Integration CI gate headers (#33)
- Quickstart: use the `setup` wizard (`user create` doesn't configure a connector), `X-API-Key` header (not `Bearer`), `/notes` path in Docker, `MM_USER_ID` for the MCP container

## [0.1.0] — 2026-08-19

### Added
- Core package structure (`mm/`) with Apache 2.0 license
- Two-tier embedding pipeline (summary + detail chunks), model-agnostic via `EmbeddingProvider`
- Multi-tenant user store (per-user SQLite + ChromaDB isolation)
- Connector framework (`BaseConnector`) with file and GitHub connectors
- REST API (FastAPI) with X-API-Key auth, `/query`, `/domains`, `/pages`, `/health`, OpenAPI spec
- MCP server (stdio + HTTP transport) exposing 5 tools for Claude Desktop / Cursor
- CLI: `monkey-mind setup` wizard, `ingest`, `domain`, `user`, `eval` subcommands
- Parameterized 9-scenario eval suite (S1–S9)
- Docker Compose deployment (api + mcp services)
- GitHub Actions CI (tests on PR, eval on main, release on tag)
