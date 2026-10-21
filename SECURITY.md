# Security

Munkymind runs on your own machine and holds your personal notes, so here is exactly what it does with them, what it protects against, and how to report a problem.

## What leaves your machine

| Data | Where it goes | When |
|---|---|---|
| Text of your notes (in chunks) | **Your embedding provider** (OpenAI by default) | When you ingest, to turn text into search vectors |
| The most relevant chunks for a question, plus the question | **Your answer provider** (OpenAI or Anthropic) | Each time you or a connected tool asks a question |
| Nothing | — | If you use **Ollama** for both embeddings and answers |

- Calls go straight from your machine to the provider, **under your own API key**. There is no Munkymind server in the middle and no telemetry.
- Your library (the vector index, page store and config) lives in a Docker volume on your machine.
- If you connect **claude.ai or ChatGPT**, the answers Munkymind returns go to that service too, as with anything you paste into them.

## What is protected

- **Local only by default.** Docker publishes the API (8000) and MCP (8001) ports on `127.0.0.1`, so other devices on your network can't reach them. `MM_BIND=0.0.0.0` changes that; it isn't recommended.
- **Key required.** The REST API and MCP server require your `mm_sk_…` key (stored only as a bcrypt hash; can be read-only and can expire, see [Authentication](#authentication)). Connector login uses OAuth 2.1 with PKCE and only redirects to known claude.ai / ChatGPT callbacks. Login and token endpoints are rate limited (10 attempts a minute per IP, `MM_AUTH_RATE_LIMIT`).
- **Notes are read-only.** Your notes folder is mounted read-only into the container.
- **Least privilege.** The app runs as an unprivileged user (uid 10001), never root, with every Linux capability dropped and `no-new-privileges` set. Compilers are used only in a separate build stage and are not in the image. A one-shot `volume-init` service is the only part that starts as root: it hands the data volume to the app user (needed once when upgrading from a release that ran as root), then drops to that user with no capabilities.
- **Supply chain.** The Docker image uses a digest-pinned base image and installs dependencies from `uv.lock` with hash checking (`--require-hashes`), with no dev or test tools. CI runs the test suite, a dependency audit (`pip-audit`), a scan of the built image for known vulnerabilities (Trivy; fixable HIGH/CRITICAL findings fail the build, and it re-runs weekly), a secrets scan and a personal-content leak scan; Dependabot proposes dependency and base-image updates.
- **Corrupt files** are skipped with a warning instead of stopping ingestion.

## Authentication

Munkymind has two ways in, both tied to your `mm_sk_…` key.

| Client | How it authenticates |
|---|---|
| **MCP connectors** (claude.ai, ChatGPT) | **OAuth 2.1** with PKCE. You enter your key once on the login page; the connector then sends it as its bearer token. Redirects only go to known connector callbacks |
| **REST API** (scripts, agents, the viewer) | The key in the `X-API-Key` header |
| **MCP over stdio** (local desktop apps) | No network auth: it runs as a local process with your user's data |

**Keys.** Each user has one key: `mm_sk_` plus 32 random bytes. Only a bcrypt hash is stored (`users/<name>/api_key.hash`); the key itself is printed once, when it is issued, and never logged. A key only reaches its owner's library.

**Scopes.** A key is either:

- `read_write` (the default): query, browse and ingest.
- `read` (`--read-only`): query and browse only. `GET` routes, `POST /query` and `POST /preview` work; anything that can change the library (such as `POST /ingest/files`) returns **403**. The check is central: every non-GET route is treated as a write unless it is explicitly listed as read-only, and a test fails if a new route skips it.

The MCP tools only read, so both scopes can use them.

**Expiry.** A key can expire (`--expires-in 30d`, `12h`, `8w`, or `--expires-at 2027-01-31`, meaning 00:00 UTC that day). An expired key gets **401** on the API and is refused by the connector login and on every MCP request, so a connector stops working when its key expires.

**Rotation** is one command: `munkymind user rotate-key <name>`. It issues a new key with the same scope and lifetime (a 30-day key gets a fresh 30 days), prints it once, and the old key stops working immediately. Pass `--read-only`/`--read-write`, `--expires-in`/`--expires-at` or `--no-expiry` to change the policy. Connectors that used the old key need to log in again.

```bash
munkymind user create my-agent --read-only --expires-in 30d   # recommended for agents that only query
munkymind user rotate-key my-agent                            # rotate (same policy)
munkymind user keys                                           # scope, created, expiry; never the key
munkymind user revoke-key my-agent                            # revoke without a replacement
```

**Recommendation:** give an agent or script that only needs to ask questions its own **read-only key with an expiry**, and rotate it on a schedule. Keep a read-write key for ingesting.

**Upgrading.** Keys issued before scopes existed have no policy file and keep working exactly as before: read-write, no expiry. Rotate them to add a scope or expiry. A key set with `MM_OSS_API_KEY` is read-write with no expiry. If a key's policy file is unreadable, the key is refused rather than given full access.

## Things to know (the threat model)

- **Prompt injection through your content.** Whatever you ingest is shown to an AI model. A document or third-party GitHub repo can contain hidden instructions ("ignore previous instructions…"). Munkymind fences retrieved text as data and tells its own answer model never to follow instructions inside it, and the MCP tools label returned text as user content. **No model is fully immune, so only ingest sources you trust**, and be careful ingesting repositories you don't control.
- **Exposing it to the internet.** A Cloudflare tunnel or hosted deploy makes the MCP login page public. It still needs your key, but stop the tunnel when you're not using it, keep your key private, and rotate it (`munkymind user rotate-key`) if it may have leaked. Use a read-only key with an expiry wherever you can.
- **Secrets in your notes.** Anything in your notes, including passwords or keys you've written down, is sent to your providers when ingested. Keep secrets out of the notes folder.
- **Your API keys** live in `.env` (and `<DATA_ROOT>/.env` if you used the wizard). Don't commit `.env`; it's in `.gitignore`.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting: **Security → Report a vulnerability** on [munkymind/munkymind](https://github.com/munkymind/munkymind/security/advisories/new). We'll acknowledge within a few days and credit you in the release notes if you'd like.
