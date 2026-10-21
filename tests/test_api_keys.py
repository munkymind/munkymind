"""API key scope, expiry and rotation (REST auth, MCP auth and CLI)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mm.auth import keys
from mm.auth.keys import (
    SCOPE_READ,
    SCOPE_READ_WRITE,
    KeyPolicy,
    check_key,
    generate_key,
    issue_key,
    load_key_policy,
    parse_duration,
    parse_expiry,
    save_key_hash,
)

UTC = dt.UTC


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _expire_now(user_dir: Path) -> None:
    """Move a key's expiry into the past (issue_key refuses past dates)."""
    pol = load_key_policy(user_dir)
    past = _now() - dt.timedelta(minutes=1)
    save_key_hash(user_dir, keys.load_key_hash(user_dir),
                  KeyPolicy(pol.scope, past, past - dt.timedelta(days=1)))


def _legacy_user(data_root: Path, name: str) -> str:
    """A user as created before key policies: a hash file and nothing else."""
    raw, hashed = generate_key()
    user_dir = data_root / "users" / name
    user_dir.mkdir(parents=True)
    (user_dir / "api_key.hash").write_text(hashed)
    return raw


# ------------------------------------------------------------------ #
# keys module
# ------------------------------------------------------------------ #


class TestKeyPolicy:
    def test_legacy_key_without_policy_is_read_write_and_never_expires(self, tmp_path):
        raw = _legacy_user(tmp_path, "old")
        check = check_key(raw, tmp_path / "users" / "old")
        assert check.valid
        assert check.policy.scope == SCOPE_READ_WRITE and check.policy.can_write
        assert check.policy.expires_at is None

    def test_new_key_records_scope_expiry_and_creation(self, tmp_path):
        when = _now() + dt.timedelta(days=30)
        issue_key(tmp_path, SCOPE_READ, when)
        data = json.loads((tmp_path / "api_key.json").read_text())
        assert data["scope"] == "read"
        assert dt.datetime.fromisoformat(data["expires_at"]) == when
        assert data["created_at"]
        assert "mm_sk_" not in (tmp_path / "api_key.json").read_text()

    def test_unexpired_key_is_valid(self, tmp_path):
        raw = issue_key(tmp_path, expires_at=_now() + dt.timedelta(hours=1))
        assert check_key(raw, tmp_path).valid

    def test_expired_key_is_rejected(self, tmp_path):
        raw = issue_key(tmp_path, expires_at=_now() + dt.timedelta(hours=1))
        _expire_now(tmp_path)
        check = check_key(raw, tmp_path)
        assert check.matched and check.expired and not check.valid

    def test_no_expiry_key_is_valid(self, tmp_path):
        raw = issue_key(tmp_path)
        assert check_key(raw, tmp_path).valid

    def test_wrong_key_does_not_match(self, tmp_path):
        issue_key(tmp_path)
        assert not check_key(generate_key()[0], tmp_path).matched
        assert not check_key("not-a-key", tmp_path).matched

    def test_unreadable_policy_fails_closed(self, tmp_path):
        raw = issue_key(tmp_path)
        (tmp_path / "api_key.json").write_text('{"scope": "admin"}')
        check = check_key(raw, tmp_path)
        assert check.matched and not check.valid
        (tmp_path / "api_key.json").write_text("not json")
        assert not check_key(raw, tmp_path).valid

    def test_issue_key_rejects_past_expiry_and_bad_scope(self, tmp_path):
        with pytest.raises(ValueError):
            issue_key(tmp_path, expires_at=_now() - dt.timedelta(seconds=1))
        with pytest.raises(ValueError):
            issue_key(tmp_path, scope="admin")

    def test_revoke_removes_key_and_policy(self, tmp_path):
        raw = issue_key(tmp_path, SCOPE_READ)
        assert keys.revoke_key(tmp_path) is True
        assert not check_key(raw, tmp_path).matched
        assert not (tmp_path / "api_key.json").exists()
        assert keys.revoke_key(tmp_path) is False

    @pytest.mark.parametrize("text,expected", [
        ("12h", dt.timedelta(hours=12)), ("30d", dt.timedelta(days=30)),
        ("8w", dt.timedelta(weeks=8)), ("1D", dt.timedelta(days=1)),
    ])
    def test_parse_duration(self, text, expected):
        assert parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["", "30", "0d", "-1d", "3m", "d", "1.5d"])
    def test_parse_duration_rejects(self, text):
        with pytest.raises(ValueError):
            parse_duration(text)

    def test_parse_expiry(self):
        assert parse_expiry("2030-01-02") == dt.datetime(2030, 1, 2, tzinfo=UTC)
        assert parse_expiry("2030-01-02T10:00:00+02:00") == dt.datetime(2030, 1, 2, 8, tzinfo=UTC)
        with pytest.raises(ValueError):
            parse_expiry("next tuesday")


