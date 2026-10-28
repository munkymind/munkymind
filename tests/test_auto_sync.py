"""Automatic sync: ingest runs only when a notes folder actually changed."""
from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock

import pytest
import yaml

from mm.core.store import UserStore
from mm.embedding.providers import EmbeddingProvider
from mm.ingestion import sync


@pytest.fixture
def lib(tmp_path, monkeypatch):
    data, notes = tmp_path / "data", tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("# A\nalpha")
    store = UserStore(data, "u1")
    store.init()
    store.config_path.write_text(yaml.safe_dump({
        "user_id": "u1", "connectors": [{"connector": "files", "path": str(notes)}]}))
    embed = MagicMock()
    embed.embed.side_effect = lambda texts: [[0.1] * 8 for _ in texts]
    monkeypatch.setattr(EmbeddingProvider, "from_config", staticmethod(lambda cfg: embed))
    return data, notes, embed


def test_first_pass_ingests_then_unchanged_folder_is_skipped(lib):
    data, notes, embed = lib
    first = sync.sync_user(data, "u1")
    assert first[0]["pages_created"] == 1
    assert sync.sync_user(data, "u1") == []          # nothing changed: no ingest at all
    assert embed.embed.call_count == 1


def test_edit_add_and_delete_are_picked_up(lib):
    data, notes, _ = lib
    sync.sync_user(data, "u1")
    (notes / "b.md").write_text("# B\nbeta")
    (notes / "a.md").write_text("# A\nalpha, edited")
    os.utime(notes / "a.md", (1, 1))                  # a different change time
    r = sync.sync_user(data, "u1")[0]
    assert (r["pages_created"], r["pages_updated"]) == (1, 1)
    (notes / "b.md").unlink()
    assert sync.sync_user(data, "u1")[0]["pages_removed"] == 1


def test_missing_folder_is_left_alone(lib):
    data, notes, _ = lib
    sync.sync_user(data, "u1")
    for f in notes.iterdir():
        f.unlink()
    notes.rmdir()                                     # e.g. the mount went away
    assert sync.sync_user(data, "u1") == []


def test_domain_mapping_change_resyncs(lib):
    data, notes, _ = lib
    sync.sync_user(data, "u1")
    store = UserStore(data, "u1")
    cfg = yaml.safe_load(store.config_path.read_text())
    cfg["connectors"][0]["domain_map"] = {"health": ["a"]}
    store.config_path.write_text(yaml.safe_dump(cfg))
    assert sync.sync_user(data, "u1")[0]["pages_updated"] == 1


def test_one_broken_user_does_not_stop_the_others(lib, monkeypatch):
    data, _, _ = lib
    (data / "users" / "broken").mkdir()
    (data / "users" / "broken" / "config.yaml").write_text("connectors: [")
    seen = []
    real = sync.sync_user
    monkeypatch.setattr(sync, "sync_user", lambda d, u, force=False: seen.append(u) or real(d, u, force))
    sync.sync_all(data)
    assert seen == ["broken", "u1"]


@pytest.mark.parametrize("value, expected", [("", 300), ("60", 60), ("0", 0), ("soon", 300)])
def test_interval(monkeypatch, value, expected):
    monkeypatch.setenv("MM_SYNC_INTERVAL", value)
    assert sync.interval() == expected


def test_off_starts_no_thread(monkeypatch, tmp_path):
    monkeypatch.setenv("MM_SYNC_INTERVAL", "0")
    before = threading.active_count()
    assert sync.start_background(tmp_path) is None
    assert threading.active_count() == before
