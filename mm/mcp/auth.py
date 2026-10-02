"""Auth layer for the Munkymind MCP HTTP server.

Supports four auth modes:

1. X-API-Key header           — curl / direct clients
2. OAuth 2.0 Client Credentials — backward-compatible token endpoint
3. OAuth 2.1 Authorization Code + PKCE — Claude / ChatGPT connectors
     GET  /authorize     → HTML login form
     POST /authorize     → validate API key, issue auth code, redirect to callback
     POST /token         → exchange code → access token (or client_credentials)
     POST /register      → Dynamic Client Registration (RFC 7591)

PKCE (RFC 7636) is enforced on the authorization_code grant.
The access_token IS the mm_sk_ key — stateless, no token DB needed.
Stdio transport bypasses all of this entirely.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from starlette.types import ASGIApp, Receive, Scope, Send

# ---------------------------------------------------------------------------
# In-memory stores (lost on restart — DCR re-registration handles clients;
# users just re-auth after restart which is acceptable for personal use)
# ---------------------------------------------------------------------------

# { code: { "client_id", "redirect_uri", "code_challenge", "api_key", "expires_at" } }
_AUTH_CODES: dict[str, dict[str, Any]] = {}

# { client_id: { "client_secret"?, "redirect_uris": [...], "registered_at" } }
_CLIENTS: dict[str, dict[str, Any]] = {}

AUTH_CODE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------

def _pkce_challenge(verifier: str) -> str:
    """Compute S256 code_challenge from code_verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _verify_pkce(code_challenge: str, code_verifier: str) -> bool:
    return secrets.compare_digest(_pkce_challenge(code_verifier), code_challenge)


# ---------------------------------------------------------------------------
# Key verification (shared with all auth paths)
# ---------------------------------------------------------------------------

def _get_hash(data_root: Path, user_id: str) -> str | None:
    hash_file = data_root / "users" / user_id / "api_key.hash"
    if hash_file.exists():
        return hash_file.read_text().strip()
    return None


