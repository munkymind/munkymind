"""FastAPI REST server for Munkymind."""
from __future__ import annotations

import contextlib
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
from mm.auth.sessions import SESSION_PREFIX, check_session
from mm.config.env import load_data_root_env
from mm.core.store import UserStore

@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    # Keep libraries in step with their notes folders (MM_SYNC_INTERVAL=0 turns it off)
    from mm.ingestion.sync import start_background
    stop = start_background(DATA_ROOT)
    yield
    if stop:
        stop.set()


app = FastAPI(
    title="Munkymind",
    description="Personal context library API",
    version=__version__,
    lifespan=_lifespan,
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
            if api_key.startswith(SESSION_PREFIX):
                # A browser session from a login link: read-only, never the API key itself
                session_policy = check_session(user_dir, api_key)
                if session_policy is not None:
                    owner = user_dir.name
                    matched_store = UserStore(DATA_ROOT, user_dir.name)
                    policy = session_policy
                    break
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
        if api_key.startswith(SESSION_PREFIX):
            raise HTTPException(
                status_code=401,
                detail="Signed out: this browser session has ended or expired. "
                       "Get a new link with: munkymind login-link --user <name>",
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


# ------------------------------------------------------------------ #
# Capture and the Review queue
# ------------------------------------------------------------------ #


class CaptureRequest(BaseModel):
    text: str
    kind: str = "fact"
    source_client: str = "other"
    title: str = ""
    domain: str = ""


@app.post("/capture")
def capture(body: CaptureRequest, store: UserStore = Depends(get_user_store)):
    """Save an idea, decision, preference or fact to the Review queue (never straight to the library)."""
    from mm.review import capture as cap

    store.init()
    try:
        return cap.add(store, body.text, kind=body.kind, source_client=body.source_client,
                       title=body.title, domain=body.domain)
    except cap.CaptureError as exc:
        raise HTTPException(status_code=429 if isinstance(exc, cap.RateLimited) else 400, detail=str(exc))


def _aware(iso: object) -> datetime.datetime | None:
    """An ISO time as an aware datetime (no zone = UTC), or None."""
    try:
        t = datetime.datetime.fromisoformat(str(iso or "").strip())
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.UTC)


@app.get("/review")
async def review_list(status: str = "pending", since: str = "",
                      store: UserStore = Depends(get_user_store)):
    """Proposals with the given status (pending, approved, rejected, superseded; comma-separated).
    `since` (an ISO time) keeps only those decided after it. A pending list also carries `batch`:
    what approving the clean ones would write, and what it would set aside for a look."""
    from mm.review import queue

    rows = queue.items(store, status)
    if since:
        cut = _aware(since)
        if cut is None:
            raise HTTPException(status_code=400, detail="since must be an ISO time, e.g. 2026-11-20T09:00:00Z")
        rows = [d for d in rows if (_aware(d.get("decided_at")) or cut) > cut]
    out = {"proposals": [queue.summary(d) for d in rows], **queue.counts(store)}
    if status == "pending":
        out["batch"] = queue.batch_plan(store, rows if not since else None)
    return out


class BatchApprove(BaseModel):
    ids: list[str]


@app.post("/review/batch")
def review_batch(body: BatchApprove, store: UserStore = Depends(get_user_store)):
    """Approve the listed proposals that are still clean (no flags), oldest first. A second change
    to the same page, or a page-changed conflict, is set aside as needing a look; flagged ones are
    skipped. Needs a read-write key."""
    from mm.review import queue

    if len(body.ids) > 200:
        raise HTTPException(status_code=400, detail="Too many at once: send at most 200 ids")
    return queue.approve_clean(store, body.ids, via="api (batch)")


@app.get("/review/{pid}")
async def review_one(pid: str, store: UserStore = Depends(get_user_store)):
    from mm.review import queue

    try:
        return queue.detail(queue.get(store, pid))
    except queue.ReviewError:
        raise HTTPException(status_code=404, detail="Proposal not found")


class ReviewDecision(BaseModel):
    action: str
    force: bool = False


@app.post("/review/{pid}")
def review_decide(pid: str, body: ReviewDecision, store: UserStore = Depends(get_user_store)):
    """Approve (write the page and index it) or reject a pending proposal. Needs a read-write key."""
    from mm.review import queue

    try:
        queue.get(store, pid)
    except queue.ReviewError:
        raise HTTPException(status_code=404, detail="Proposal not found")
    try:
        return queue.decide(store, pid, body.action, force=body.force, via="api")
    except queue.ReviewConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except queue.ReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


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


class LoginLinkRequest(BaseModel):
    token: str


@app.post("/ui/session", include_in_schema=False)
async def ui_session_start(body: LoginLinkRequest):
    """Trade a one-time login link token for a read-only browser session.
    Needs no key (the token is the proof), like /bootstrap."""
    from mm.auth.sessions import LINK_PREFIX, redeem_link

    users_dir = DATA_ROOT / "users"
    if body.token.startswith(LINK_PREFIX) and users_dir.is_dir():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and (user_dir / "ui_sessions.json").exists():
                session = redeem_link(user_dir, body.token)
                if session:
                    return {"session": session, "user_id": user_dir.name}
    raise HTTPException(
        status_code=401,
        detail="This login link has been used or has expired. Links work once, for 10 minutes. "
               "Get a new one with: munkymind login-link --user <name>",
    )


@app.delete("/ui/session", include_in_schema=False)
async def ui_session_end(api_key: Optional[str] = Depends(API_KEY_HEADER)):
    """Sign this browser out (ends its session; an API key is left alone)."""
    from mm.auth.sessions import end_session

    users_dir = DATA_ROOT / "users"
    if api_key and api_key.startswith(SESSION_PREFIX) and users_dir.is_dir():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and end_session(user_dir, api_key):
                break
    return {"status": "signed_out"}


UI_HOMES = ("brains", "review-if-pending")


def ui_home() -> str:
    """MM_UI_HOME: where the viewer opens. `review-if-pending` opens on Review when something is
    waiting; anything else (the default) opens on Brains."""
    v = os.environ.get("MM_UI_HOME", "").strip().lower()
    return v if v in UI_HOMES else "brains"


@app.get("/ui/config.json", include_in_schema=False)
async def ui_config():
    """Viewer settings the server owner chose (no secrets, no user data)."""
    return {"home": ui_home()}


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
    """Accept uploaded files. By default each one waits in the Review queue
    (MM_REVIEW_UPLOADS=off indexes them at once, as before v0.4)."""
    from mm.review import queue

    if queue.uploads_need_review():
        store.init()
        proposals, errors = [], []
        for upload in files:
            try:
                p = queue.propose_upload(store, upload.filename or "file.md", await upload.read())
                proposals.append({"id": p["id"], "path": p["path"], "title": p["title"]})
            except Exception as exc:  # noqa: BLE001 - one bad file must not lose the others
                errors.append(f"{upload.filename}: {exc}")
        if not proposals:
            raise HTTPException(status_code=400, detail="; ".join(errors) or "No files received")
        return {"status": "queued", "proposals": proposals, "errors": errors,
                "pages_created": 0, "pages_updated": 0, "chunks_total": 0,
                "message": f"{len(proposals)} file(s) are waiting in Review. Approve them in the viewer "
                           "(/ui#/review), with POST /review/{id}, or with: munkymind review approve"}
    return await _ingest_now(files, store)


async def _ingest_now(files: list[UploadFile], store: UserStore):
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
