"""Proposals: pages that wait for the user's approval before they reach the library.

A proposal is the full text of one page plus why it was proposed. Approving writes the page
to the user's own `context-repo/<domain>/` folder (Munkymind owns it; the notes folder is
never written to) and indexes it, so the next question can find it. Rejecting changes nothing.

Each proposal records the version of the page it was written against. If the page changed
before approval (another proposal for the same page was approved, or the file was edited),
approval stops with a conflict instead of silently replacing the newer text; `force=True`
replaces it anyway.

Storage: <user_dir>/review/<id>.json, one proposal per file.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mm.connectors.files import FilesConnector
from mm.core.store import UserStore
from mm.review import switched_on

STATUSES = ("pending", "approved", "rejected", "superseded")
ACTIONS = {"approve": "approved", "reject": "rejected"}
_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_PATH = re.compile(r"^[a-z0-9][a-z0-9_-]*/[A-Za-z0-9][\w.-]*\.md$")
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
_lock = threading.Lock()

KEEP = ("id", "status", "path", "domain", "title", "origin", "kind", "source_client", "change_note",
        "flags", "changes", "current", "proposed", "base_version", "created_at")


class ReviewError(ValueError):
    """A bad proposal or decision; the message goes back to the caller."""


class ReviewConflict(ReviewError):
    """The page changed after the proposal was made."""


def uploads_need_review() -> bool:
    """MM_REVIEW_UPLOADS (default on): uploaded files wait in Review instead of going straight in."""
    return switched_on("MM_REVIEW_UPLOADS")


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def review_dir(store: UserStore) -> Path:
    return store.user_dir / "review"


def _dir(store: UserStore) -> Path:
    d = review_dir(store)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(store: UserStore, pid: str) -> Path:
    if not _ID.match(pid or ""):
        raise ReviewError(f"bad proposal id: {pid!r}")
    return review_dir(store) / f"{pid}.json"


def check_path(path: str) -> str:
    if not isinstance(path, str) or ".." in path or not _PATH.match(path):
        raise ReviewError(f"bad page path: {path!r} (expected <domain>/<name>.md)")
    return path


def page_file(store: UserStore, path: str) -> Path:
    return store.context_dir / check_path(path)


def current_text(store: UserStore, path: str) -> str:
    f = page_file(store, path)
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return ""


def slug(text: str, limit: int = 60) -> str:
    s = _SLUG.sub("-", (text or "").strip()).strip("-.").lower()
    if len(s) > limit:  # cut at a word break where there is one
        s = s[:limit + 1].rsplit("-", 1)[0] if "-" in s[:limit + 1] else s[:limit]
    return s.rstrip("-.") or "page"


# ── versions and diffs ────────────────────────────────────────────────────────

def _body(text: str) -> str:
    """Page text without YAML frontmatter."""
    if (text or "").startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text or ""


def _content_lines(text: str) -> list[str]:
    lines = [ln.rstrip() for ln in _body(text).splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def version_of(text: str | None) -> str:
    """A short hash of a page's content ('' for no page). Frontmatter and surrounding blank
    lines don't count, so re-saving the same text doesn't look like a change."""
    if not text:
        return ""
    norm = "\n".join(_content_lines(text))
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12] if norm else ""


def diff(d: dict) -> list[dict]:
    """Line diff for display: [{op: ' '|'+'|'-'|'@', text}]. A new page is all '+'."""
    out = []
    for ln in difflib.unified_diff(_content_lines(d.get("current") or ""),
                                   _content_lines(d.get("proposed") or ""), lineterm="", n=2):
        if ln.startswith(("---", "+++")):
            continue
        op = "@" if ln.startswith("@@") else (ln[:1] or " ")
        out.append({"op": op, "text": ln if op == "@" else ln[1:]})
    return out


# ── storage ───────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _locked(store: UserStore):
    """One writer per user's queue at a time, across threads and processes (API + MCP)."""
    with _lock:
        try:
            import fcntl
        except ImportError:  # Windows without Docker: threads only
            yield
            return
        store.user_dir.mkdir(parents=True, exist_ok=True)
        with open(store.user_dir / ".review.lock", "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _save(store: UserStore, d: dict) -> None:
    f = _file(store, d["id"])
    _dir(store)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def new_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def get(store: UserStore, pid: str) -> dict:
    f = _file(store, pid)
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except OSError:
        raise ReviewError(f"no proposal {pid}") from None


def items(store: UserStore, status: str = "") -> list[dict]:
    """Proposals newest first, optionally only those with the given status(es), comma-separated."""
    wanted = {s.strip() for s in status.split(",") if s.strip()} if status else set(STATUSES)
    d = review_dir(store)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rec.get("status") in wanted:
            out.append(rec)
    return out


def counts(store: UserStore) -> dict[str, Any]:
    pending = items(store, "pending")
    return {"pending": len(pending),
            "oldest_pending": min((p.get("created_at") or "" for p in pending), default=None)}


_BULLET = re.compile(r"^[-*•]\s+")


def peek(d: dict, width: int = 110) -> str:
    """One line of what a proposal changes, for a list row: the first added line (else the first
    removed one) and how many lines are added and removed. Headings are left out when anything
    else changed. Empty when the text doesn't change."""
    lines = [ln for ln in diff(d) if ln["op"] in "+-" and ln["text"].strip()]
    if not lines:
        return ""
    text_lines = [ln for ln in lines if not ln["text"].lstrip().startswith("#")]
    counted = text_lines or lines  # headings count only when nothing else changed
    added = sum(1 for ln in counted if ln["op"] == "+")
    removed = len(counted) - added
    op = "+" if added else "-"
    text = next(ln["text"].strip() for ln in counted if ln["op"] == op)
    op = "+" if op == "+" else "−"
    line = f"{op} {_BULLET.sub('', text)}"
    if len(line) > width:
        line = line[:width - 1].rstrip() + "…"
    return f"{line}  (+{added} −{removed})"


def summary(d: dict) -> dict:
    """What a list shows: no page bodies."""
    return {k: d.get(k) for k in ("id", "status", "path", "domain", "title", "origin", "kind",
                                  "source_client", "change_note", "created_at", "decided_at",
                                  "decided_via", "superseded_by", "result")} | {
        "flags": {k: v for k, v in (d.get("flags") or {}).items() if v},
        "changes": d.get("changes") or [],
        "is_new_page": not d.get("current"),
        "peek": peek(d),
    }


def detail(d: dict) -> dict:
    return {**summary(d), "diff": diff(d), "proposed": d.get("proposed", "")}


def propose(store: UserStore, *, path: str, domain: str, title: str, proposed: str, origin: str,
            change_note: str = "", kind: str = "", source_client: str = "",
            flags: dict | None = None, changes: list | None = None, supersede: bool = False) -> dict:
    """Queue a page for review. `supersede=True` replaces earlier pending proposals for the
    same page (a newer upload of the same file wins)."""
    check_path(path)
    if not isinstance(proposed, str) or not proposed.strip():
        raise ReviewError("the proposed page is empty")
    if not title or not domain:
        raise ReviewError("a proposal needs a title and a domain")
    with _locked(store):
        current = current_text(store, path)
        rec = {"id": new_id(), "status": "pending", "path": path, "domain": domain, "title": title,
               "origin": origin, "kind": kind, "source_client": source_client,
               "change_note": change_note, "flags": flags or {}, "changes": changes or [],
               "current": current, "proposed": proposed, "base_version": version_of(current),
               "created_at": _now()}
        while _file(store, rec["id"]).exists():
            rec["id"] = new_id()
        if supersede:
            for old in items(store, "pending"):
                if old.get("path") == path:
                    old.update(status="superseded", decided_at=_now(), decided_via="replaced",
                               superseded_by=rec["id"])
                    _save(store, old)
        _save(store, {k: rec.get(k) for k in KEEP})
    return rec


def pending_paths(store: UserStore) -> set[str]:
    return {p.get("path") for p in items(store, "pending")}


# ── approving ─────────────────────────────────────────────────────────────────

class ReviewedPagesConnector(FilesConnector):
    """The user's approved pages (context-repo/), indexed like a notes folder of their own."""

    connector_id = "review"
    display_name = "Approved in Review"
    description = "Pages approved in the Review queue."

    def ingest(self, progress_cb=None):
        pages = super().ingest(progress_cb)
        for p in pages:
            p.connector = p.source = "review"
        return pages


def index_library(store: UserStore, embed_provider=None) -> dict:
    """Index the approved pages folder: new and edited pages are embedded, deleted ones removed."""
    from mm.embedding.providers import EmbeddingProvider
    from mm.ingestion.runner import run_connector

    store.init()
    cfg = store.get_config()
    embed = embed_provider or EmbeddingProvider.from_config(
        {"provider": cfg.embedding.provider, "model": cfg.embedding.model})
    connector = ReviewedPagesConnector({"path": str(store.context_dir), "extensions": [".md"]}, cfg)
    return run_connector(connector, store, embed)


def decide(store: UserStore, pid: str, action: str, *, force: bool = False, via: str = "api",
           index: Callable[[UserStore], dict] | None = None) -> dict:
    """Approve (write the page, then index it) or reject a pending proposal."""
    if action not in ACTIONS:
        raise ReviewError("action must be approve or reject")
    with _locked(store):
        d = get(store, pid)
        if d.get("status") != "pending":
            raise ReviewError(f"{pid} is already {d.get('status')}")
        result: dict = {}
        if action == "approve":
            live = version_of(current_text(store, d["path"]))
            if live != (d.get("base_version") or "") and not force:
                raise ReviewConflict(
                    "the page changed after this proposal was made: approve anyway to replace it "
                    "with this version, or reject it")
            f = page_file(store, d["path"])
            previous = f.read_text(encoding="utf-8") if f.exists() else None
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(d["proposed"], encoding="utf-8")
            os.replace(tmp, f)
            try:
                ran = (index or index_library)(store)
            except Exception as exc:
                if previous is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_text(previous, encoding="utf-8")
                raise ReviewError(f"couldn't index the page ({type(exc).__name__}: {exc}); "
                                  "nothing was changed, try again") from exc
            result = {"path": d["path"], "chunks_total": ran.get("chunks_total", 0),
                      "pages_created": ran.get("pages_created", 0),
                      "pages_updated": ran.get("pages_updated", 0)}
            d["result"] = result
        d["status"] = ACTIONS[action]
        d["decided_at"] = _now()
        d["decided_via"] = via
        _save(store, d)
    return {"id": pid, "status": d["status"], **result}


# ── approving the clean ones in one go ────────────────────────────────────────

SAME_PAGE = "another change to this page comes first"
PAGE_CHANGED = "the page changed after this was proposed"


def is_clean(d: dict) -> bool:
    """Pending with no flag of any kind (no change of mind, no warning): nothing to weigh up."""
    return (d.get("status") == "pending" and not any((d.get("flags") or {}).values())
            and not d.get("changes"))


def _oldest_first(ds: list[dict]) -> list[dict]:
    return sorted(ds, key=lambda d: (d.get("created_at") or "", d["id"]))


def batch_plan(store: UserStore, pending: list[dict] | None = None) -> dict:
    """What one "approve the clean ones" would write, oldest first, and which clean proposals it
    sets aside as needing a look: a second proposal for a page already in the batch (approving
    the first changes the page under it), or one whose page changed since it was made.
    Flagged proposals are never part of it."""
    ds = _oldest_first([d for d in (items(store, "pending") if pending is None else pending)
                        if is_clean(d)])
    approve: list[str] = []
    needs_look: list[dict] = []
    seen: set[str] = set()
    for d in ds:
        if d["path"] in seen:
            needs_look.append({"id": d["id"], "path": d["path"], "reason": SAME_PAGE})
        elif version_of(current_text(store, d["path"])) != (d.get("base_version") or ""):
            needs_look.append({"id": d["id"], "path": d["path"], "reason": PAGE_CHANGED})
        else:
            approve.append(d["id"])
        seen.add(d["path"])
    return {"approve": approve, "needs_look": needs_look}


def approve_clean(store: UserStore, ids: list[str], *, via: str = "api",
                  index: Callable[[UserStore], dict] | None = None) -> dict:
    """Approve the given proposals, oldest first, if they are still clean and pending.
    Anything flagged, decided already or unknown is skipped. A second proposal for a page
    already approved in this batch, or a page-changed conflict, is set aside, never forced."""
    chosen, skipped = [], []
    for pid in dict.fromkeys(ids or []):
        try:
            chosen.append(get(store, pid))
        except ReviewError:
            skipped.append(pid)
    approved: list[dict] = []
    needs_look: list[dict] = []
    seen: set[str] = set()
    for d in _oldest_first(chosen):
        if not is_clean(d):
            skipped.append(d["id"])
            continue
        if d["path"] in seen:
            needs_look.append({"id": d["id"], "path": d["path"], "reason": SAME_PAGE})
            continue
        seen.add(d["path"])
        try:
            r = decide(store, d["id"], "approve", via=via, index=index)
        except ReviewConflict:
            needs_look.append({"id": d["id"], "path": d["path"], "reason": PAGE_CHANGED})
            continue
        except ReviewError as exc:  # decided meanwhile, or indexing failed (the write was undone)
            needs_look.append({"id": d["id"], "path": d["path"], "reason": str(exc)})
            continue
        approved.append({"id": d["id"], "path": r.get("path") or d["path"]})
    return {"approved": approved, "needs_look": needs_look, "skipped": skipped}


# ── uploads ───────────────────────────────────────────────────────────────────

def _upload_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    stem = Path(filename).stem
    if suffix == ".pdf":
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n\n".join((p.extract_text() or "") for p in pdf.pages).strip()
        return f"# {stem}\n\n## Content\n\n{text}\n"
    text = data.decode("utf-8", errors="replace")
    if suffix == ".md":
        return text
    return f"# {stem}\n\n{text}\n"


def propose_upload(store: UserStore, filename: str, data: bytes) -> dict:
    """Queue one uploaded file (.md, .txt or .pdf) as a page proposal."""
    from mm.connectors.files import DOMAIN_KEYWORDS, _detect_domain, _parse_frontmatter

    name = Path(filename or "upload.md").name
    if Path(name).suffix.lower() not in (".md", ".txt", ".pdf"):
        raise ReviewError(f"{name}: only .md, .txt and .pdf files can be uploaded")
    text = _upload_text(name, data)
    if not text.strip():
        raise ReviewError(f"{name}: the file is empty")
    cfg = store.get_config()
    ids = set(DOMAIN_KEYWORDS) | {d.id for d in cfg.domains}
    meta, body = _parse_frontmatter(text)
    domain = _detect_domain(Path(name), domain_ids=ids, meta=meta, text=body)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", domain):
        domain = "personal"
    path = f"{domain}/{slug(Path(name).stem, 80)}.md"
    h1 = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
    title = (meta.get("title") or h1 or Path(name).stem)[:120]
    exists = bool(current_text(store, path))
    return propose(store, path=path, domain=domain, title=title, proposed=text, origin="upload",
                   change_note=f"Uploaded {name}" + (" (replaces the current version)" if exists else ""),
                   supersede=True)