def _verify_key(raw_key: str, data_root: Path, user_id: str) -> bool:
    if not raw_key or not raw_key.startswith("mm_sk_"):
        return False
    stored = _get_hash(data_root, user_id)
    if not stored:
        return False
    try:
        import bcrypt
        return bcrypt.checkpw(raw_key.encode(), stored.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# HTML login form
# ---------------------------------------------------------------------------

_LOGIN_FORM = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Munkymind — Sign In</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f0f0f; color: #e8e8e8;
          display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
  .card {{ background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 12px;
           padding: 2rem; width: 340px; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 0.25rem; }}
  p.sub {{ color: #888; font-size: 0.875rem; margin: 0 0 1.5rem; }}
  label {{ display: block; font-size: 0.875rem; color: #aaa; margin-bottom: 0.4rem; }}
  input[type=password] {{ width: 100%; box-sizing: border-box; padding: 0.6rem 0.75rem;
                          background: #111; border: 1px solid #333; border-radius: 8px;
                          color: #e8e8e8; font-size: 0.95rem; }}
  input[type=password]:focus {{ outline: none; border-color: #555; }}
  button {{ margin-top: 1rem; width: 100%; padding: 0.65rem; background: #2563eb;
            border: none; border-radius: 8px; color: white; font-size: 0.95rem;
            cursor: pointer; }}
  button:hover {{ background: #1d4ed8; }}
  .error {{ color: #f87171; font-size: 0.875rem; margin-top: 0.75rem; }}
  .app {{ font-size: 0.8rem; color: #666; margin-bottom: 1rem;
          padding: 0.4rem 0.75rem; background: #111; border-radius: 6px; }}
</style>
</head>
<body>
<div class="card">
  <h1>🐵 Munkymind</h1>
  <p class="sub">Sign in to connect your context library</p>
  {app_banner}
  <form method="post">
    <input type="hidden" name="client_id" value="{client_id}">
    <input type="hidden" name="redirect_uri" value="{redirect_uri}">
    <input type="hidden" name="state" value="{state}">
    <input type="hidden" name="code_challenge" value="{code_challenge}">
    <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
    <label for="key">API Key</label>
    <input type="password" id="key" name="api_key" placeholder="mm_sk_…" autofocus>
    {error}
    <button type="submit">Sign In</button>
  </form>
</div>
</body>
</html>
"""


def _render_login(
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
    error: str = "",
) -> bytes:
    client = _CLIENTS.get(client_id, {})
    app_name = client.get("client_name", client_id)
    app_banner = f'<div class="app">Authorising: <strong>{html.escape(app_name)}</strong></div>' if app_name else ""
    error_html = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return _LOGIN_FORM.format(
        client_id=html.escape(client_id),
        redirect_uri=html.escape(redirect_uri),
        state=html.escape(state),
        code_challenge=html.escape(code_challenge),
        code_challenge_method=html.escape(code_challenge_method),
        app_banner=app_banner,
        error=error_html,
    ).encode()


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# Paths that bypass the Bearer/X-API-Key gate entirely
_PUBLIC_PATHS = {b"/authorize", b"/register"}
_WELL_KNOWN_PREFIX = b"/.well-known/"
_TOKEN_PATH = b"/token"


class OAuthMCPMiddleware:
    """ASGI middleware handling OAuth endpoints and request auth."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._data_root = Path(os.environ.get("DATA_ROOT", "./data"))
        self._user_id = os.environ.get("USER_ID", "default")
        self._hash_cache: str | None = None

    def _verify(self, raw_key: str) -> bool:
        return _verify_key(raw_key, self._data_root, self._user_id)

    async def _read_body(self, receive: Receive) -> bytes:
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        return body

    async def _send_json(self, send: Send, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
                [b"cache-control", b"no-store"],
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def _send_html(self, send: Send, status: int, body: bytes) -> None:
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"text/html; charset=utf-8"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def _send_redirect(self, send: Send, location: str) -> None:
        await send({
            "type": "http.response.start",
            "status": 302,
            "headers": [[b"location", location.encode()]],
        })
        await send({"type": "http.response.body", "body": b""})

    # ------------------------------------------------------------------
    # /register — Dynamic Client Registration (RFC 7591)
    # ------------------------------------------------------------------

    async def _handle_register(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["method"] != "POST":
            await self._send_json(send, 405, {"error": "method_not_allowed"})
            return

        body = await self._read_body(receive)
        try:
            meta = json.loads(body)
        except Exception:
            await self._send_json(send, 400, {"error": "invalid_request", "error_description": "Invalid JSON"})
            return

        redirect_uris = meta.get("redirect_uris", [])
        if not redirect_uris or not isinstance(redirect_uris, list):
            await self._send_json(send, 400, {"error": "invalid_redirect_uri"})
            return

        client_id = f"dyn_{secrets.token_urlsafe(16)}"
        _CLIENTS[client_id] = {
            "redirect_uris": redirect_uris,
            "client_name": meta.get("client_name", ""),
            "registered_at": time.time(),
        }

        await self._send_json(send, 201, {
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })

    # ------------------------------------------------------------------
    # /authorize — Auth code flow login form + code issuance
    # ------------------------------------------------------------------

    def _parse_qs(self, raw: str | bytes) -> dict[str, str]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return {k: v[0] for k, v in parse_qs(raw).items()}

    async def _handle_authorize_get(self, scope: Scope, receive: Receive, send: Send) -> None:
        qs = self._parse_qs(scope.get("query_string", b""))
        client_id = qs.get("client_id", "")
        redirect_uri = qs.get("redirect_uri", "")
        state = qs.get("state", "")
        code_challenge = qs.get("code_challenge", "")
        code_challenge_method = qs.get("code_challenge_method", "S256")
        response_type = qs.get("response_type", "")

        if response_type != "code":
            await self._send_json(send, 400, {"error": "unsupported_response_type"})
            return

        if not code_challenge:
            await self._send_json(send, 400, {"error": "invalid_request", "error_description": "PKCE code_challenge required"})
            return

        if code_challenge_method != "S256":
            await self._send_json(send, 400, {"error": "invalid_request", "error_description": "Only S256 code_challenge_method supported"})
            return

        # Validate client & redirect_uri
        err = self._validate_client_redirect(client_id, redirect_uri)
        if err:
            await self._send_json(send, 400, {"error": err})
            return

        body = _render_login(client_id, redirect_uri, state, code_challenge, code_challenge_method)
        await self._send_html(send, 200, body)

    async def _handle_authorize_post(self, scope: Scope, receive: Receive, send: Send) -> None:
        raw_body = await self._read_body(receive)
        params = self._parse_qs(raw_body)

        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        state = params.get("state", "")
        code_challenge = params.get("code_challenge", "")
        code_challenge_method = params.get("code_challenge_method", "S256")
        api_key = params.get("api_key", "")

        # Re-validate client
        err = self._validate_client_redirect(client_id, redirect_uri)
        if err:
            await self._send_json(send, 400, {"error": err})
            return

        # Verify API key
        if not self._verify(api_key):
            body = _render_login(
                client_id, redirect_uri, state,
                code_challenge, code_challenge_method,
                error="Invalid API key. Please try again.",
            )
            await self._send_html(send, 200, body)
            return

        # Issue auth code
        code = secrets.token_urlsafe(32)
        _AUTH_CODES[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "api_key": api_key,
            "expires_at": time.time() + AUTH_CODE_TTL,
        }

        # Redirect back to client with code
        parts = urlparse(redirect_uri)
        callback_qs = {"code": code}
        if state:
            callback_qs["state"] = state
        new_query = urlencode(callback_qs)
        location = urlunparse(parts._replace(query=new_query))
        await self._send_redirect(send, location)

    def _validate_client_redirect(self, client_id: str, redirect_uri: str) -> str:
        """Returns error string or empty string if valid."""
        if not client_id:
            return "invalid_client"

        client = _CLIENTS.get(client_id)
        if client is None:
            # Allow pre-known redirect URIs for unregistered clients
            # (e.g. ChatGPT before DCR, or simple API-key clients)
            allowed = self._allowed_redirect_uris()
            if redirect_uri not in allowed:
                return "invalid_client"
            return ""

        if redirect_uri not in client["redirect_uris"]:
            return "invalid_redirect_uri"
        return ""

    def _allowed_redirect_uris(self) -> set[str]:
        """Env-configurable set of pre-approved redirect URIs for non-DCR clients."""
        env = os.environ.get("OAUTH_ALLOWED_REDIRECT_URIS", "")
        uris = set(filter(None, env.split(",")))
        # Always include known platform callbacks
        uris.add("https://chatgpt.com/aip/mcp/oauth/callback")
        uris.add("https://claude.ai/api/mcp/auth_callback")
        return uris

    # ------------------------------------------------------------------
    # /token — Client credentials + Authorization code exchange
    # ------------------------------------------------------------------

    async def _handle_token(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = await self._read_body(receive)
        # Support form-encoded or JSON
        try:
            params = self._parse_qs(body)
        except Exception:
            params = {}
        if not params:
            try:
                params = json.loads(body)
            except Exception:
                params = {}

        grant_type = params.get("grant_type", "")

        if grant_type == "client_credentials":
            await self._handle_token_client_credentials(params, send)
        elif grant_type == "authorization_code":
            await self._handle_token_auth_code(params, send)
        else:
            await self._send_json(send, 400, {"error": "unsupported_grant_type"})

    async def _handle_token_client_credentials(self, params: dict, send: Send) -> None:
        client_secret = params.get("client_secret", "")
        if not client_secret or not self._verify(client_secret):
            await self._send_json(send, 401, {"error": "invalid_client"})
            return
        await self._send_json(send, 200, {
            "access_token": client_secret,
            "token_type": "bearer",
            "expires_in": 86400,
        })

    async def _handle_token_auth_code(self, params: dict, send: Send) -> None:
        code = params.get("code", "")
        code_verifier = params.get("code_verifier", "")
        redirect_uri = params.get("redirect_uri", "")
        client_id = params.get("client_id", "")

        entry = _AUTH_CODES.pop(code, None)

        if entry is None:
            await self._send_json(send, 400, {"error": "invalid_grant", "error_description": "Unknown or expired code"})
            return

        if time.time() > entry["expires_at"]:
            await self._send_json(send, 400, {"error": "invalid_grant", "error_description": "Code expired"})
            return

        if entry["client_id"] != client_id:
            await self._send_json(send, 400, {"error": "invalid_grant", "error_description": "client_id mismatch"})
            return

        if entry["redirect_uri"] != redirect_uri:
            await self._send_json(send, 400, {"error": "invalid_grant", "error_description": "redirect_uri mismatch"})
            return

        if not code_verifier or not _verify_pkce(entry["code_challenge"], code_verifier):
            await self._send_json(send, 400, {"error": "invalid_grant", "error_description": "PKCE verification failed"})
            return

        # Access token IS the mm_sk_ key — stateless
        await self._send_json(send, 200, {
            "access_token": entry["api_key"],
            "token_type": "bearer",
            "expires_in": 86400,
        })

    # ------------------------------------------------------------------
    # ASGI __call__
    # ------------------------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if isinstance(path, bytes):
            path = path.decode("utf-8", errors="replace")
        path_bytes = path.encode()

        # Public: /.well-known/
        if path_bytes.startswith(_WELL_KNOWN_PREFIX):
            await self.app(scope, receive, send)
            return

        # /register — DCR (open, no auth)
        if path_bytes == b"/register" and scope["type"] == "http":
            await self._handle_register(scope, receive, send)
            return

        # /authorize — login form (GET) or code issuance (POST)
        if path_bytes == b"/authorize" and scope["type"] == "http":
            method = scope.get("method", "GET")
            if method == "GET":
                await self._handle_authorize_get(scope, receive, send)
            else:
                await self._handle_authorize_post(scope, receive, send)
            return

        # /token — self-authenticated
        if path_bytes == b"/token" and scope["type"] == "http":
            await self._handle_token(scope, receive, send)
            return

        # All other paths — Bearer or X-API-Key
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="replace")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = headers.get(b"x-api-key", b"").decode("utf-8", errors="replace")

        if not token or not self._verify(token):
            base_url = os.environ.get("MCP_BASE_URL", "").rstrip("/")
            if not base_url:  # derive the public address from the request (tunnel / proxy)
                proto = headers.get(b"x-forwarded-proto", b"").decode() or scope.get("scheme", "http")
                host = headers.get(b"host", b"").decode()
                base_url = f"{proto}://{host}" if host else ""
            www_auth = (
                f'Bearer realm="Munkymind", '
                f'resource_metadata="{base_url}/.well-known/oauth-protected-resource"'
            )
            body = json.dumps({
                "detail": "Unauthorized. Use OAuth (/authorize) or X-API-Key header."
            }).encode()
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                    [b"www-authenticate", www_auth.encode()],
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


# Backwards-compatible alias
ApiKeyMiddleware = OAuthMCPMiddleware
