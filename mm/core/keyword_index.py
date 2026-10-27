"""Keyword index over chunk text (SQLite FTS5), used alongside vector search.

Embeddings miss exact names and terms (a project name mentioned dozens of times can
still lose to unrelated text), so retrieval also asks this index. It lives in the
user's metadata database and is kept in step with the vector store by the
ingestion runner; libraries created before it existed are backfilled on first use.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, page_id UNINDEXED, domain UNINDEXED, text,
    tokenize = 'porter unicode61'
)
"""
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does", "for", "from", "has", "have",
    "how", "i", "in", "is", "it", "last", "me", "my", "of", "on", "or", "the", "thing", "this", "to",
    "was", "what", "when", "where", "which", "who", "why", "with", "you", "your",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.execute(_SCHEMA)
    return con


def upsert(db_path: Path, chunks) -> None:
    """Write chunks (objects with .id, .text, .metadata) to the index, replacing older copies."""
    if not chunks:
        return
    con = _connect(db_path)
    try:
        for c in chunks:
            con.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (c.id,))
            con.execute("INSERT INTO chunks_fts (chunk_id, page_id, domain, text) VALUES (?,?,?,?)",
                        (c.id, c.metadata.get("page_id", ""), c.metadata.get("domain", ""), c.text))
        con.commit()
    finally:
        con.close()


def delete_pages(db_path: Path, page_ids: list[str]) -> None:
    if not page_ids:
        return
    con = _connect(db_path)
    try:
        con.executemany("DELETE FROM chunks_fts WHERE page_id = ?", [(p,) for p in page_ids])
        con.commit()
    finally:
        con.close()


def count(db_path: Path) -> int:
    con = _connect(db_path)
    try:
        return con.execute("SELECT count(*) FROM chunks_fts").fetchone()[0]
    finally:
        con.close()


def backfill(db_path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    """rows: (chunk_id, page_id, domain, text) — for libraries built before this index existed."""
    con = _connect(db_path)
    try:
        con.executemany("INSERT INTO chunks_fts (chunk_id, page_id, domain, text) VALUES (?,?,?,?)", rows)
        con.commit()
    finally:
        con.close()


def match_expression(query: str) -> str | None:
    """Turn a question into an FTS5 OR-query of its meaningful terms (quoted, so no syntax leaks)."""
    terms = [t for t in re.findall(r"[\w][\w'-]*", query.lower()) if len(t) > 1 and t not in _STOPWORDS]
    terms = list(dict.fromkeys(terms))[:12]
    if not terms:
        return None
    return " OR ".join('"' + t.replace('"', "") + '"' for t in terms)


def search(db_path: Path, query: str, limit: int, domains: list[str] | None = None) -> list[str]:
    """Chunk ids ranked by BM25 relevance."""
    expr = match_expression(query)
    if not expr:
        return []
    con = _connect(db_path)
    try:
        sql = "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?"
        params: list = [expr]
        if domains:
            sql += f" AND domain IN ({','.join('?' * len(domains))})"
            params += list(domains)
        sql += " ORDER BY bm25(chunks_fts) LIMIT ?"
        params.append(limit)
        try:
            return [r[0] for r in con.execute(sql, params)]
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()
