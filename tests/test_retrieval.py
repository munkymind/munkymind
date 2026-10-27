"""Hybrid retrieval: keyword + vector, relevance cut-off, duplicate removal."""
from __future__ import annotations

import hashlib
import math
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mm.api.query import QueryEngine, _dedupe, max_distance
from mm.config.user import UserConfig
from mm.connectors.files import FilesConnector
from mm.core import keyword_index
from mm.core.store import UserStore
from mm.ingestion.runner import run_connector

DIM = 64


def _vec(text: str) -> list[float]:
    v = [0.0] * DIM
    for w in text.lower().split():
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture
def lib(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "zeta.md").write_text("# Zeta rollout\n\nZeta migration finished. Next for Zeta: cut over billing.")
    (notes / "garden.md").write_text("# Garden\n\nPlant tomatoes and basil in spring, water daily.")
    (notes / "recipes.md").write_text("# Recipes\n\nSlow-cooked lamb with rosemary and garlic.")
    store = UserStore(data_root=tmp_path / "data", user_id="u1")
    store.init()
    UserConfig.default("u1").save(store.config_path)
    embed = MagicMock()
    embed.embed.side_effect = lambda texts: [_vec(t) for t in texts]
    run_connector(FilesConnector(config={"path": str(notes)}, user_config=None), store, embed)
    with patch("mm.embedding.providers.EmbeddingProvider.from_config", return_value=embed):
        yield store, notes, embed


def _titles(chunks):
    return {Path(c["metadata"].get("source_ref") or c["metadata"].get("path", "")).stem or c["metadata"].get("title")
            for c in chunks}


def test_name_query_finds_the_note_by_keyword(lib, monkeypatch):
    store, _, _ = lib
    monkeypatch.setenv("MM_MAX_DISTANCE", "0.01")  # vector side finds nothing close enough
    chunks = QueryEngine().retrieve(store, "what was the last thing I did for Zeta?", limit=5)
    assert chunks and all("zeta" in c["text"].lower() for c in chunks)
    assert {c["match"] for c in chunks} == {"keyword"}


def test_nothing_relevant_returns_empty(lib, monkeypatch):
    store, _, _ = lib
    monkeypatch.setenv("MM_MAX_DISTANCE", "0.5")
    assert QueryEngine().retrieve(store, "quantum chromodynamics lattice", limit=5) == []


def test_close_vector_match_without_keywords_still_returned(lib, monkeypatch):
    store, _, _ = lib
    monkeypatch.setenv("MM_MAX_DISTANCE", "2.0")
    chunks = QueryEngine().retrieve(store, "zzzz", limit=3)  # no keyword hit; generous cut-off keeps vectors
    assert chunks and all(c["match"] == "vector" for c in chunks)


def test_keyword_index_backfilled_for_older_libraries(lib, monkeypatch):
    store, _, _ = lib
    con = sqlite3.connect(store.db_path)
    con.execute("DELETE FROM chunks_fts")
    con.commit()
    con.close()
    monkeypatch.setenv("MM_MAX_DISTANCE", "0.01")
    assert QueryEngine().retrieve(store, "Zeta billing", limit=3)
    assert keyword_index.count(store.db_path) > 0


def test_deleted_note_leaves_keyword_index(lib):
    store, notes, embed = lib
    (notes / "zeta.md").unlink()
    run_connector(FilesConnector(config={"path": str(notes)}, user_config=None), store, embed)
    assert keyword_index.search(store.db_path, "Zeta", 10) == []


def test_dedupe_drops_summary_inside_a_returned_section():
    section = {"text": "Zeta migration finished.   Next: billing.", "metadata": {"page_id": "p1", "chunk_type": "detail"}}
    summary = {"text": "Zeta migration finished.", "metadata": {"page_id": "p1", "chunk_type": "summary"}}
    other = {"text": "Zeta migration finished.", "metadata": {"page_id": "p2", "chunk_type": "summary"}}
    assert _dedupe([summary, section, other]) == [section]  # p2 is identical text, so dropped as a repeat


def test_default_cut_off_only_for_openai(monkeypatch):
    monkeypatch.delenv("MM_MAX_DISTANCE", raising=False)
    assert max_distance("openai") == 1.3 and max_distance("ollama") is None
    monkeypatch.setenv("MM_MAX_DISTANCE", "0.9")
    assert max_distance("ollama") == 0.9


def test_match_expression_quotes_every_term():
    expr = keyword_index.match_expression('what is "zeta" NEAR(x) OR billing* --')
    parts = expr.split(" OR ")
    assert all(p.startswith('"') and p.endswith('"') and p.count('"') == 2 for p in parts)
    assert '"zeta"' in parts and '"billing"' in parts
    assert keyword_index.match_expression("what is the") is None


def test_hostile_query_does_not_break_search(lib):
    store, _, _ = lib
    assert keyword_index.search(store.db_path, 'zeta") OR chunks_fts MATCH ("*', 5)
