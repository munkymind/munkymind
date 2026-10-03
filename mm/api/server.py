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
from mm.auth.keys import load_key_hash, seed_key_from_env, verify_key
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


async def get_user_store(
    api_key: Optional[str] = Depends(API_KEY_HEADER),
    request: Request = None,  # type: ignore[assignment]
) -> UserStore:
    """Authenticate via X-API-Key header and return the matching UserStore."""
    users_dir = DATA_ROOT / "users"
    matched_store: Optional[UserStore] = None
    endpoint = str(request.url.path) if request else ""
    ip = request.client.host if (request and request.client) else ""
    key_hint = (api_key[:8] + "...") if api_key and len(api_key) >= 8 else (api_key or "")

    if api_key and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            stored_hash = load_key_hash(user_dir)
            if stored_hash and verify_key(api_key, stored_hash):
                user_id = user_dir.name
                matched_store = UserStore(DATA_ROOT, user_id)
                break

    result = "ok" if matched_store else "fail"

    # Write auth_log — pick a store to write to
    log_store = matched_store
    if log_store is None and api_key and users_dir.exists():
        # Try to find any valid store to log against (first user)
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and (user_dir / "db" / "metadata.sqlite3").exists():
                log_store = UserStore(DATA_ROOT, user_dir.name)
                break

    if log_store is not None:
        try:
            con = sqlite3.connect(log_store.db_path)
            con.execute(
                "INSERT INTO auth_log (key_hint, result, endpoint, ip, ts) VALUES (?,?,?,?,?)",
                (key_hint, result, endpoint, ip, datetime.datetime.utcnow().isoformat()),
            )
            con.commit()
            con.close()
        except Exception:
            pass

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
                    "Generate one with: mm keys generate"
                ),
            )
        raise HTTPException(
            status_code=401,
            detail=(
                "Invalid API key. Pass your key via the X-API-Key header: "
                "X-API-Key: mm_sk_<your-key>"
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
    """One-time user bootstrap. Creates the USER_ID user and returns an API key.
    Fails if user already has a key (idempotent-safe)."""
    from mm.auth.keys import generate_key, save_key_hash

    user_id = os.environ.get("USER_ID", "default")
    store = UserStore(DATA_ROOT, user_id)

    if load_key_hash(store.user_dir) is not None:
        raise HTTPException(status_code=409, detail=f"User '{user_id}' already bootstrapped.")

    raw_key, hashed = generate_key()
    store.user_dir.mkdir(parents=True, exist_ok=True)
    save_key_hash(store.user_dir, hashed)
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

        result = run_connector(connector, store, embed_provider)
        return {"status": "ok", **result}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
