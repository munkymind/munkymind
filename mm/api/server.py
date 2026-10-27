"""FastAPI REST server for Munkymind."""
from __future__ import annotations

import datetime
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from mm import __version__
from mm.auth.keys import KeyPolicy, check_key, load_key_hash, seed_key_from_env
from mm.config.env import load_data_root_env
from mm.core.store import UserStore

app = FastAPI(
    title="Munkymind",
    description="Personal context library API",
    version=__version__,
)

DATA_ROOT = Path(os.environ.get("DATA_ROOT", "./data"))
load_data_root_env(DATA_ROOT)
# Same default as /bootstrap below.
seed_key_from_env(DATA_ROOT, os.environ.get("USER_ID", "default"), "mm-api")
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


# ------------------------------------------------------------------ #
# Auth dependency
# ------------------------------------------------------------------ #

# Every route that changes the library needs a read-write key. Rather than
# marking write routes one by one, get_user_store treats every authenticated
# non-GET route as a write unless it is listed here. A new POST route is
# therefore closed to read-only keys until someone decides otherwise.
READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
READ_SCOPE_POST_ROUTES = frozenset({
    "/query",    # asks a question; nothing is stored
    "/preview",  # retrieval only
})


def _route_path(request: Optional[Request]) -> str:
    """The matched route's path template (e.g. /pages/{path:path}), not the raw URL."""
    if request is None:
        return ""
    route = request.scope.get("route")
    return getattr(route, "path", "") or str(request.url.path)


def requires_write(method: str, route_path: str) -> bool:
    return method.upper() not in READ_ONLY_METHODS and route_path not in READ_SCOPE_POST_ROUTES


def _log_auth(store: UserStore, key_hint: str, result: str, endpoint: str, ip: str) -> None:
    try:
        con = sqlite3.connect(store.db_path)
        con.execute(
            "INSERT INTO auth_log (key_hint, result, endpoint, ip, ts) VALUES (?,?,?,?,?)",
            (key_hint, result, endpoint, ip, datetime.datetime.utcnow().isoformat()),
        )
        con.commit()
        con.close()
    except Exception:
        pass


async def get_user_store(
    api_key: Optional[str] = Depends(API_KEY_HEADER),
    request: Request = None,  # type: ignore[assignment]
) -> UserStore:
    """Authenticate via X-API-Key and return the matching UserStore.

    Also enforces the key's policy: an expired key gets 401, and a read-only
    key gets 403 on any route that can change the library (see requires_write).
    """
    users_dir = DATA_ROOT / "users"
    matched_store: Optional[UserStore] = None
    policy: Optional[KeyPolicy] = None
    expired = False
    owner: Optional[str] = None
    endpoint = str(request.url.path) if request else ""
    ip = request.client.host if (request and request.client) else ""
    key_hint = (api_key[:8] + "...") if api_key and len(api_key) >= 8 else (api_key or "")

    if api_key and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            check = check_key(api_key, user_dir)
            if check.matched:
                owner = user_dir.name
                if check.valid:
                    matched_store = UserStore(DATA_ROOT, user_dir.name)
                    policy = check.policy
                expired = check.expired
                break

    write = requires_write(request.method if request else "GET", _route_path(request))
    forbidden = matched_store is not None and write and not policy.can_write  # type: ignore[union-attr]
    result = "expired" if expired else "forbidden" if forbidden else "ok" if matched_store else "fail"

    # Write auth_log — pick a store to write to
    log_store = matched_store or (UserStore(DATA_ROOT, owner) if owner else None)
    if log_store is None and api_key and users_dir.exists():
        # Try to find any valid store to log against (first user)
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and (user_dir / "db" / "metadata.sqlite3").exists():
                log_store = UserStore(DATA_ROOT, user_dir.name)
                break
    if log_store is not None:
        _log_auth(log_store, key_hint, result, endpoint, ip)

    if matched_store is None:
        if not api_key:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Missing API key. Pass your key via the X-API-Key header: "
                    "X-API-Key: mm_sk_<your-key>"
                ),
            )
        if not api_key.startswith("mm_sk_"):
            raise HTTPException(
                status_code=401,
                detail=(
                    "Invalid API key format. Keys must start with 'mm_sk_'. "
                    "Create one with: munkymind user create <name>"
                ),
            )
        if expired:
            raise HTTPException(
                status_code=401,
                detail=(
                    "API key expired. Issue a new one with: "
                    "munkymind user rotate-key <name>"
                ),
            )
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid API key. Pass your key via the X-API-Key header: "
                "X-API-Key: mm_sk_<your-key>"
            ),
        )

    if forbidden:
        raise HTTPException(
            status_code=403,
            detail=(
                "This API key is read-only and this request would change your library. "
                "Use a read-write key (munkymind user rotate-key <name> --read-write)."
            ),
        )

    return matched_store


