"""Keep each library in step with its notes folder, without running `ingest` by hand.

Every MM_SYNC_INTERVAL seconds (default 300; 0 turns it off) a cheap check lists the
files of each `files` connector with their size and change time. Only when that
fingerprint differs from the last synced one does the ingest run, and the ingest itself
re-embeds only new or changed notes. Polling rather than file-system events, because
events don't reliably cross Docker Desktop's bind mounts on macOS and Windows.

GitHub connectors are not synced automatically (each check would call the GitHub API).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("munkymind.sync")
if not log.handlers:  # show sync activity in `docker compose logs` without app-wide logging setup
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s:     sync: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

DEFAULT_INTERVAL = 300
STATE_FILE = "sync_state.json"


def interval() -> int:
    """Seconds between checks from MM_SYNC_INTERVAL; 0 or less means off."""
    raw = (os.environ.get("MM_SYNC_INTERVAL") or "").strip()
    try:
        return int(raw) if raw else DEFAULT_INTERVAL
    except ValueError:
        log.warning("MM_SYNC_INTERVAL=%r is not a number of seconds; using %s", raw, DEFAULT_INTERVAL)
        return DEFAULT_INTERVAL


def fingerprint(conn_cfg: dict) -> str | None:
    """What the folder holds right now (names, sizes, change times, plus the domain
    mapping), or None when the folder isn't there (e.g. not mounted)."""
    from mm.connectors.files import list_files

    path = Path(conn_cfg.get("path", "")).expanduser()
    if not path.exists():
        return None
    h = hashlib.sha256(json.dumps(conn_cfg.get("domain_map") or {}, sort_keys=True).encode())
    for f in sorted(list_files(path, conn_cfg.get("extensions"))):
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{f}\t{st.st_mtime_ns}\t{st.st_size}\n".encode())
    return h.hexdigest()


def _load_state(store) -> dict:
    try:
        return json.loads((store.user_dir / STATE_FILE).read_text())
    except (OSError, ValueError):
        return {}


def _save_state(store, state: dict) -> None:
    tmp = store.user_dir / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(store.user_dir / STATE_FILE)


def sync_user(data_root: Path, user_id: str, force: bool = False) -> list[dict]:
    """Ingest each of the user's notes folders that changed since the last sync."""
    from mm.config.user import UserConfig
    from mm.connectors.files import FilesConnector
    from mm.core.store import UserStore
    from mm.embedding.providers import EmbeddingProvider
    from mm.ingestion.runner import run_connector

    store = UserStore(data_root, user_id)
    if not store.config_path.exists():
        return []
    cfg = UserConfig.load(store.config_path)
    state = _load_state(store)
    results = []
    for conn_cfg in cfg.connectors:
        if conn_cfg.get("connector") != "files":
            continue
        key = str(conn_cfg.get("path", ""))
        fp = fingerprint(conn_cfg)
        if fp is None or (fp == state.get(key) and not force):
            continue
        embed = EmbeddingProvider.from_config({"provider": cfg.embedding.provider, "model": cfg.embedding.model})
        connector = FilesConnector({k: v for k, v in conn_cfg.items() if k != "connector"}, cfg)
        result = run_connector(connector, store, embed)
        state[key] = fp
        _save_state(store, state)
        results.append({"path": key, **result})
        changed = sum(result.get(k, 0) for k in ("pages_created", "pages_updated", "pages_removed"))
        if changed:
            log.info("Synced %s for %s: %s new, %s updated, %s removed", key, user_id,
                     result.get("pages_created", 0), result.get("pages_updated", 0), result.get("pages_removed", 0))
    return results


def sync_all(data_root: Path) -> None:
    """One pass over every user; a failure for one user never stops the others."""
    users = data_root / "users"
    if not users.is_dir():
        return
    for user_dir in sorted(users.iterdir()):
        if not (user_dir / "config.yaml").exists():
            continue
        try:
            sync_user(data_root, user_dir.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("Sync failed for %s: %s: %s", user_dir.name, type(exc).__name__, exc)


def run_forever(data_root: Path, every: int, stop: threading.Event, user_id: str | None = None) -> None:
    while not stop.is_set():
        if user_id:
            try:
                sync_user(data_root, user_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Sync failed for %s: %s: %s", user_id, type(exc).__name__, exc)
        else:
            sync_all(data_root)
        stop.wait(every)


def start_background(data_root: Path) -> threading.Event | None:
    """Start syncing in a daemon thread; returns the event that stops it (None when off)."""
    every = interval()
    if every <= 0:
        log.info("Automatic sync is off (MM_SYNC_INTERVAL=%s)", every)
        return None
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(data_root, every, stop), name="mm-sync", daemon=True).start()
    log.info("Automatic sync every %ss", every)
    return stop
