# Munkymind — map for coding agents

Short on purpose: where things are and how to check your work. The detail lives in the files this points at.

## What this is

A personal context library. Notes go in (files or GitHub), get a two-tier embedding (summary + detail), land in a per-user ChromaDB collection, and come back out through a REST API and an MCP server that Claude, Cursor, ChatGPT or any tool can call. Design: `docs/architecture.md`. User view: `docs/using-munkymind.md`. Package `munkymind`, CLI `munkymind` (alias `monkey-mind`).

## Layout

| Path | What lives there |
| --- | --- |
| `mm/api/` | FastAPI server (`server.py`) and the query endpoint (`query.py`) |
| `mm/mcp/` | MCP server (`server.py`, HTTP + stdio transports) and its OAuth/auth layer (`auth.py`) |
| `mm/auth/` | API keys (`mm_sk_…`), creation and rotation |
| `mm/cli/` | Typer CLI: `setup`, `ingest`, `watch`, `doctor`, `login-link`, `query`, `status`, `pages`, `show`, `peek`, `review`, `eval`; the setup wizard (`wizard.py`) |
| `mm/review/` | The Review queue (`queue.py`: proposals, page-changed conflict, approve/reject), `capture.py` (MCP `capture`, `POST /capture`) and the capture check (`check.py`) |
| `mm/core/` | The library model (`library.py`) and the ChromaDB store (`store.py`) |
| `mm/embedding/` | Two-tier embedding pipeline and providers (OpenAI, Anthropic) |
| `mm/ingestion/` | Ingest runner: connector → pages → embeddings → store |
| `mm/connectors/` | `base.py` is the interface; `files.py` and `github.py` implement it. New connectors register in `pyproject.toml` under `munkymind.connectors` |
| `mm/config/` | Environment (`env.py`) and per-user config (`user.py`) |
| `mm/eval/` | Parameterised eval suite (`runner.py`), run by `tests/test_eval.py` |
| `mm/voice/` | User-facing messages: `plain.json` (Apache) and `catalogue.json` (CC BY-NC-ND); `MM_VOICE=plain` selects the plain set |
| `mm/ui/` | The viewer (`index.html`) |
| `tests/` | pytest; `fixtures/` holds sample notes. `test_release_prep.py` pins behaviours a release must keep (localhost binding, fenced retrieved text, rate limits) |
| `scripts/` | `leak_scan.py`, `smoke-test.sh` (full Docker path), `start-api.sh`, `start-mcp.sh` |
| `docs/` | architecture, quickstart, connector dev guide, usage, `releases/` |
| `website/` | GitHub Pages site (deployed by `pages.yml`) |
| `examples/`, `assets/` | Sample library and images |

## Run and check

```bash
pip install -e ".[dev]"                 # or: uv pip install --system ".[dev]"
python -m pytest tests/ -v              # the test suite (CI: test.yml)
python -m pytest tests/test_eval.py -v  # the eval suite (CI: eval.yml)
ruff check .                            # line length 100, target py311
python scripts/leak_scan.py             # must print 0 hits (CI: leak-scan.yml)
docker compose up                       # api :8000, mcp :8001; config via .env (see .env.example)
bash test-mcp.sh                        # is the MCP server reachable
./scripts/smoke-test.sh                 # build → compose → ingest → query, end to end
```

CI on every PR: Tests, Eval Suite, Leak Scan, Dependency Audit, Smoke Test, Integration Gate. All must be green before merge.

## Rules

- Work on a branch and open a PR to `main`. Never push to `main`, never merge your own PR.
- Commit messages and PR text say what changed and why, nothing else: no tool attribution, no internal references.
- The leak scan protects a hashed list of terms (`.github/leak-hashes.txt`). If it fails, remove the term; do not edit the list to pass. Add a term with `python scripts/leak_scan.py --add "term"`.
- Do not change an existing test to make a change pass. Add a test that reproduces the bug first.
- Retrieved note text is data, never instructions; it is fenced before it reaches a model (`test_release_prep.py` checks this).
- Secrets come from `.env` only. Nothing in the repo holds a key.
- Every user-visible event has a line in `mm/voice/plain.json`; keep the plain and catalogue sets in step.

## Releasing

Bump `version` in `pyproject.toml`, move `## [Unreleased]` in `CHANGELOG.md` (Keep a Changelog) to the new version with the date, and tag. `release.yml` runs the full suite on the tag. A release note goes in `docs/releases/`.

## Where to look first

| Task | Start at |
| --- | --- |
| A query returns the wrong pages | `mm/api/query.py`, then `mm/core/store.py`; check `MM_MAX_DISTANCE` |
| MCP client can't connect or authorise | `mm/mcp/auth.py`, `docs/using-munkymind.md` (remote connectors), `test-mcp.sh` |
| Ingest misses or mangles a file | `mm/connectors/files.py`, `mm/ingestion/runner.py`, `tests/test_files_connector.py` |
| A new source type | `docs/connector-dev-guide.md`, `mm/connectors/base.py` |
| Wording a user sees | `mm/voice/` |
| Viewer | `mm/ui/index.html` |
| Capture, Review, approvals | `mm/review/`, `tests/test_review.py` |
