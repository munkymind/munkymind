"""Re-ingesting syncs the library with its source folder."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock

import chromadb

from mm.connectors.files import FilesConnector
from mm.core.store import UserStore
from mm.ingestion.runner import run_connector


def _store(tmp_path: Path) -> UserStore:
    s = UserStore(data_root=tmp_path / "data", user_id="u1")
    s.init()
    return s


def _embed():
    m = MagicMock()
    m.embed.side_effect = lambda texts: [[0.1] * 8 for _ in texts]
    return m


def _run(store, folder, embed=None, **kw):
    return run_connector(FilesConnector(config={"path": str(folder)}, user_config=None), store, embed or _embed(), **kw)


def _pages(store):
    con = sqlite3.connect(store.db_path)
    try:
        return {Path(r[0]).stem: r[1] for r in con.execute("SELECT raw_path, updated_at FROM pages")}
    finally:
        con.close()


def _chunk_page_ids(store):
    col = chromadb.PersistentClient(path=str(store.chroma_dir)).get_or_create_collection(store.collection_name())
    return {m["page_id"] for m in col.get(include=["metadatas"])["metadatas"]}


def _write(folder: Path, name: str, text: str, mtime: float | None = None) -> Path:
    p = folder / name
    p.write_text(text)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def test_deleted_and_renamed_notes_leave_the_library(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    _write(notes, "keep.md", "# Keep\n\nStill here.")
    gone = _write(notes, "gone.md", "# Gone\n\nWill be deleted.")
    old = _write(notes, "old-name.md", "# Project\n\nRenamed later.")
    store = _store(tmp_path)
    _run(store, notes)
    assert set(_pages(store)) == {"keep", "gone", "old-name"}

    gone.unlink()
    old.rename(notes / "new-name.md")
    r = _run(store, notes)

    assert r["pages_removed"] == 2 and r["pages_created"] == 1 and r["pages_unchanged"] == 1
    assert set(_pages(store)) == {"keep", "new-name"}
    assert len(_chunk_page_ids(store)) == 2  # chunks of the deleted/renamed pages are gone too


def test_only_changed_notes_are_re_embedded(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    _write(notes, "a.md", "# A\n\nOne.")
    _write(notes, "b.md", "# B\n\nTwo.")
    store = _store(tmp_path)
    _run(store, notes)

    _write(notes, "b.md", "# B\n\nTwo, edited.")
    embed = _embed()
    r = _run(store, notes, embed)
    assert (r["pages_created"], r["pages_updated"], r["pages_unchanged"]) == (0, 1, 1)
    embedded = [t for call in embed.embed.call_args_list for t in call.args[0]]
    assert embedded and all("One." not in t for t in embedded)


def test_freshness_uses_file_modification_time(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    june = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))
    _write(notes, "old-note.md", "# Old\n\nUntouched since June.", mtime=june)
    store = _store(tmp_path)
    _run(store, notes)
    assert _pages(store)["old-note"].startswith("2026-06-15")


def test_empty_source_never_empties_the_library(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    _write(notes, "a.md", "# A\n\nOne.")
    store = _store(tmp_path)
    _run(store, notes)
    (notes / "a.md").unlink()  # e.g. vault unmounted / wrong folder: the source returns nothing
    r = _run(store, notes)
    assert r["pages_removed"] == 0 and set(_pages(store)) == {"a"}


def test_removals_stay_inside_the_connector_folder(tmp_path):
    work, home = tmp_path / "work", tmp_path / "home"
    work.mkdir()
    home.mkdir()
    _write(work, "w.md", "# W\n\nWork note.")
    _write(home, "h.md", "# H\n\nHome note.")
    store = _store(tmp_path)
    _run(store, work)
    _run(store, home)  # a different folder must not prune the work notes
    assert set(_pages(store)) == {"w", "h"}


def test_sync_false_is_additive(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    a = _write(notes, "a.md", "# A\n\nOne.")
    _write(notes, "b.md", "# B\n\nTwo.")
    store = _store(tmp_path)
    _run(store, notes)
    a.unlink()
    r = _run(store, notes, sync=False)
    assert r["pages_removed"] == 0 and set(_pages(store)) == {"a", "b"}
