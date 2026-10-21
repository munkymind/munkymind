"""API key generation, storage and verification.

Each user has one REST API key. It is stored as a bcrypt hash in
``<user_dir>/api_key.hash``; its policy (scope, expiry, creation time) lives
next to it in ``<user_dir>/api_key.json``. A hash without a policy file is a
key issued before policies existed: it has full read-write access and never
expires, exactly as before.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import base58 as _base58
import bcrypt

HASH_FILE = "api_key.hash"
POLICY_FILE = "api_key.json"

SCOPE_READ = "read"
SCOPE_READ_WRITE = "read_write"
SCOPES = (SCOPE_READ, SCOPE_READ_WRITE)


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


@dataclass(frozen=True)
class KeyPolicy:
    """What a key may do and until when. Defaults match keys from before policies."""

    scope: str = SCOPE_READ_WRITE
    expires_at: _dt.datetime | None = None  # UTC; None = never expires
    created_at: _dt.datetime | None = None  # UTC; None = unknown (older key)

    @property
    def can_write(self) -> bool:
        return self.scope == SCOPE_READ_WRITE

    def is_expired(self, now: _dt.datetime | None = None) -> bool:
        return self.expires_at is not None and (now or _utcnow()) >= self.expires_at

    @property
    def lifetime(self) -> _dt.timedelta | None:
        """How long the key was issued for, so a rotation can renew it for the same period."""
        if self.expires_at is None or self.created_at is None:
            return None
        return self.expires_at - self.created_at

    def to_json(self) -> dict:
        iso = lambda d: d.isoformat() if d else None
        return {"scope": self.scope, "expires_at": iso(self.expires_at),
                "created_at": iso(self.created_at)}

    @classmethod
    def from_json(cls, data: dict) -> KeyPolicy:
        scope = data.get("scope", SCOPE_READ_WRITE)
        if scope not in SCOPES:
            raise ValueError(f"unknown key scope {scope!r}")
        return cls(scope=scope, expires_at=_parse_dt(data.get("expires_at")),
                   created_at=_parse_dt(data.get("created_at")))


class KeyPolicyError(ValueError):
    """The key's policy file exists but can't be read. The key is refused (fail closed)."""


def _parse_dt(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    d = _dt.datetime.fromisoformat(value)
    return d.replace(tzinfo=_dt.UTC) if d.tzinfo is None else d.astimezone(_dt.UTC)


def parse_duration(text: str) -> _dt.timedelta:
    """'12h', '30d', '8w' -> timedelta."""
    m = re.fullmatch(r"\s*(\d+)\s*([hdw])\s*", text or "", re.IGNORECASE)
    if not m or int(m.group(1)) <= 0:
        raise ValueError(f"invalid duration {text!r}: use a positive number with h, d or w (e.g. 12h, 30d, 8w)")
    n, unit = int(m.group(1)), m.group(2).lower()
    return {"h": _dt.timedelta(hours=n), "d": _dt.timedelta(days=n), "w": _dt.timedelta(weeks=n)}[unit]


def parse_expiry(text: str) -> _dt.datetime:
    """ISO date or datetime -> UTC datetime. A date means 00:00 UTC that day; no timezone means UTC."""
    try:
        return _parse_dt(text.strip())  # type: ignore[return-value]
    except (ValueError, AttributeError):
        raise ValueError(f"invalid date {text!r}: use YYYY-MM-DD or an ISO 8601 date-time") from None


# ------------------------------------------------------------------ #
# Generation and verification
# ------------------------------------------------------------------ #


def generate_key() -> tuple[str, str]:
    """Returns (raw_key, bcrypt_hash). Key format: mm_sk_{base58(32 random bytes)}"""
    raw_bytes = os.urandom(32)
    encoded = _base58.b58encode(raw_bytes).decode('ascii')
    raw_key = f'mm_sk_{encoded}'
    hashed = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode('utf-8')
    return raw_key, hashed


def verify_key(raw_key: str, stored_hash: str) -> bool:
    """Verify raw key against bcrypt hash."""
    try:
        return bcrypt.checkpw(raw_key.encode(), stored_hash.encode())
    except ValueError:
        return False


# ------------------------------------------------------------------ #
# Storage
# ------------------------------------------------------------------ #


def save_key_hash(user_dir: Path, hash: str, policy: KeyPolicy | None = None) -> None:
    """Store a key's hash and its policy (default: read-write, no expiry, created now).

    The policy is written first, so a crash in between can only leave the
    *previous* key with the new (just-requested) policy, never the new key
    with an older, possibly broader one.
    """
    user_dir = Path(user_dir)
    user_dir.mkdir(parents=True, exist_ok=True)
    policy = policy or KeyPolicy()
    if policy.created_at is None:
        policy = KeyPolicy(policy.scope, policy.expires_at, _utcnow())
    (user_dir / POLICY_FILE).write_text(json.dumps(policy.to_json(), indent=2) + "\n")
    (user_dir / HASH_FILE).write_text(hash)


def load_key_hash(user_dir: Path) -> str | None:
    """Load hash from user_dir/api_key.hash. Returns None if not found."""
    p = Path(user_dir) / HASH_FILE
    return p.read_text().strip() if p.exists() else None


def load_key_policy(user_dir: Path) -> KeyPolicy:
    """The key's policy. No policy file = an older key: read-write, no expiry.

    Raises KeyPolicyError if the file exists but is unreadable or invalid.
    """
    p = Path(user_dir) / POLICY_FILE
    if not p.exists():
        return KeyPolicy()
    try:
        return KeyPolicy.from_json(json.loads(p.read_text()))
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        raise KeyPolicyError(f"unreadable key policy {p}: {exc}") from None


def issue_key(user_dir: Path, scope: str = SCOPE_READ_WRITE,
              expires_at: _dt.datetime | None = None) -> str:
    """Create a new key for this user, replacing any existing one. Returns the raw key (show it once)."""
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {', '.join(SCOPES)}")
    if expires_at is not None and expires_at <= _utcnow():
        raise ValueError("expiry must be in the future")
    raw_key, hashed = generate_key()
    save_key_hash(user_dir, hashed, KeyPolicy(scope=scope, expires_at=expires_at))
    return raw_key


def revoke_key(user_dir: Path) -> bool:
    """Delete the user's key. Returns False if there was none."""
    had = (Path(user_dir) / HASH_FILE).exists()
    for name in (HASH_FILE, POLICY_FILE):
        (Path(user_dir) / name).unlink(missing_ok=True)
    return had


