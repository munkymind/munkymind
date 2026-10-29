"""`munkymind doctor`: each check passes and fails as it should, with a fix, never printing keys."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from mm.auth.keys import KeyPolicy, issue_key, load_key_hash, save_key_hash
from mm.cli import doctor
from mm.config.user import UserConfig
from mm.core.store import UserStore

runner = CliRunner()
OPENAI_KEY = "sk-test-doctor-0123456789abcdefghij"


def _resp(code=200, body=None):
    r = MagicMock()
    r.status_code, r.ok = code, code < 400
    r.json.return_value = body or {}
    return r


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A healthy Docker-style install: one user, notes, an indexed library, every service up."""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("# A\n")
    (notes / "b.md").write_text("# B\n")
    store = UserStore(tmp_path, "tester")
    store.init()
    cfg = UserConfig.default("tester")
    cfg.connectors = [{"connector": "files", "path": str(notes)}]
    cfg.llm.provider, cfg.llm.model = "openai", "gpt-4o-mini"
    cfg.embedding.provider, cfg.embedding.model = "openai", "text-embedding-3-small"
    cfg.save(store.config_path)
    con = sqlite3.connect(store.db_path)
    con.execute("INSERT INTO pages (id, domain, type, title, source, connector, created_at, updated_at) "
                "VALUES ('p1','work','note','A','files','files','2026-10-28','2026-10-28')")
    con.execute("INSERT INTO ingestions (connector, source_ref, status, pages_created, pages_updated, ran_at) "
                "VALUES ('files', '', 'ok', 1, 0, ?)", (dt.datetime.now(dt.UTC).isoformat(),))
    con.commit()
    con.close()
    raw_key = issue_key(store.user_dir)

    monkeypatch.setattr(doctor, "in_docker", lambda: True)
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    monkeypatch.setenv("MM_USER_ID", "tester")
    monkeypatch.delenv("MM_SYNC_INTERVAL", raising=False)
    monkeypatch.setattr("mm.cli.main.DATA_ROOT", tmp_path)
    calls = []
    answers = {}

    def fake_get(url, headers=None):
        calls.append((url, headers))
        for prefix, r in answers.items():
            if url.startswith(prefix):
                if isinstance(r, Exception):
                    raise r
                return r
        return _resp(200)

    monkeypatch.setattr(doctor, "_get", fake_get)
    return {"root": tmp_path, "store": store, "notes": notes, "answers": answers, "calls": calls, "key": raw_key}


def _find(checks, text):
    return next(c for c in checks if text.lower() in c.label.lower())


def test_healthy_install_passes(env):
    checks = doctor.run(env["root"], "tester")
    bad = [c for c in checks if c.status in (doctor.FAIL, doctor.WARN)]
    assert not bad, bad
    assert _find(checks, "notes folder").label.endswith("2 notes")
    assert "1 page" in _find(checks, "library").label
    assert _find(checks, "automatic sync").status == doctor.OK


def test_api_down_fails_with_fix(env):
    env["answers"]["http://localhost"] = ConnectionError("refused")
    c = doctor.check_api()
    assert c.status == doctor.FAIL and "docker compose up -d" in c.fix


def test_mcp_down_and_wrong_user(env, monkeypatch):
    env["answers"]["http://mcp:8001"] = ConnectionError("refused")
    env["answers"]["http://localhost:8001"] = ConnectionError("refused")
    monkeypatch.setenv("MM_USER_ID", "someone-else")
    checks = doctor.check_mcp("tester")
    assert checks[0].status == doctor.FAIL and "up -d mcp" in checks[0].fix
    assert checks[1].status == doctor.FAIL and "MM_USER_ID=tester" in checks[1].fix


def test_expired_and_missing_key(env):
    user_dir = env["store"].user_dir
    past = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    save_key_hash(user_dir, load_key_hash(user_dir), KeyPolicy(expires_at=past, created_at=past - dt.timedelta(days=30)))
    c = doctor.check_api_key(env["store"])
    assert c.status == doctor.FAIL and "expired" in c.label and "rotate-key tester" in c.fix
    (user_dir / "api_key.hash").unlink()
    assert doctor.check_api_key(env["store"]).status == doctor.FAIL


def test_key_expiring_soon_warns(env):
    issue_key(env["store"].user_dir, expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=3))
    assert doctor.check_api_key(env["store"]).status == doctor.WARN


