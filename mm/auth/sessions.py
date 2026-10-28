"""One-time login links and the browser sessions they start.

A login link lets someone open the library UI without pasting their API key,
so the key can stay in a password manager. ``munkymind login-link`` prints a
URL carrying a single-use token (``mm_lt_``) that works for a few minutes.
The UI trades it for a session (``mm_ss_``): a read-only credential that only
this browser holds, lasts a set number of days, and is never the API key.

Both are random 32-byte values, so a plain SHA-256 is enough to store them
(bcrypt is for guessable secrets). Each user's are kept in
``<user_dir>/ui_sessions.json``; nothing in that file can be used to sign in.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path

import contextlib

import base58 as _base58

from mm.auth.keys import SCOPE_READ, KeyPolicy

SESSIONS_FILE = "ui_sessions.json"
LINK_PREFIX = "mm_lt_"
SESSION_PREFIX = "mm_ss_"
LINK_TTL = _dt.timedelta(minutes=10)
SESSION_TTL = _dt.timedelta(days=30)
MAX_SESSIONS = 20  # oldest are dropped first


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _new(prefix: str) -> str:
    return prefix + _base58.b58encode(secrets.token_bytes(32)).decode("ascii")


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _load(user_dir: Path) -> dict:
    p = Path(user_dir) / SESSIONS_FILE
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return {"links": {}, "sessions": {}}
    return {"links": dict(data.get("links") or {}), "sessions": dict(data.get("sessions") or {})}


def _save(user_dir: Path, data: dict) -> None:
    """Write atomically, dropping anything expired."""
    now = _utcnow()
    for kind in ("links", "sessions"):
        data[kind] = {h: v for h, v in data[kind].items() if _dt.datetime.fromisoformat(v["expires_at"]) > now}
    if len(data["sessions"]) > MAX_SESSIONS:
        keep = sorted(data["sessions"].items(), key=lambda kv: kv[1]["created_at"])[-MAX_SESSIONS:]
        data["sessions"] = dict(keep)
    user_dir = Path(user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=user_dir, prefix=".sessions.")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, user_dir / SESSIONS_FILE)


@contextlib.contextmanager
def _locked(user_dir: Path):
    """Serialise changes, so two browsers can't both use the same link."""
    try:
        import fcntl
    except ImportError:  # Windows without Docker: single process anyway
        yield
        return
    user_dir = Path(user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    with open(user_dir / ".sessions.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def create_link(user_dir: Path, ttl: _dt.timedelta = LINK_TTL) -> str:
    """A new single-use login token. Returns the raw token (it goes in the link only)."""
    raw = _new(LINK_PREFIX)
    with _locked(user_dir):
        data = _load(user_dir)
        now = _utcnow()
        data["links"][_digest(raw)] = {"created_at": now.isoformat(), "expires_at": (now + ttl).isoformat()}
        _save(user_dir, data)
    return raw


def redeem_link(user_dir: Path, raw: str, ttl: _dt.timedelta = SESSION_TTL) -> str | None:
    """Use up a login token and start a session. None if the token is unknown, used or expired."""
    if not raw or not raw.startswith(LINK_PREFIX):
        return None
    with _locked(user_dir):
        data = _load(user_dir)
        link = data["links"].pop(_digest(raw), None)
        if link is None:
            return None
        if _dt.datetime.fromisoformat(link["expires_at"]) <= _utcnow():
            _save(user_dir, data)
            return None
        session = _new(SESSION_PREFIX)
        now = _utcnow()
        data["sessions"][_digest(session)] = {"created_at": now.isoformat(), "expires_at": (now + ttl).isoformat()}
        _save(user_dir, data)
    return session


def check_session(user_dir: Path, raw: str) -> KeyPolicy | None:
    """The session's policy (always read-only) if raw is a live session for this user."""
    if not raw or not raw.startswith(SESSION_PREFIX):
        return None
    s = _load(user_dir)["sessions"].get(_digest(raw))
    if s is None:
        return None
    expires = _dt.datetime.fromisoformat(s["expires_at"])
    if expires <= _utcnow():
        return None
    return KeyPolicy(scope=SCOPE_READ, expires_at=expires, created_at=_dt.datetime.fromisoformat(s["created_at"]))


def end_session(user_dir: Path, raw: str) -> bool:
    with _locked(user_dir):
        data = _load(user_dir)
        if data["sessions"].pop(_digest(raw or ""), None) is None:
            return False
        _save(user_dir, data)
    return True


def end_all(user_dir: Path) -> int:
    """Sign every browser out and cancel unused links. Returns how many sessions ended."""
    with _locked(user_dir):
        data = _load(user_dir)
        n = len(data["sessions"])
        if n or data["links"]:
            _save(user_dir, {"links": {}, "sessions": {}})
    return n


def count_sessions(user_dir: Path) -> int:
    now = _utcnow()
    return sum(1 for s in _load(user_dir)["sessions"].values()
               if _dt.datetime.fromisoformat(s["expires_at"]) > now)
