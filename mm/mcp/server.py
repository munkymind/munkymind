"""MCP server for Munkymind.

Exposes 5 tools:
  - query_context
  - list_domains
  - get_page
  - search_pages
  - get_staleness_report

Supports:
  stdio transport (default, for Claude Desktop)
  HTTP transport (--transport http --port N, for Cursor)

Environment:
  USER_ID   — user to scope all queries to
  DATA_ROOT — root data directory (default: ./data)
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

# ------------------------------------------------------------------ #
# Server init
# ------------------------------------------------------------------ #

mcp = MCPServer("munkymind")

# ------------------------------------------------------------------ #
# Store factory  (reads env on first call, cached per process)
# ------------------------------------------------------------------ #

_store: Any = None


def _get_store():
    global _store
    if _store is None:
        from mm.core.store import UserStore

        user_id = os.environ.get("USER_ID", "default")
        data_root = Path(os.environ.get("DATA_ROOT", "./data"))
        _store = UserStore(data_root, user_id)
        _store.init()
    return _store


def _text(content: str, sources: list = None, staleness_warnings: list = None) -> dict:
    """Build a MCP-compliant tool response."""
    return {
        "content": [{"type": "text", "text": content}],
        "sources": sources or [],
        "staleness_warnings": staleness_warnings or [],
    }


# ------------------------------------------------------------------ #
# Tools
# ------------------------------------------------------------------ #


@mcp.tool()
def query_context(
    query: str,
    domains: Optional[list[str]] = None,
    limit: int = 10,
) -> dict:
    """Semantic search over the user's context library with LLM synthesis.

    Args:
        query: The question or search query.
        domains: Optional list of domain IDs to restrict the search.
        limit: Maximum number of source chunks to retrieve (default 10).

    Returns:
        MCP tool result with synthesised answer, sources, and staleness warnings.
    """
    from mm.api.query import QueryEngine

    store = _get_store()
    engine = QueryEngine()
    chunks = engine.retrieve(store, query, domains=domains, limit=limit)
    user_config = store.get_config()
    result = engine.synthesise(query, chunks, user_config)
    return _text(
        result["answer"],
        sources=result.get("sources", []),
        staleness_warnings=result.get("staleness_warnings", []),
    )


@mcp.tool()
def list_domains() -> dict:
    """List all domains in the user's context library with page counts and staleness info.

    Returns:
        MCP tool result with domains list.
    """
    store = _get_store()
    user_config = store.get_config()

    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT domain, COUNT(*) as page_count FROM pages GROUP BY domain"
    ).fetchall()
    con.close()

    counts: dict[str, int] = {row["domain"]: row["page_count"] for row in rows}
    domains = []
    for d in user_config.domains:
        domains.append(
            {
                "id": d.id,
                "label": d.label,
                "staleness_threshold_days": d.staleness_threshold_days,
                "page_count": counts.get(d.id, 0),
            }
        )

    return _text(json.dumps({"domains": domains}, indent=2))


@mcp.tool()
def get_page(path: str) -> dict:
    """Retrieve a specific context page by its path/ID.

    Args:
        path: The page path or ID (as stored in metadata).

    Returns:
        MCP tool result with page metadata and content.
    """
    store = _get_store()
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM pages WHERE id=?", (path,)).fetchone()
    con.close()

    if row is None:
        return _text(f"Page not found: {path}")

    page = dict(row)
    # Try to read raw file content if available
    raw_path = page.get("raw_path")
    content_text = ""
    if raw_path:
        raw = Path(raw_path)
        if not raw.is_absolute():
            raw = store.context_dir / raw_path
        if raw.exists():
            try:
                content_text = raw.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content_text = ""

    result = {
        "metadata": page,
        "content": content_text,
    }
    return _text(json.dumps(result, indent=2, default=str))


@mcp.tool()
def search_pages(term: str, domain: Optional[str] = None) -> dict:
    """Keyword/metadata search over stored context pages.

    Args:
        term: Search term (matched against title, source, tags, and ID).
        domain: Optional domain ID to restrict the search.

    Returns:
        MCP tool result with matching pages list.
    """
    store = _get_store()
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    like = f"%{term}%"

    if domain:
        rows = con.execute(
            """SELECT * FROM pages
               WHERE domain=?
                 AND (title LIKE ? OR source LIKE ? OR tags LIKE ? OR id LIKE ?)
               ORDER BY updated_at DESC""",
            (domain, like, like, like, like),
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT * FROM pages
               WHERE title LIKE ? OR source LIKE ? OR tags LIKE ? OR id LIKE ?
               ORDER BY updated_at DESC""",
            (like, like, like, like),
        ).fetchall()
    con.close()

    pages = [dict(r) for r in rows]
    return _text(json.dumps({"pages": pages, "count": len(pages)}, indent=2, default=str))