# ------------------------------------------------------------------ #
# Request / response models
# ------------------------------------------------------------------ #


class QueryRequest(BaseModel):
    query: str
    domains: Optional[list[str]] = None
    limit: int = 10


# ------------------------------------------------------------------ #
# Routes
# ------------------------------------------------------------------ #


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/bootstrap")
async def bootstrap():
    """One-time user bootstrap. Creates the USER_ID user and returns a read-write API key.
    Fails if the user already exists (idempotent-safe). Needs no key, so it is
    the one non-GET route outside the write-scope check."""
    from mm.auth.keys import issue_key

    user_id = os.environ.get("USER_ID", "default")
    store = UserStore(DATA_ROOT, user_id)

    # A revoked key leaves config.yaml behind: revoking must not reopen bootstrap.
    if load_key_hash(store.user_dir) is not None or store.config_path.exists():
        raise HTTPException(
            status_code=409,
            detail=f"User '{user_id}' already bootstrapped. Issue a new key with: "
                   "munkymind user rotate-key",
        )

    raw_key = issue_key(store.user_dir)
    # Ensure config exists
    store.get_config()
    return {"user_id": user_id, "api_key": raw_key}


@app.post("/query")
async def query(body: QueryRequest, store: UserStore = Depends(get_user_store)):
    from mm.api.query import QueryEngine

    engine = QueryEngine()
    chunks = engine.retrieve(store, body.query, domains=body.domains, limit=body.limit)
    user_config = store.get_config()
    result = engine.synthesise(body.query, chunks, user_config)
    return result


@app.get("/domains")
async def list_domains(store: UserStore = Depends(get_user_store)):
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
    return {"domains": domains}


@app.get("/pages")
async def list_pages(
    domain: Optional[str] = None,
    q: Optional[str] = None,
    stale: bool = False,
    store: UserStore = Depends(get_user_store),
):
    from mm.core import library

    return {"pages": library.list_pages(store, domain=domain, q=q, stale_only=stale)}


@app.get("/pages/{path:path}")
async def get_page(path: str, content: bool = False,
                   store: UserStore = Depends(get_user_store)):
    from mm.core import library

    page = library.get_page(store, path, content=content)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@app.get("/status")
async def library_status(store: UserStore = Depends(get_user_store)):
    """Library overview: page counts and freshness per domain, recent ingestions."""
    from mm.core import library

    return library.status(store)


class PreviewRequest(BaseModel):
    query: str
    domains: Optional[list[str]] = None
    limit: int = 10


@app.post("/preview")
async def preview(body: PreviewRequest, store: UserStore = Depends(get_user_store)):
    """What an AI tool would be sent for this question. Retrieval only, no LLM call."""
    from mm.core import library

    return library.preview(store, body.query, domains=body.domains, limit=body.limit)


_UI_DIR = Path(__file__).resolve().parent.parent / "ui"


@app.get("/ui", include_in_schema=False)
async def ui():
    """Read-only library viewer. The page holds no data; it calls the API with your key."""
    return FileResponse(_UI_DIR / "index.html", headers={
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'none'; script-src 'self' 'unsafe-inline'; "
            "style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        ),
    })


@app.get("/ui/voice.json", include_in_schema=False)
async def ui_voice():
    """Messages for the viewer (plain lines, plus the voice catalogue unless MM_VOICE=plain)."""
    from mm import voice

    return voice.lines()


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/ui")


@app.post("/ingest/files")
async def ingest_files(
    files: list[UploadFile] = File(...),
    store: UserStore = Depends(get_user_store),
):
    """Accept uploaded files and ingest them into the user's context library."""
    from mm.connectors.files import FilesConnector
    from mm.config.user import UserConfig
    from mm.embedding.providers import EmbeddingProvider
    from mm.ingestion.runner import run_connector

    tmp_dir = Path(tempfile.mkdtemp(prefix="mm_ingest_"))
    try:
        # Write uploaded files to temp dir, preserving relative paths
        for upload in files:
            filename = upload.filename or "file.md"
            dest = tmp_dir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = await upload.read()
            dest.write_bytes(content)

        # Load user config (needed by FilesConnector)
        # Ensure store is fully initialised (dirs + schema)
        store.init()
        user_config = store.get_config()

        # Build connector
        connector = FilesConnector(
            config={"path": str(tmp_dir)},
            user_config=user_config,
        )

        # Build embed provider from env (MM_EMBED_PROVIDER / OPENAI_API_KEY)
        embed_provider = EmbeddingProvider.from_config({
            "provider": os.environ.get("MM_EMBED_PROVIDER", "openai"),
            "model": os.environ.get("MM_EMBED_MODEL", "text-embedding-3-small"),
            "api_key": os.environ.get("OPENAI_API_KEY"),
        })

        # Uploads add to the library; they never prune pages from other sources
        result = run_connector(connector, store, embed_provider, sync=False)
        return {"status": "ok", **result}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
