"""Tests for UserStore, UserConfig, and auth keys."""
import sqlite3
import pytest
from pathlib import Path

from mm.core.store import UserStore
from mm.config.user import UserConfig
from mm.auth.keys import generate_key, verify_key


def test_user_store_init(tmp_path):
    store = UserStore(tmp_path, 'alice')
    store.init()
    assert store.user_dir.exists()
    assert store.context_dir.exists()
    assert store.chroma_dir.exists()
    assert store.db_path.parent.exists()
    assert store.db_path.exists()


def test_sqlite_schema(tmp_path):
    store = UserStore(tmp_path, 'alice')
    store.init()
    con = sqlite3.connect(store.db_path)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    con.close()
    assert {'pages', 'ingestions', 'auth_log'}.issubset(tables)


def test_collection_name(tmp_path):
    store = UserStore(tmp_path, 'alice')
    assert store.collection_name() == 'mm-alice'


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(ValueError):
        UserStore(tmp_path, '../evil')


def test_two_users_isolated(tmp_path):
    s1 = UserStore(tmp_path, 'alice')
    s2 = UserStore(tmp_path, 'bob')
    assert s1.db_path != s2.db_path
    assert s1.collection_name() != s2.collection_name()


def test_key_generate_verify():
    raw_key, hashed = generate_key()
    assert verify_key(raw_key, hashed) is True


def test_key_format():
    raw_key, _ = generate_key()
    assert raw_key.startswith('mm_sk_')


def test_user_config_default():
    cfg = UserConfig.default('alice')
    assert len(cfg.domains) == 6


def test_user_config_roundtrip(tmp_path):
    cfg = UserConfig.default('alice')
    path = tmp_path / 'config.yaml'
    cfg.save(path)
    cfg2 = UserConfig.load(path)
    assert cfg2.user_id == cfg.user_id
    assert len(cfg2.domains) == len(cfg.domains)
    assert cfg2.llm.provider == cfg.llm.provider
    assert cfg2.embedding.model == cfg.embedding.model
    assert cfg2.domains[0].id == cfg.domains[0].id


def test_seed_key_from_env_rotates_and_is_idempotent(tmp_path, monkeypatch, capsys):
    from mm.auth.keys import generate_key, load_key_hash, save_key_hash, seed_key_from_env, verify_key

    old, old_hash = generate_key()
    (tmp_path / "users" / "alice").mkdir(parents=True)
    save_key_hash(tmp_path / "users" / "alice", old_hash)
    (tmp_path / "users" / "tester").mkdir()
    save_key_hash(tmp_path / "users" / "tester", generate_key()[1])

    new, _ = generate_key()
    monkeypatch.setenv("MM_OSS_API_KEY", new)
    seed_key_from_env(tmp_path, "alice", "mm-api")
    stored = load_key_hash(tmp_path / "users" / "alice")
    assert verify_key(new, stored) and not verify_key(old, stored)
    out = capsys.readouterr().out
    assert "users with API keys: alice, tester" in out  # surfaces stray users
    assert "seeded" in out

    seed_key_from_env(tmp_path, "alice", "mm-api")
    assert load_key_hash(tmp_path / "users" / "alice") == stored  # no rewrite when in sync


def test_seed_key_from_env_ignores_missing_or_bad_key(tmp_path, monkeypatch):
    from mm.auth.keys import seed_key_from_env

    monkeypatch.setenv("MM_OSS_API_KEY", "not-a-key")
    seed_key_from_env(tmp_path, "alice", "mm-api")
    assert not (tmp_path / "users" / "alice" / "api_key.hash").exists()