@mcp.tool()
def get_staleness_report(domain: Optional[str] = None) -> dict:
    """Get a report of pages exceeding their staleness threshold.

    Args:
        domain: Optional domain ID to restrict the report.

    Returns:
        MCP tool result with stale pages and their ages.
    """
    store = _get_store()
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row

    if domain:
        rows = con.execute(
            "SELECT * FROM pages WHERE domain=? ORDER BY updated_at ASC", (domain,)
        ).fetchall()
    else:
        rows = con.execute("SELECT * FROM pages ORDER BY updated_at ASC").fetchall()
    con.close()

    now = datetime.datetime.utcnow()
    stale: list[dict] = []

    for row in rows:
        page = dict(row)
        updated_str = page.get("updated_at", "")
        threshold = int(page.get("staleness_threshold_days") or 30)
        if updated_str:
            try:
                updated = datetime.datetime.fromisoformat(
                    updated_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
                age_days = (now - updated).days
                if age_days > threshold:
                    stale.append(
                        {
                            "id": page["id"],
                            "domain": page["domain"],
                            "title": page.get("title"),
                            "updated_at": updated_str,
                            "age_days": age_days,
                            "threshold_days": threshold,
                        }
                    )
            except ValueError:
                pass

    report = {"stale_pages": stale, "count": len(stale)}
    warnings = [
        f"{p['id']} is {p['age_days']} days old (threshold: {p['threshold_days']})"
        for p in stale
    ]
    return _text(
        json.dumps(report, indent=2, default=str),
        staleness_warnings=warnings,
    )


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #


def _seed_api_key_from_env() -> None:
    from mm.auth.keys import seed_key_from_env

    seed_key_from_env(
        Path(os.environ.get("DATA_ROOT", "./data")),
        os.environ.get("USER_ID", "default"),
        "mm-mcp",
    )


def public_base(request) -> str:
    """The address clients reached us on: MCP_BASE_URL if set, else the request's
    own (forwarded) host. Behind a tunnel or proxy that is the public HTTPS URL."""
    configured = os.environ.get("MCP_BASE_URL")  # empty from compose = unset
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return f"{proto}://{request.headers.get('host', request.url.netloc)}"

# OAuth discovery (RFC 8414). claude.ai / ChatGPT read authorization_endpoint and
# registration_endpoint from here; without them the connector login never starts.
async def oauth_metadata(request):
    from starlette.responses import JSONResponse

    base = public_base(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "client_credentials"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
    })

# RFC 9728: which authorization server protects /mcp.
async def protected_resource(request):
    from starlette.responses import JSONResponse

    base = public_base(request)
    return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": [base]})


def build_http_app(host: str, oauth_metadata, protected_resource=None):
    """Compose discovery + streamable HTTP (/mcp) + SSE (/sse, /messages).

    - /mcp is what claude.ai and ChatGPT connectors call. It used to be built
      but never mounted, so every request 404'd after a successful OAuth login.
    - `host` must be the real bind host: the SDK enables DNS-rebinding
      protection for the default 127.0.0.1, which answers 421 to any other
      Host header (e.g. *.up.railway.app).
    - The streamable app's lifespan runs the session manager; without it /mcp
      requests fail once mounted.
    """
    from starlette.applications import Starlette
    from starlette.routing import Route

    mcp_app = mcp.streamable_http_app(host=host)
    sse_app = mcp.sse_app(host=host)
    return Starlette(
        routes=[
            Route("/.well-known/oauth-authorization-server", oauth_metadata),
            *([Route("/.well-known/oauth-protected-resource", protected_resource),
               Route("/.well-known/oauth-protected-resource/mcp", protected_resource)] if protected_resource else []),
            *mcp_app.routes,
            *sse_app.routes,
        ],
        lifespan=mcp_app.router.lifespan_context,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Munkymind MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport to use (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8001,
        help="Port for HTTP transport (default: 8001)",
    )
    args = parser.parse_args()
    _seed_api_key_from_env()

    if args.transport == "stdio":
        asyncio.run(mcp.run_stdio_async())
    else:
        # HTTP / streamable-http — bind to 0.0.0.0 for Railway/remote deployments
        # Wrapped with OAuthMCPMiddleware: handles /token (client credentials) and
        # validates Bearer / X-API-Key on all MCP requests.
        import uvicorn
        from starlette.responses import JSONResponse
        from mm.mcp.auth import OAuthMCPMiddleware

        host = os.environ.get("MCP_HOST", "0.0.0.0")

        composed = build_http_app(host, oauth_metadata, protected_resource)
        protected_app = OAuthMCPMiddleware(composed)

        config = uvicorn.Config(protected_app, host=host, port=args.port, log_level="info")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
