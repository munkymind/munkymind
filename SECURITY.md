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
- **Key required.** The REST API and MCP server require your `mm_sk_…` key (stored only as a bcrypt hash). Connector login uses OAuth 2.1 with PKCE and only redirects to known claude.ai / ChatGPT callbacks. Login and token endpoints are rate limited (10 attempts a minute per IP, `MM_AUTH_RATE_LIMIT`).
- **Notes are read-only.** Your notes folder is mounted read-only into the container.
- **Supply chain.** The Docker image uses a digest-pinned base image and installs dependencies from `uv.lock` with hash checking (`--require-hashes`), with no dev or test tools. CI runs the test suite, a dependency audit (`pip-audit`), a secrets scan and a personal-content leak scan; Dependabot proposes dependency and base-image updates.
- **Corrupt files** are skipped with a warning instead of stopping ingestion.

## Things to know (the threat model)

- **Prompt injection through your content.** Whatever you ingest is shown to an AI model. A document or third-party GitHub repo can contain hidden instructions ("ignore previous instructions…"). Munkymind fences retrieved text as data and tells its own answer model never to follow instructions inside it, and the MCP tools label returned text as user content. **No model is fully immune, so only ingest sources you trust**, and be careful ingesting repositories you don't control.
- **Exposing it to the internet.** A Cloudflare tunnel or hosted deploy makes the MCP login page public. It still needs your key, but stop the tunnel when you're not using it, keep your key private, and rotate it (`munkymind user rotate-key`) if it may have leaked.
- **Secrets in your notes.** Anything in your notes, including passwords or keys you've written down, is sent to your providers when ingested. Keep secrets out of the notes folder.
- **Your API keys** live in `.env` (and `<DATA_ROOT>/.env` if you used the wizard). Don't commit `.env`; it's in `.gitignore`.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting: **Security → Report a vulnerability** on [munkymind/munkymind](https://github.com/munkymind/munkymind/security/advisories/new). We'll acknowledge within a few days and credit you in the release notes if you'd like.
