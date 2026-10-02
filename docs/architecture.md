# Architecture Overview

Munkymind is a self-hosted personal context library. This document describes the technical design.

---

## Component Map

```
┌─────────────────────────────────────────────────────────┐
│                      Data Sources                        │
│   Local Files   │   GitHub Profile   │  (community +)   │
└────────┬────────┴─────────┬──────────┴──────────────────┘
         │                  │
         ▼                  ▼
┌─────────────────────────────────────────────────────────┐
│                  Connector Framework                     │
│   BaseConnector → ConnectorPage (id, domain, summary,   │
│   detail_sections, source, provenance)                  │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│               Embedding Pipeline                         │
│   Two-tier chunking: [SUMMARY] + [DETAIL:{heading}]     │
│   EmbeddingProvider (OpenAI / Ollama)                   │
│   → ChromaDB collection: mm-{user_id}                   │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   User Store                             │
│   $DATA_ROOT/users/{user_id}/                           │
│   ├── config.yaml      (domains, providers, connectors) │
│   ├── api_key.hash     (bcrypt)                         │
│   ├── context-repo/    (domain pages on disk)           │
│   ├── chroma/          (vector store)                   │
│   └── db/metadata.sqlite3  (pages, ingestions, auth_log)│
└─────────────────────┬───────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
┌─────────────────┐   ┌───────────────────────┐
│   REST API      │   │     MCP Server         │
│   FastAPI       │   │   fastmcp (stdio/HTTP) │
│   Port 8000     │   │   Port 8001            │
│   X-API-Key auth│   │   Local sidecar        │
└────────┬────────┘   └──────────┬────────────┘
         │                       │
         ▼                       ▼
┌─────────────────────────────────────────────────────────┐
│              Query Engine                                │
│   retrieve(user_id, query, domains, limit)              │
│   → embed query → ChromaDB search → top-k chunks       │
│   synthesise(query, chunks, user_config)                │
│   → LLMProvider → {answer, sources, staleness_warnings} │
└─────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Ingestion

```
connector.ingest() → list[ConnectorPage]
  ↓
pipeline.build_chunks(pages) → list[Chunk]
  ↓  [SUMMARY] chunk + N [DETAIL:{heading}] chunks per page
EmbeddingProvider.embed(chunks) → embeddings
  ↓
ChromaDB.upsert(collection=mm-{user_id}, ...)
  ↓
SQLite.upsert_pages(user_id, pages)  ← metadata + provenance
```

### Query

```
Client → POST /query {query, domains?, limit?}
  ↓  X-API-Key auth → UserStore
QueryEngine.retrieve(store, query)
  ↓  embed query → ChromaDB similarity search
QueryEngine.synthesise(query, chunks, config)
  ↓  LLMProvider with source-citation system prompt
Response: {answer, sources, staleness_warnings}
```

---

## Multi-Tenancy

v0.1 uses **directory-per-user isolation** — deliberately simple.

- Each user gets `$DATA_ROOT/users/{user_id}/` — completely separate
- ChromaDB collection: `mm-{user_id}` — no shared collections
- API key → user_id mapping done at auth time; all downstream queries use that user_id
- Path traversal rejected at UserStore construction (`..` and `/` disallowed in user_id)
- No cross-user SQL joins, no cross-user vector queries

No Postgres multi-tenancy in v0.1. Postgres is Phase 2 if/when needed.

---

## Auth Model

```
munkymind user create rob
→ generate cryptographically random 32-byte key
→ encode as base58: mm_sk_{48 chars}
→ bcrypt hash stored in users/rob/api_key.hash
→ raw key shown ONCE to user — never stored
```

On every API request:
```
X-API-Key: mm_sk_...
→ scan users/ dirs, bcrypt.verify against each api_key.hash
→ match found → scope all queries to that user_id
→ no match → 401, log attempt, no context leaked
```

---

## Two-Tier Embedding

Inherited from the original Munkymind architecture. Each context page produces:

- **1 summary chunk:** `[SUMMARY] {page.summary}` — 100–200 tokens, self-contained
- **N detail chunks:** `[DETAIL:{heading}] {section_text}` — one per markdown section

Retrieval hits the summary tier first (fast, broad). Detail chunks provide depth when summary relevance is high. This keeps query latency low while preserving depth.

---

## Connector Interface

See [connector-dev-guide.md](connector-dev-guide.md) for how to build a connector.

The interface:
```python
class BaseConnector(ABC):
    connector_id: str
    display_name: str
    description: str

    def validate(self) -> tuple[bool, str]: ...
    def schema(self) -> dict: ...
    def ingest(self, progress_cb=None) -> list[ConnectorPage]: ...
```

Connectors are registered via Python entry points — no changes to core required.

---

## MCP Server

Exposes 5 tools via the MCP protocol:

| Tool | Description |
|------|-------------|
| `query_context` | Semantic search + LLM synthesis across all domains |
| `list_domains` | List domains with page counts and staleness |
| `get_page` | Retrieve a specific page by path |
| `search_pages` | Keyword search across page metadata |
| `get_staleness_report` | Pages exceeding their staleness threshold |

Supports stdio transport (Claude Desktop) and HTTP transport (Cursor, other HTTP clients).

---

## Docker Compose

```
docker compose up
  api  → uvicorn mm.api.server:app  (port 8000)
  mcp  → python -m mm.mcp.server   (port 8001)
  volume: mm_data → /data (shared between services)
```

Both services share the same Docker image and volume. The MCP service depends on the API healthcheck passing before starting.
