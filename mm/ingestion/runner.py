"""Ingestion runner: orchestrates connector → chunks → embed → store.

Re-running a connector syncs the library with its source:
- unchanged pages (same content hash) are skipped, so nothing is re-embedded;
- changed pages drop their old chunks before the new ones are written;
- pages whose source has gone (deleted or renamed files) are removed with their chunks,
  but only within this connector's own source folder, and never when the source
  returned nothing at all (a wrong or unmounted path must not empty the library);
- `updated_at` is the source's own change time (e.g. file mtime) where the connector
  provides it, so freshness reflects the note rather than the ingest.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path

import chromadb

from mm.connectors.base import BaseConnector, ConnectorPage
from mm.core import keyword_index
from mm.core.store import UserStore
from mm.embedding.pipeline import build_chunks, upsert_chunks
from mm.embedding.providers import EmbeddingProvider


def content_hash(page: ConnectorPage) -> str:
    """Hash of everything that ends up in the chunks or the page row."""
    payload = {
        "title": page.title, "domain": page.domain, "type": page.type, "summary": page.summary,
        "sections": page.detail_sections, "tags": sorted(page.tags) if isinstance(page.tags, list) else page.tags,
        "confidence": page.confidence, "staleness": page.staleness_threshold_days,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _sync_root(connector: BaseConnector) -> str | None:
    """The folder this connector owns; removals are limited to pages under it."""
    path = (connector.config or {}).get("path")
    if not path:
        return None
    try:
        return str(Path(path).resolve())
    except OSError:
        return None


def _under(raw_path: str | None, root: str) -> bool:
    if not raw_path:
        return False
    try:
        p = str(Path(raw_path).resolve())
    except OSError:
        return False
    return p == root or p.startswith(root.rstrip("/") + "/")


def run_connector(
    connector: BaseConnector,
    user_store: UserStore,
    embed_provider: EmbeddingProvider,
    dry_run: bool = False,
    sync: bool = True,
) -> dict:
    """
    Run a connector and ingest its output.

    Steps:
    1. connector.validate() — raise if invalid
    2. connector.ingest() — get list[ConnectorPage]
    3. compare content hashes with the library — skip unchanged pages
    4. build_chunks + embed only new/changed pages; clear old chunks of changed pages
    5. upsert pages to SQLite (updated_at = source change time when known)
    6. sync=True: remove pages (and chunks) under this connector's folder that the source no longer has
    7. Record ingestion in SQLite ingestions table

    `sync=False` keeps the run additive (e.g. one-off uploads from a temporary folder).

    Returns: {pages_created, pages_updated, pages_unchanged, pages_removed, chunks_total, status}
    """
    ok, msg = connector.validate()
    if not ok:
        raise ValueError(f"Connector validation failed: {msg}")

    # One ingest per library at a time: automatic sync, the CLI and uploads can overlap
    with _ingest_lock(user_store):
        return _run(connector, user_store, embed_provider, dry_run, sync)


@contextlib.contextmanager
def _ingest_lock(user_store: UserStore):
    try:
        import fcntl
    except ImportError:  # Windows without Docker: no cross-process lock
        yield
        return
    user_store.user_dir.mkdir(parents=True, exist_ok=True)
    with open(user_store.user_dir / ".ingest.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _run(connector, user_store, embed_provider, dry_run, sync) -> dict:
    pages: list[ConnectorPage] = connector.ingest()
    hashes = {p.id: content_hash(p) for p in pages}

    known: dict[str, str | None] = {}
    if user_store.db_path.exists():
        con = sqlite3.connect(user_store.db_path)
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(pages)")}
            if cols:
                hcol = "content_hash" if "content_hash" in cols else "NULL"
                known = {r[0]: r[1] for r in con.execute(f"SELECT id, {hcol} FROM pages")}
        finally:
            con.close()

    new = [p for p in pages if p.id not in known]
    changed = [p for p in pages if p.id in known and known[p.id] != hashes[p.id]]
    unchanged = [p for p in pages if p.id in known and known[p.id] == hashes[p.id]]
    chunks = build_chunks(new + changed)

    removed: list[str] = []
    root = _sync_root(connector) if sync else None

    if not dry_run:
        client = chromadb.PersistentClient(path=str(user_store.chroma_dir))
        collection = client.get_or_create_collection(user_store.collection_name())
        con = sqlite3.connect(user_store.db_path)
        try:
            if root and pages:
                current = {p.id for p in pages}
                rows = con.execute("SELECT id, raw_path FROM pages WHERE connector = ?",
                                   (connector.connector_id,)).fetchall()
                removed = [pid for pid, raw in rows if pid not in current and _under(raw, root)]

            # Old chunks of changed/removed pages go first, so dropped sections don't linger
            stale_ids = [p.id for p in changed] + removed
            for i in range(0, len(stale_ids), 100):
                collection.delete(where={"page_id": {"$in": stale_ids[i:i + 100]}})
            keyword_index.delete_pages(user_store.db_path, stale_ids)
            if chunks:
                upsert_chunks(chunks, collection, embed_provider.embed)
                keyword_index.upsert(user_store.db_path, chunks)

            now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None).isoformat()
            for page in new + changed:
                tags_str = ",".join(page.tags) if isinstance(page.tags, list) else str(page.tags)
                updated = page.source_updated_at or now
                if page.id in known:
                    con.execute(
                        """UPDATE pages SET domain=?, type=?, title=?, source=?,
                           connector=?, updated_at=?, staleness_threshold_days=?,
                           confidence=?, tags=?, raw_path=?, content_hash=? WHERE id=?""",
                        (
                            page.domain, page.type, page.title, page.source,
                            page.connector, updated, page.staleness_threshold_days,
                            page.confidence, tags_str, page.source_ref, hashes[page.id], page.id,
                        ),
                    )
                else:
                    con.execute(
                        """INSERT INTO pages
                           (id, domain, type, title, source, connector,
                            created_at, updated_at, staleness_threshold_days,
                            confidence, tags, raw_path, content_hash)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            page.id, page.domain, page.type, page.title,
                            page.source, page.connector, now, updated,
                            page.staleness_threshold_days, page.confidence,
                            tags_str, page.source_ref, hashes[page.id],
                        ),
                    )
            for pid in removed:
                con.execute("DELETE FROM pages WHERE id = ?", (pid,))

            con.execute(
                """INSERT INTO ingestions
                   (connector, source_ref, status, pages_created, pages_updated, ran_at)
                   VALUES (?,?,?,?,?,?)""",
                (connector.connector_id, connector.config.get("path", ""), "ok", len(new), len(changed), now),
            )
            con.commit()
        finally:
            con.close()

    return {
        "pages_created": len(new),
        "pages_updated": len(changed),
        "pages_unchanged": len(unchanged),
        "pages_removed": len(removed),
        "chunks_total": len(chunks),
        "status": "ok",
    }
