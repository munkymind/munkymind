"""One-time login links for the library UI, and the read-only sessions they start."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mm.auth import sessions
from mm.auth.keys import issue_key

runner = CliRunner()


def _expire(user_dir, kind: str) -> None:
    p = user_dir / sessions.SESSIONS_FILE
    data = json.loads(p.read_text())
    past = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).isoformat()
    for v in data[kind].values():
        v["expires_at"] = past
    p.write_text(json.dumps(data))


class TestSessions:
    def test_link_works_once(self, tmp_path):
        link = sessions.create_link(tmp_path)
        s = sessions.redeem_link(tmp_path, link)
        assert s and s.startswith("mm_ss_")
        assert sessions.redeem_link(tmp_path, link) is None

    def test_expired_link_is_refused(self, tmp_path):
        link = sessions.create_link(tmp_path)
        _expire(tmp_path, "links")
        assert sessions.redeem_link(tmp_path, link) is None

    def test_session_is_read_only_and_expires(self, tmp_path):
        s = sessions.redeem_link(tmp_path, sessions.create_link(tmp_path))
        policy = sessions.check_session(tmp_path, s)
        assert policy is not None and not policy.can_write
        _expire(tmp_path, "sessions")
        assert sessions.check_session(tmp_path, s) is None

    def test_file_holds_no_usable_tokens(self, tmp_path):
        link = sessions.create_link(tmp_path)
        s = sessions.redeem_link(tmp_path, sessions.create_link(tmp_path))
        text = (tmp_path / sessions.SESSIONS_FILE).read_text()
        assert link not in text and s not in text and "mm_" not in text
        assert (tmp_path / sessions.SESSIONS_FILE).stat().st_mode & 0o077 == 0

    def test_end_session_and_end_all(self, tmp_path):
        a = sessions.redeem_link(tmp_path, sessions.create_link(tmp_path))
        b = sessions.redeem_link(tmp_path, sessions.create_link(tmp_path))
        unused = sessions.create_link(tmp_path)
        assert sessions.end_session(tmp_path, a)
        assert sessions.check_session(tmp_path, a) is None
        assert sessions.check_session(tmp_path, b) is not None
        assert sessions.end_all(tmp_path) == 1
        assert sessions.check_session(tmp_path, b) is None
        assert sessions.redeem_link(tmp_path, unused) is None

    def test_concurrent_redeem_gives_one_session(self, tmp_path):
        link = sessions.create_link(tmp_path)
        got = []
        threads = [threading.Thread(target=lambda: got.append(sessions.redeem_link(tmp_path, link)))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len([g for g in got if g]) == 1

    def test_api_key_is_not_a_session(self, tmp_path):
        key = issue_key(tmp_path)
        assert sessions.check_session(tmp_path, key) is None
        assert sessions.redeem_link(tmp_path, key) is None


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from mm.api import server
    from mm.core.store import UserStore

    monkeypatch.setattr(server, "DATA_ROOT", tmp_path)
    server.app.dependency_overrides.clear()

    def make_user(name: str):
        store = UserStore(tmp_path, name)
        store.init()
        store.get_config()
        issue_key(store.user_dir)
        return store.user_dir

    return TestClient(server.app), make_user


class TestApi:
    def test_link_signs_in_read_only(self, api):
        tc, make_user = api
        user_dir = make_user("alice")
        resp = tc.post("/ui/session", json={"token": sessions.create_link(user_dir)})
        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == "alice"
        s = resp.json()["session"]
        assert tc.get("/status", headers={"X-API-Key": s}).status_code == 200
        resp = tc.post("/ingest/files", headers={"X-API-Key": s},
                       files=[("files", ("a.md", b"# A\n", "text/markdown"))])
        assert resp.status_code == 403

    def test_used_or_bad_link_gets_401(self, api):
        tc, make_user = api
        link = sessions.create_link(make_user("alice"))
        assert tc.post("/ui/session", json={"token": link}).status_code == 200
        resp = tc.post("/ui/session", json={"token": link})
        assert resp.status_code == 401 and "login-link" in resp.json()["detail"]
        assert tc.post("/ui/session", json={"token": "mm_lt_nope"}).status_code == 401

    def test_session_belongs_to_its_user_only(self, api):
        tc, make_user = api
        make_user("bob")
        alice = make_user("alice")
        s = tc.post("/ui/session", json={"token": sessions.create_link(alice)}).json()["session"]
        assert sessions.check_session(alice, s) is not None
        assert sessions.check_session(alice.parent / "bob", s) is None

    def test_sign_out_ends_session(self, api):
        tc, make_user = api
        user_dir = make_user("alice")
        s = tc.post("/ui/session", json={"token": sessions.create_link(user_dir)}).json()["session"]
        assert tc.delete("/ui/session", headers={"X-API-Key": s}).status_code == 200
        resp = tc.get("/status", headers={"X-API-Key": s})
        assert resp.status_code == 401 and "login-link" in resp.json()["detail"]


@pytest.fixture()
def cli_root(monkeypatch, tmp_path):
    from mm.core.store import UserStore

    monkeypatch.setattr("mm.cli.main.DATA_ROOT", tmp_path)
    monkeypatch.delenv("MM_PUBLIC_URL", raising=False)
    monkeypatch.setenv("API_PORT", "8123")
    store = UserStore(tmp_path, "alice")
    store.init()
    issue_key(store.user_dir)
    return store.user_dir


class TestCli:
    def _app(self):
        from mm.cli.main import app
        return app

    def test_prints_a_working_link(self, cli_root):
        res = runner.invoke(self._app(), ["login-link", "--user", "alice"])
        assert res.exit_code == 0, res.output
        m = re.search(r"http://localhost:8123/ui#login=(mm_lt_\w+)", res.output)
        assert m, res.output
        assert sessions.redeem_link(cli_root, m.group(1))

    def test_url_option_and_env(self, cli_root, monkeypatch):
        res = runner.invoke(self._app(), ["login-link", "-u", "alice", "--url", "https://mm.example.org/"])
        assert "https://mm.example.org/ui#login=mm_lt_" in res.output
        monkeypatch.setenv("MM_PUBLIC_URL", "https://env.example.org")
        res = runner.invoke(self._app(), ["login-link", "-u", "alice"])
        assert "https://env.example.org/ui#login=mm_lt_" in res.output

    def test_unknown_user(self, cli_root):
        res = runner.invoke(self._app(), ["login-link", "--user", "nobody"])
        assert res.exit_code == 1

    def test_sign_out_all(self, cli_root):
        s = sessions.redeem_link(cli_root, sessions.create_link(cli_root))
        res = runner.invoke(self._app(), ["login-link", "--user", "alice", "--sign-out-all"])
        assert res.exit_code == 0 and "Signed out 1 browser" in res.output
        assert sessions.check_session(cli_root, s) is None

    @pytest.mark.parametrize("cmd", [["user", "rotate-key", "alice"], ["user", "revoke-key", "alice"]])
    def test_key_rotation_signs_browsers_out(self, cli_root, cmd):
        s = sessions.redeem_link(cli_root, sessions.create_link(cli_root))
        res = runner.invoke(self._app(), cmd)
        assert res.exit_code == 0, res.output
        assert sessions.check_session(cli_root, s) is None
