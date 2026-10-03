"""Read-only views of a user's library: pages, freshness, status and query previews.

Shared by the CLI (`munkymind pages/show/status/peek`), the REST API and the /ui page.
Page text lives in the vector store as chunks, so `get_page` rebuilds it from them.
"""
from __future__ import annotations

import datetime
import re
import sqlite3
from typing import Any

from mm.core.store import UserStore
from mm.embedding.pipeline import CHUNK_OVERLAP_CHARS

# A page is "fresh" for the first half of its staleness threshold, "ripening" until the
# threshold, then "stale". The UI calls these Fed / Peckish / Starving.
FRESHNESS_LEVELS = ("fresh", "ripening", "stale")

# Each domain ("brain") gets an icon: its own from config.yaml, a default for the built-in
# domains, or a stable pick from the pool for anything else.
DEFAULT_ICONS = {"health": "💪", "professional": "💼", "personal": "🏡",
                 "strategic": "♟️", "temporal": "⏰", "projects": "🛠️"}
ICON_POOL = ("🦉", "🐙", "🦊", "🐝", "🍄", "🌶️", "🎸", "🔭", "🧪", "🎲", "🪐", "🧩")


def domain_icon(domain_id: str, configured: str = "") -> str:
    if configured:
        return configured
    if domain_id in DEFAULT_ICONS:
        return DEFAULT_ICONS[domain_id]
    return ICON_POOL[sum(map(ord, domain_id)) % len(ICON_POOL)]


_CHUNK_PREFIX = re.compile(r"^\[(?:SUMMARY|DETAIL:[^\]]*|CONTENT)\]\s*")


def approx_tokens(text: str) -> int:
    """Rough token count (1 token ≈ 4 chars), good enough for showing relative cost."""
    return (len(text) + 3) // 4


