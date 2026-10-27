"""UserStore — per-user directory + SQLite metadata store."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from mm.config.user import UserConfig

_VALID_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9\-]*$')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT,
    source TEXT,
    connector TEXT,
    created_at TEXT,
    updated_at TEXT,
    staleness_threshold_days INTEGER DEFAULT 30,
    confidence TEXT DEFAULT 'medium',
    tags TEXT,
    raw_path TEXT,
    content_hash TEXT
);

CREATE TABLE IF NOT EXISTS ingestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connector TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    pages_created INTEGER,
    pages_updated INTEGER,
    error_msg TEXT,
    ran_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hint TEXT,
    result TEXT,
    endpoint TEXT,
    ip TEXT,
    ts TEXT NOT NULL
);
"""


class UserStore:
    def __init__(self, data_root: Path, user_id: str):
        if '..' in user_id or '/' in user_id or not _VALID_ID.match(user_id):
            raise ValueError(f"Invalid user_id: {user_id!r}")
        self.user_id = user_id
        self.user_dir = data_root / 'users' / user_id
        self.context_dir = self.user_dir / 'context-repo'
        self.chroma_dir = self.user_dir / 'chroma'
        self.db_path = self.user_dir / 'db' / 'metadata.sqlite3'
        self.config_path = self.user_dir / 'config.yaml'

    def init(self) -> None:
        for d in (self.context_dir, self.chroma_dir, self.db_path.parent):
            d.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        # Libraries created before content_hash existed: add the column in place
        cols = {r[1] for r in con.execute("PRAGMA table_info(pages)")}
        if "content_hash" not in cols:
            con.execute("ALTER TABLE pages ADD COLUMN content_hash TEXT")
        con.commit()
        con.close()

    def get_config(self) -> UserConfig:
        if self.config_path.exists():
            return UserConfig.load(self.config_path)
        cfg = UserConfig.default(self.user_id)
        cfg.save(self.config_path)
        return cfg

    def collection_name(self) -> str:
        return f'mm-{self.user_id}'
