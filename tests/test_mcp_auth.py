"""Tests for MCP HTTP server auth middleware (OAuthMCPMiddleware).

Tests:
- No key → 401
- Wrong key → 401
- Valid X-API-Key → pass through
- Valid Bearer token → pass through
- OAuth /token with valid client_secret → 200 + access_token
- OAuth /token with wrong secret → 401
- OAuth /token with wrong grant_type → 400
- No hash file → 401
"""
from __future__ import annotations

from pathlib import Path

import bcrypt
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mm.mcp.auth import OAuthMCPMiddleware

VALID_KEY = "mm_sk_testkey1234567890abcdef"


def _make_user_store(tmp_path: Path, raw_key: str) -> Path:
    user_dir = tmp_path / "users" / "alice"
    user_dir.mkdir(parents=True)
    hashed = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    (user_dir / "api_key.hash").write_text(hashed)
    return tmp_path


def _make_client(tmp_path: Path, create_hash: bool = True) -> TestClient:
    if create_hash:
        data_root = _make_user_store(tmp_path, VALID_KEY)
    else:
        data_root = tmp_path
        (tmp_path / "users" / "alice").mkdir(parents=True)

    async def ok(request):
        return JSONResponse({"status": "ok"})

    inner = Starlette(routes=[Route("/mcp", ok), Route("/", ok)])
    middleware = OAuthMCPMiddleware(inner)
    middleware._data_root = data_root
    middleware._user_id = "alice"
    middleware._hash = None
    return TestClient(middleware, raise_server_exceptions=True)


class TestApiKeyAuth:
    @pytest.fixture()
    def client(self, tmp_path):
        return _make_client(tmp_path)

    def test_no_key_returns_401(self, client):
        resp = client.get("/mcp")
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client):
        resp = client.get("/mcp", headers={"X-API-Key": "mm_sk_wrongkeyXXXXXXXXXXXXXXXX"})
        assert resp.status_code == 401

    def test_invalid_prefix_returns_401(self, client):
        resp = client.get("/mcp", headers={"X-API-Key": "notavalidkey"})
        assert resp.status_code == 401

    def test_valid_api_key_passes(self, client):
        resp = client.get("/mcp", headers={"X-API-Key": VALID_KEY})
        assert resp.status_code == 200

    def test_valid_bearer_passes(self, client):
        resp = client.get("/mcp", headers={"Authorization": f"Bearer {VALID_KEY}"})
        assert resp.status_code == 200

    def test_no_hash_file_returns_401(self, tmp_path):
        client = _make_client(tmp_path, create_hash=False)
        resp = client.get("/mcp", headers={"X-API-Key": VALID_KEY})
        assert resp.status_code == 401


class TestOAuthToken:
    @pytest.fixture()
    def client(self, tmp_path):
        return _make_client(tmp_path)

    def test_valid_client_credentials(self, client):
        resp = client.post("/token", data={
            "grant_type": "client_credentials",
            "client_id": "alice",
            "client_secret": VALID_KEY,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["token_type"] == "bearer"
        assert data["access_token"] == VALID_KEY
        assert "expires_in" in data

    def test_wrong_secret_returns_401(self, client):
        resp = client.post("/token", data={
            "grant_type": "client_credentials",
            "client_id": "alice",
            "client_secret": "mm_sk_wrongXXXXXXXXXXXXXXXXXXXX",
        })
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_client"

    def test_unknown_grant_type_returns_400(self, client):
        resp = client.post("/token", data={
            "grant_type": "not_a_real_grant",
            "client_id": "alice",
            "client_secret": VALID_KEY,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "unsupported_grant_type"

    def test_auth_code_grant_without_code_returns_invalid_grant(self, client):
        # authorization_code IS supported now, but a request missing the
        # actual `code` parameter should fail as invalid_grant, not be
        # rejected as an unsupported grant type.
        resp = client.post("/token", data={
            "grant_type": "authorization_code",
            "client_id": "alice",
            "client_secret": VALID_KEY,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_token_can_be_used_as_bearer(self, client):
        # Get token then use it
        token_resp = client.post("/token", data={
            "grant_type": "client_credentials",
            "client_id": "alice",
            "client_secret": VALID_KEY,
        })
        token = token_resp.json()["access_token"]
        resp = client.get("/mcp", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200


class TestClientPersistence:
    """Registered clients survive a restart, so connectors can sign in again."""

    def test_registered_client_survives_restart(self, tmp_path):
        import mm.mcp.auth as auth
        client = _make_client(tmp_path)
        cb = "https://chatgpt.com/connector/oauth/abc123"
        cid = client.post("/register", json={"redirect_uris": [cb], "client_name": "ChatGPT"}).json()["client_id"]
        auth._CLIENTS.clear()
        auth._CLIENTS_LOADED_FROM = None  # a new process
        resp = client.get("/authorize", params={
            "response_type": "code", "client_id": cid, "redirect_uri": cb,
            "code_challenge": "x", "code_challenge_method": "S256"})
        assert resp.status_code == 200
        assert (tmp_path / "oauth" / "clients.json").exists()

    def test_chatgpt_connector_callback_allowed_for_unknown_client(self, tmp_path):
        client = _make_client(tmp_path)
        q = {"response_type": "code", "client_id": "dyn_unknown", "code_challenge": "x",
             "code_challenge_method": "S256"}
        ok = client.get("/authorize", params={**q, "redirect_uri": "https://chatgpt.com/connector/oauth/abc"})
        assert ok.status_code == 200
        for bad in ("https://chatgpt.com.evil.example/connector/oauth/x",
                    "https://chatgpt.com/connector/oauth/x?next=evil",
                    "https://chatgpt.com/connector/oauth/../../evil"):
            resp = client.get("/authorize", params={**q, "redirect_uri": bad})
            assert resp.status_code == 400

    def test_token_lifetime_does_not_force_daily_sign_in(self):
        import mm.mcp.auth as auth
        assert auth.TOKEN_EXPIRES_IN >= 30 * 86400