def test_notes_folder_missing_or_empty(env):
    cfg = UserConfig.load(env["store"].config_path)
    for f in env["notes"].iterdir():
        f.unlink()
    assert doctor.check_notes(cfg)[0].status == doctor.WARN
    env["notes"].rmdir()
    c = doctor.check_notes(cfg)[0]
    assert c.status == doctor.FAIL and "MM_NOTES_DIR" in c.fix


def test_empty_library_and_failed_ingest(env):
    con = sqlite3.connect(env["store"].db_path)
    con.execute("DELETE FROM pages")
    con.execute("INSERT INTO ingestions (connector, source_ref, status, pages_created, pages_updated, ran_at) "
                "VALUES ('files', '', 'error', 0, 0, '2026-10-28T00:00:00')")
    con.commit()
    con.close()
    checks = doctor.check_library(env["store"])
    assert _find(checks, "empty").status == doctor.FAIL
    assert _find(checks, "failed").status == doctor.FAIL


def test_sync_off_warns(env, monkeypatch):
    monkeypatch.setenv("MM_SYNC_INTERVAL", "0")
    assert _find(doctor.check_library(env["store"]), "sync").status == doctor.WARN


def test_rejected_key_fails_and_key_never_printed(env):
    env["answers"]["https://api.openai.com"] = _resp(401)
    res = runner.invoke(_app(), ["doctor", "--user", "tester"])
    assert res.exit_code == 1, res.output
    assert "key rejected (401)" in res.output and "→ Fix:" in res.output
    assert OPENAI_KEY not in res.output and env["key"] not in res.output


def test_missing_openai_key(env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    c = doctor.check_providers(UserConfig.load(env["store"].config_path))[0]
    assert c.status == doctor.FAIL and "OPENAI_API_KEY" in c.fix


def test_one_call_when_both_roles_share_a_key(env):
    doctor.check_providers(UserConfig.load(env["store"].config_path))
    assert sum("api.openai.com" in u for u, _ in env["calls"]) == 2  # answers + search models differ
    cfg = UserConfig.load(env["store"].config_path)
    cfg.embedding.model = cfg.llm.model
    env["calls"].clear()
    doctor.check_providers(cfg)
    assert sum("api.openai.com" in u for u, _ in env["calls"]) == 1


def test_ollama_model_not_pulled(env):
    env["answers"]["http://"] = _resp(200, {"models": [{"name": "llama3:latest"}]})
    c = doctor._key_check("Answers", "ollama", "qwen2.5")
    assert c.status == doctor.FAIL and c.fix == "ollama pull qwen2.5"


def test_unknown_user(env):
    checks = doctor.run(env["root"], "nobody")
    assert checks[-1].status == doctor.FAIL and "tester" in checks[-1].label


def test_claude_desktop_config(env, monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "in_docker", lambda: False)
    cfg = tmp_path / "claude_desktop_config.json"
    monkeypatch.setattr(doctor, "_claude_desktop_configs", lambda: [cfg])
    assert doctor.check_claude_desktop("tester").status == doctor.SKIP
    cfg.write_text(json.dumps({"mcpServers": {}}))
    assert doctor.check_claude_desktop("tester").status == doctor.FAIL
    entry = {"command": "docker", "args": ["compose", "exec", "-T", "-e", "USER_ID=tester", "mcp"]}
    cfg.write_text(json.dumps({"mcpServers": {"munkymind": entry}}))
    assert doctor.check_claude_desktop("tester").status == doctor.OK
    assert doctor.check_claude_desktop("other").status == doctor.FAIL
    # An older local install: another name, python launcher, USER_ID in env
    old = {"command": "python", "args": ["-m", "mm.mcp.server"], "env": {"USER_ID": "tester"}}
    cfg.write_text(json.dumps({"mcpServers": {"monkey-mind": old}}))
    assert doctor.check_claude_desktop("tester").status == doctor.OK
    assert doctor.check_claude_desktop("other").status == doctor.FAIL


def test_cli_healthy_exit_zero_and_default_user(env, monkeypatch):
    monkeypatch.delenv("MM_USER_ID")
    res = runner.invoke(_app(), ["doctor"])
    assert res.exit_code == 0, res.output
    assert "Checking Munkymind for 'tester'" in res.output
    assert "Everything's working." in res.output


def _app():
    from mm.cli.main import app
    return app