# ------------------------------------------------------------------ #
# REST API (real auth dependency)
# ------------------------------------------------------------------ #


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from mm.api import server
    from mm.core.store import UserStore

    monkeypatch.setattr(server, "DATA_ROOT", tmp_path)
    server.app.dependency_overrides.clear()

    def make_user(name: str, scope: str = SCOPE_READ_WRITE, expires_at=None) -> str:
        store = UserStore(tmp_path, name)
        store.init()
        store.get_config()
        return issue_key(store.user_dir, scope, expires_at)

    return TestClient(server.app), make_user, tmp_path


def _auth_results(data_root: Path, name: str) -> list[str]:
    import sqlite3

    con = sqlite3.connect(data_root / "users" / name / "db" / "metadata.sqlite3")
    rows = [r[0] for r in con.execute("SELECT result FROM auth_log ORDER BY id")]
    con.close()
    return rows


class TestRestAuth:
    def test_read_write_key_reads(self, api):
        tc, make_user, _ = api
        key = make_user("alice")
        assert tc.get("/domains", headers={"X-API-Key": key}).status_code == 200

    def test_legacy_key_still_reads_and_writes(self, api):
        tc, _, root = api
        from mm.core.store import UserStore

        store = UserStore(root, "old")
        store.init()
        store.get_config()
        raw, hashed = generate_key()
        (store.user_dir / "api_key.hash").write_text(hashed)  # no api_key.json
        assert tc.get("/domains", headers={"X-API-Key": raw}).status_code == 200
        with patch("mm.ingestion.runner.run_connector", return_value={"pages": 0}), \
                patch("mm.embedding.providers.EmbeddingProvider.from_config"):
            resp = tc.post("/ingest/files", headers={"X-API-Key": raw},
                           files=[("files", ("a.md", b"# A\n", "text/markdown"))])
        assert resp.status_code == 200, resp.text

    def test_expired_key_gets_401_with_clear_message(self, api):
        tc, make_user, root = api
        key = make_user("alice", expires_at=_now() + dt.timedelta(hours=1))
        _expire_now(root / "users" / "alice")
        resp = tc.get("/domains", headers={"X-API-Key": key})
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()
        assert "rotate-key" in resp.json()["detail"]
        assert _auth_results(root, "alice")[-1] == "expired"

    def test_unexpired_key_works(self, api):
        tc, make_user, _ = api
        key = make_user("alice", expires_at=_now() + dt.timedelta(hours=1))
        assert tc.get("/status", headers={"X-API-Key": key}).status_code == 200

    def test_read_only_key_reads_pages_status_and_domains(self, api):
        tc, make_user, _ = api
        key = make_user("bot", SCOPE_READ)
        for path in ("/domains", "/pages", "/status", "/pages/nope"):
            assert tc.get(path, headers={"X-API-Key": key}).status_code in (200, 404), path

    def test_read_only_key_can_query_and_preview(self, api):
        tc, make_user, _ = api
        key = make_user("bot", SCOPE_READ)
        engine = MagicMock()
        engine.retrieve.return_value = []
        engine.synthesise.return_value = {"answer": "ok", "sources": []}
        with patch("mm.api.query.QueryEngine", return_value=engine):
            resp = tc.post("/query", json={"query": "hi"}, headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text
        with patch("mm.core.library.preview", return_value={"chunks": []}):
            resp = tc.post("/preview", json={"query": "hi"}, headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text

    def test_read_only_key_cannot_ingest(self, api):
        tc, make_user, root = api
        key = make_user("bot", SCOPE_READ)
        with patch("mm.ingestion.runner.run_connector") as run:
            resp = tc.post("/ingest/files", headers={"X-API-Key": key},
                           files=[("files", ("a.md", b"# A\n", "text/markdown"))])
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"]
        run.assert_not_called()
        assert _auth_results(root, "bot")[-1] == "forbidden"

    def test_rotation_invalidates_old_key(self, api):
        tc, make_user, root = api
        old = make_user("alice")
        new = issue_key(root / "users" / "alice")
        assert tc.get("/domains", headers={"X-API-Key": old}).status_code == 401
        assert tc.get("/domains", headers={"X-API-Key": new}).status_code == 200

    def test_bootstrap_stays_closed_after_revoke(self, api, monkeypatch):
        tc, make_user, root = api
        monkeypatch.setenv("USER_ID", "alice")
        make_user("alice")
        keys.revoke_key(root / "users" / "alice")
        assert tc.post("/bootstrap").status_code == 409


# Non-GET routes that need no key at all. Anything here must justify itself.
PUBLIC_WRITE_ROUTES = {"/bootstrap"}


def _depends_on(dependant, target) -> bool:
    return any(d.call is target or _depends_on(d, target) for d in dependant.dependencies)


def _non_get_routes():
    from mm.api.server import app

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods - {"GET", "HEAD", "OPTIONS"}):
            yield route, method


def test_every_non_get_route_requires_write_scope(api):
    """Walk the app: every non-GET route must authenticate, and a read-only key
    must get 403 on it unless the route is explicitly allow-listed as read-only.
    A new mutating route can't skip the scope check without failing here."""
    from mm.api.server import READ_SCOPE_POST_ROUTES, get_user_store

    tc, make_user, _ = api
    key = make_user("bot", SCOPE_READ)
    routes = list(_non_get_routes())
    assert routes, "expected some non-GET routes"
    assert READ_SCOPE_POST_ROUTES <= {r.path for r, _ in routes}, "stale allow-list entry"

    checked = set()
    for route, method in routes:
        if route.path in PUBLIC_WRITE_ROUTES:
            continue
        assert _depends_on(route.dependant, get_user_store), f"{method} {route.path} has no auth"
        if route.path in READ_SCOPE_POST_ROUTES:
            continue
        url = route.path_format.replace("{", "").replace("}", "")  # fill path params
        resp = tc.request(method, url, headers={"X-API-Key": key})
        assert resp.status_code == 403, f"{method} {route.path} allowed a read-only key"
        checked.add(route.path)
    assert "/ingest/files" in checked


def test_scope_rule_matches_the_allow_list():
    """The scope rule itself: only listed routes are open to read-only keys."""
    from mm.api.server import READ_SCOPE_POST_ROUTES, requires_write

    for route, method in _non_get_routes():
        assert requires_write(method, route.path) == (route.path not in READ_SCOPE_POST_ROUTES)
    assert not requires_write("GET", "/pages")


# ------------------------------------------------------------------ #
# MCP OAuth layer
# ------------------------------------------------------------------ #


class TestMcpAuth:
    def test_expired_key_cannot_use_mcp(self, tmp_path):
        from mm.mcp.auth import _verify_key

        raw = issue_key(tmp_path / "users" / "alice", expires_at=_now() + dt.timedelta(hours=1))
        assert _verify_key(raw, tmp_path, "alice")
        _expire_now(tmp_path / "users" / "alice")
        assert not _verify_key(raw, tmp_path, "alice")

    def test_read_only_and_legacy_keys_can_use_mcp(self, tmp_path):
        from mm.mcp.auth import _verify_key

        raw = issue_key(tmp_path / "users" / "bot", SCOPE_READ)
        assert _verify_key(raw, tmp_path, "bot")
        legacy = _legacy_user(tmp_path, "old")
        assert _verify_key(legacy, tmp_path, "old")


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #

runner = CliRunner()


@pytest.fixture()
def cli_root(monkeypatch, tmp_path):
    monkeypatch.setattr("mm.cli.main.DATA_ROOT", tmp_path)
    return tmp_path


def _printed_key(output: str) -> str:
    return next(w for w in output.split() if w.startswith("mm_sk_"))


class TestCli:
    def _app(self):
        from mm.cli.main import app
        return app

    def test_create_read_only_with_expiry(self, cli_root):
        res = runner.invoke(self._app(), ["user", "create", "bot", "--read-only", "--expires-in", "30d"])
        assert res.exit_code == 0, res.output
        assert "read-only" in res.output
        pol = load_key_policy(cli_root / "users" / "bot")
        assert pol.scope == SCOPE_READ
        assert abs(pol.lifetime - dt.timedelta(days=30)) < dt.timedelta(seconds=5)
        assert check_key(_printed_key(res.output), cli_root / "users" / "bot").valid

    def test_create_with_expires_at(self, cli_root):
        res = runner.invoke(self._app(), ["user", "create", "bot", "--expires-at", "2099-01-01"])
        assert res.exit_code == 0, res.output
        assert load_key_policy(cli_root / "users" / "bot").expires_at == dt.datetime(2099, 1, 1, tzinfo=UTC)

    @pytest.mark.parametrize("args", [
        ["--expires-in", "soon"], ["--expires-at", "2001-01-01"],
        ["--expires-in", "1d", "--expires-at", "2099-01-01"],
    ])
    def test_create_rejects_bad_expiry(self, cli_root, args):
        res = runner.invoke(self._app(), ["user", "create", "bot", *args])
        assert res.exit_code != 0
        assert not (cli_root / "users" / "bot").exists()

    def test_default_create_is_read_write_no_expiry(self, cli_root):
        res = runner.invoke(self._app(), ["user", "create", "alice"])
        assert res.exit_code == 0, res.output
        pol = load_key_policy(cli_root / "users" / "alice")
        assert pol.scope == SCOPE_READ_WRITE and pol.expires_at is None

    def test_rotate_keeps_policy_and_revokes_old_key(self, cli_root):
        app = self._app()
        old = _printed_key(runner.invoke(
            app, ["user", "create", "bot", "--read-only", "--expires-in", "7d"]).output)
        res = runner.invoke(app, ["user", "rotate-key", "bot"])
        assert res.exit_code == 0, res.output
        new = _printed_key(res.output)
        user_dir = cli_root / "users" / "bot"
        assert not check_key(old, user_dir).matched
        assert check_key(new, user_dir).valid
        pol = load_key_policy(user_dir)
        assert pol.scope == SCOPE_READ
        assert abs(pol.lifetime - dt.timedelta(days=7)) < dt.timedelta(seconds=5)

    def test_rotate_can_change_policy(self, cli_root):
        app = self._app()
        runner.invoke(app, ["user", "create", "bot", "--read-only", "--expires-in", "7d"])
        res = runner.invoke(app, ["user", "rotate-key", "bot", "--read-write", "--no-expiry"])
        assert res.exit_code == 0, res.output
        pol = load_key_policy(cli_root / "users" / "bot")
        assert pol.scope == SCOPE_READ_WRITE and pol.expires_at is None

    def test_rotate_legacy_key_stays_read_write_no_expiry(self, cli_root):
        old = _legacy_user(cli_root, "old")
        res = runner.invoke(self._app(), ["user", "rotate-key", "old"])
        assert res.exit_code == 0, res.output
        user_dir = cli_root / "users" / "old"
        assert not check_key(old, user_dir).matched
        pol = load_key_policy(user_dir)
        assert pol.scope == SCOPE_READ_WRITE and pol.expires_at is None

    def test_keys_lists_policy_without_secrets(self, cli_root):
        app = self._app()
        k1 = _printed_key(runner.invoke(app, ["user", "create", "alice"]).output)
        k2 = _printed_key(runner.invoke(app, ["user", "create", "bot", "--read-only",
                                              "--expires-in", "12h"]).output)
        _legacy_user(cli_root, "old")
        res = runner.invoke(app, ["user", "keys"])
        assert res.exit_code == 0, res.output
        lines = {l.split()[0]: l for l in res.output.splitlines()[1:]}
        assert "read_write" in lines["alice"] and "never" in lines["alice"]
        assert "read " in lines["bot"] + " " and "active" in lines["bot"]
        assert "read_write" in lines["old"]
        assert "mm_sk_" not in res.output and "$2" not in res.output
        assert k1 not in res.output and k2 not in res.output

    def test_keys_shows_expired(self, cli_root):
        runner.invoke(self._app(), ["user", "create", "bot", "--expires-in", "1h"])
        _expire_now(cli_root / "users" / "bot")
        res = runner.invoke(self._app(), ["user", "keys", "bot"])
        assert "expired" in res.output

    def test_revoke_key(self, cli_root):
        app = self._app()
        key = _printed_key(runner.invoke(app, ["user", "create", "alice"]).output)
        res = runner.invoke(app, ["user", "revoke-key", "alice"])
        assert res.exit_code == 0, res.output
        assert not check_key(key, cli_root / "users" / "alice").matched
        assert "no key" in runner.invoke(app, ["user", "keys"]).output