def _parse_ts(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    try:
        ts = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts.replace(tzinfo=None) if ts.tzinfo is None else ts.astimezone(
        datetime.timezone.utc).replace(tzinfo=None)


def freshness(updated_at: str | None, threshold_days: int | None,
              now: datetime.datetime | None = None) -> dict[str, Any]:
    """Return {level, age_days, threshold_days} for a page."""
    threshold = int(threshold_days or 30)
    ts = _parse_ts(updated_at)
    if ts is None:
        return {"level": "stale", "age_days": None, "threshold_days": threshold}
    now = now or datetime.datetime.utcnow()
    age = max(0, (now - ts).days)
    if age > threshold:
        level = "stale"
    elif age > threshold / 2:
        level = "ripening"
    else:
        level = "fresh"
    return {"level": level, "age_days": age, "threshold_days": threshold}


def _connect(store: UserStore) -> sqlite3.Connection:
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    return con


def _with_freshness(row: dict, now: datetime.datetime | None = None) -> dict:
    row = dict(row)
    row["freshness"] = freshness(row.get("updated_at"), row.get("staleness_threshold_days"), now)
    row.pop("raw_path", None)  # local filesystem path; not useful outside the machine
    return row


def list_pages(store: UserStore, domain: str | None = None, q: str | None = None,
               stale_only: bool = False) -> list[dict]:
    """Pages newest first, optionally filtered by domain, a search term, or staleness."""
    if not store.db_path.exists():
        return []
    sql, args = "SELECT * FROM pages WHERE 1=1", []
    if domain:
        sql += " AND domain = ?"
        args.append(domain)
    if q:
        sql += " AND (title LIKE ? OR id LIKE ? OR tags LIKE ?)"
        like = f"%{q}%"
        args += [like, like, like]
    sql += " ORDER BY updated_at DESC"
    con = _connect(store)
    try:
        rows = [_with_freshness(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()
    if stale_only:
        rows = [r for r in rows if r["freshness"]["level"] == "stale"]
    return rows


def _collection(store: UserStore):
    import chromadb

    client = chromadb.PersistentClient(path=str(store.chroma_dir))
    try:
        return client.get_collection(store.collection_name())
    except Exception:
        return None


def _join_parts(parts: list[str]) -> str:
    """Re-join a section that was split with overlap during chunking."""
    if not parts:
        return ""
    text = parts[0]
    for part in parts[1:]:
        text += part[CHUNK_OVERLAP_CHARS:] if len(part) > CHUNK_OVERLAP_CHARS else part
    return text


def _chunk_order(chunk_id: str) -> tuple[int, str, int]:
    pieces = chunk_id.split("::")
    kind = 0 if len(pieces) > 1 and pieces[1] == "summary" else 1
    try:
        index = int(pieces[-1])
    except ValueError:
        index = 0
    return kind, "::".join(pieces[1:-1]), index


def page_sections(store: UserStore, page_id: str) -> list[dict]:
    """[{heading, text, tokens}] for a page, summary first, in ingestion order."""
    collection = _collection(store)
    if collection is None:
        return []
    got = collection.get(where={"page_id": page_id}, include=["documents", "metadatas"])
    items = sorted(zip(got["ids"], got["documents"], got["metadatas"]),
                   key=lambda item: _chunk_order(item[0]))
    sections: dict[str, list[str]] = {}
    for _id, doc, meta in items:
        heading = (meta or {}).get("section_heading") or "Content"
        sections.setdefault(heading, []).append(_CHUNK_PREFIX.sub("", doc or "", count=1))
    out = []
    for heading, parts in sections.items():
        text = _join_parts(parts).strip()
        out.append({"heading": heading, "text": text, "tokens": approx_tokens(text)})
    return out


def get_page(store: UserStore, page_id: str, content: bool = True) -> dict | None:
    if not store.db_path.exists():
        return None
    con = _connect(store)
    try:
        row = con.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    page = _with_freshness(row)
    if content:
        page["sections"] = page_sections(store, page_id)
        page["tokens"] = sum(s["tokens"] for s in page["sections"])
    return page


def library_tokens(store: UserStore) -> int:
    collection = _collection(store)
    if collection is None:
        return 0
    got = collection.get(include=["documents"])
    return sum(approx_tokens(d or "") for d in got["documents"])


def status(store: UserStore, recent: int = 10) -> dict[str, Any]:
    """Library overview: per-domain counts and freshness, totals, recent ingestions."""
    cfg = store.get_config()
    pages = list_pages(store)
    labels = {d.id: d.label for d in cfg.domains}

    domains: dict[str, dict] = {
        d.id: {"id": d.id, "label": d.label, "brain": d.brain, "icon": domain_icon(d.id, d.icon),
               "threshold_days": d.staleness_threshold_days,
               "pages": 0, "stale": 0, "ripening": 0, "last_fed": None}
        for d in cfg.domains
    }
    for page in pages:
        dom = domains.setdefault(page["domain"], {
            "id": page["domain"], "label": labels.get(page["domain"], page["domain"].title()),
            "brain": "", "icon": domain_icon(page["domain"]),
            "threshold_days": page["freshness"]["threshold_days"], "pages": 0, "stale": 0, "ripening": 0, "last_fed": None})
        dom["pages"] += 1
        level = page["freshness"]["level"]
        if level in ("stale", "ripening"):
            dom[level] += 1
        if page.get("updated_at") and (dom["last_fed"] is None or page["updated_at"] > dom["last_fed"]):
            dom["last_fed"] = page["updated_at"]

    ingestions: list[dict] = []
    if store.db_path.exists():
        con = _connect(store)
        try:
            ingestions = [dict(r) for r in con.execute(
                "SELECT connector, status, pages_created, pages_updated, error_msg, ran_at "
                "FROM ingestions ORDER BY ran_at DESC LIMIT ?", (recent,)).fetchall()]
        finally:
            con.close()

    return {
        "pages": len(pages),
        "stale": sum(1 for p in pages if p["freshness"]["level"] == "stale"),
        "ripening": sum(1 for p in pages if p["freshness"]["level"] == "ripening"),
        "library_tokens": library_tokens(store),
        "domains": list(domains.values()),
        "recent_ingestions": ingestions,
    }


def preview(store: UserStore, query: str, domains: list[str] | None = None,
            limit: int = 10) -> dict[str, Any]:
    """What an AI tool would be sent for `query` — retrieval only, no LLM call."""
    from mm.api.query import QueryEngine

    chunks = QueryEngine().retrieve(store, query, domains=domains, limit=limit)
    items = []
    for chunk in chunks:
        meta = chunk.get("metadata") or {}
        text = _CHUNK_PREFIX.sub("", chunk.get("text") or "", count=1)
        items.append({
            "page_id": meta.get("page_id", ""),
            "title": meta.get("title", ""),
            "domain": meta.get("domain", ""),
            "section": meta.get("section_heading", ""),
            "distance": chunk.get("distance"),
            "tokens": approx_tokens(text),
            "text": text,
            "freshness": freshness(meta.get("updated_at"), meta.get("staleness_threshold_days")),
        })
    sent = sum(i["tokens"] for i in items)
    total = library_tokens(store)
    return {
        "query": query,
        "chunks": items,
        "tokens_sent": sent,
        "library_tokens": total,
        "share": round(sent / total, 4) if total else 0.0,
    }