@dataclass(frozen=True)
class KeyCheck:
    """Result of checking a raw key against one user's stored key."""

    matched: bool
    policy: KeyPolicy | None = None

    @property
    def expired(self) -> bool:
        return bool(self.policy and self.policy.is_expired())

    @property
    def valid(self) -> bool:
        return self.matched and self.policy is not None and not self.expired


def check_key(raw_key: str, user_dir: Path) -> KeyCheck:
    """Does raw_key belong to this user, and under what policy?

    ``valid`` is False for a non-matching key, an expired key, or a key whose
    policy file can't be read.
    """
    if not raw_key or not raw_key.startswith("mm_sk_"):
        return KeyCheck(False)
    stored = load_key_hash(user_dir)
    if not stored or not verify_key(raw_key, stored):
        return KeyCheck(False)
    try:
        return KeyCheck(True, load_key_policy(user_dir))
    except KeyPolicyError as exc:
        print(f"[mm-auth] refusing key: {exc}")
        return KeyCheck(True, None)


def seed_key_from_env(data_root: Path, user_id: str, service: str) -> None:
    """If MM_OSS_API_KEY is set, make it user_id's API key (read-write, no expiry).

    Lets a Railway variable change rotate the key on redeploy, without editing
    the volume. Also logs which users exist, since the API accepts any user's
    key and an old test user would keep its key working.
    """
    users_dir = Path(data_root) / "users"
    if users_dir.is_dir():
        names = sorted(p.name for p in users_dir.iterdir() if (p / HASH_FILE).exists())
        print(f"[{service}] users with API keys: {', '.join(names) or '(none)'}")

    raw_key = os.environ.get("MM_OSS_API_KEY", "")
    if not raw_key.startswith("mm_sk_"):
        return
    user_dir = users_dir / user_id
    stored = load_key_hash(user_dir)
    if stored and verify_key(raw_key, stored):
        return  # already in sync — bcrypt is slow, don't rewrite every boot
    save_key_hash(user_dir, bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode("utf-8"))
    print(f"[{service}] API key hash seeded from MM_OSS_API_KEY for user '{user_id}'")
