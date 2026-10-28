"""`munkymind doctor`: is everything working right now, and if not, how to fix it.

`./setup.sh` proves a fresh install works once. This answers the same question any
time later (Claude Desktop stopped finding notes, Docker didn't come back after a
restart, a key ran out of credit). Every check is read-only, never prints a key, and
every failure comes with a one-line fix. Exit code 1 when anything fails.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
ICONS = {OK: "✓", WARN: "⚠", FAIL: "✗", SKIP: "·"}
TIMEOUT = 8


@dataclass
class Check:
    status: str
    label: str
    fix: str = ""


def in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("DATA_ROOT") == "/data"


def _get(url: str, headers: dict | None = None):
    import requests
    return requests.get(url, headers=headers or {}, timeout=TIMEOUT)


def _first_ok(urls: list[str]) -> str | None:
    for url in urls:
        try:
            if _get(url).status_code < 500:
                return url
        except Exception:  # noqa: BLE001
            continue
    return None


def _cmd(text: str) -> str:
    """A command as the user should type it: through Docker when Munkymind runs there."""
    return f"docker compose exec api {text}" if in_docker() else text


# ------------------------------------------------------------------ #
# Checks
# ------------------------------------------------------------------ #

def check_api() -> Check:
    port = os.environ.get("API_PORT") or "8000"
    urls = list(dict.fromkeys([f"http://localhost:{port}/health", "http://localhost:8000/health"]))
    url = _first_ok(urls)
    if url:
        return Check(OK, f"API running ({url.rsplit('/', 1)[0].removeprefix('http://')})")
    if not in_docker():  # a local install can work without the server (CLI, stdio MCP)
        return Check(WARN, "API server isn't running (needed for the library UI and automatic sync)",
                     "Start it: docker compose up -d, or uvicorn mm.api.server:app")
    return Check(FAIL, "API isn't answering", "Start it: docker compose up -d (then docker compose logs api if it stays down)")


def check_mcp(username: str) -> list[Check]:
    port = os.environ.get("MCP_PORT") or "8001"
    candidates = [os.environ.get("MM_MCP_URL", "").rstrip("/")] if os.environ.get("MM_MCP_URL") else []
    candidates += ["http://mcp:8001", f"http://localhost:{port}"]
    url = _first_ok([f"{c}/.well-known/oauth-protected-resource" for c in dict.fromkeys(candidates)])
    if url:
        out = [Check(OK, "MCP server running (for Claude Desktop, claude.ai, ChatGPT, Cursor)")]
    elif in_docker():
        out = [Check(FAIL, "MCP server isn't answering, so AI tools can't reach your library",
                     "Start it: docker compose up -d mcp")]
    else:
        out = [Check(SKIP, "MCP HTTP server not running here (Claude Desktop over stdio doesn't need it)")]
    served = os.environ.get("MM_USER_ID")
    if served is None and not in_docker():
        served = _local_env().get("USER_ID")
    if served and served != username:
        out.append(Check(FAIL, f"AI tools connect as '{served}', not '{username}'",
                         f"In .env set MM_USER_ID={username}, then: docker compose up -d mcp"))
    return out


def check_api_key(store) -> Check:
    from mm.auth.keys import KeyPolicyError, load_key_hash, load_key_policy

    if load_key_hash(store.user_dir) is None:
        return Check(FAIL, "No API key, so the library UI and connectors can't sign in",
                     _cmd(f"munkymind user rotate-key {store.user_id}"))
    try:
        pol = load_key_policy(store.user_dir)
    except KeyPolicyError:
        return Check(FAIL, "API key's policy file can't be read, so the key is refused",
                     _cmd(f"munkymind user rotate-key {store.user_id} --read-write --no-expiry"))
    if pol.is_expired():
        return Check(FAIL, f"API key expired on {pol.expires_at:%Y-%m-%d}",
                     _cmd(f"munkymind user rotate-key {store.user_id}"))
    access = "read-write" if pol.can_write else "read-only"
    if pol.expires_at:
        days = (pol.expires_at - dt.datetime.now(dt.UTC)).days
        if days < 7:
            return Check(WARN, f"API key ({access}) expires in {days} day{'s' if days != 1 else ''}",
                         _cmd(f"munkymind user rotate-key {store.user_id}"))
        return Check(OK, f"API key valid ({access}, expires {pol.expires_at:%Y-%m-%d})")
    return Check(OK, f"API key valid ({access}, no expiry)")


def check_notes(cfg) -> list[Check]:
    from mm.connectors.files import list_files

    folders = [c for c in cfg.connectors if c.get("connector") == "files"]
    if not folders:
        return [Check(SKIP, "No notes folder configured (GitHub or uploads only)")]
    out = []
    for c in folders:
        path = Path(str(c.get("path", ""))).expanduser()
        if not path.is_dir():
            fix = ("Check MM_NOTES_DIR in .env points at your notes, then: docker compose up -d"
                   if in_docker() else f"Check the folder exists, or re-run setup: munkymind setup")
            out.append(Check(FAIL, f"Notes folder {path} isn't there (not mounted?)", fix))
            continue
        n = sum(1 for _ in list_files(path, c.get("extensions")))
        if n == 0:
            out.append(Check(WARN, f"Notes folder {path} has no notes in it",
                             "Add .md/.txt/.pdf files, or check MM_NOTES_DIR in .env"))
        else:
            out.append(Check(OK, f"Notes folder {path}: {n} note{'s' if n != 1 else ''}"))
    return out


def check_library(store) -> list[Check]:
    from mm.ingestion import sync

    if not store.db_path.exists():
        return [Check(FAIL, "Library is empty: nothing has been indexed yet",
                      _cmd(f"munkymind ingest -c files --user {store.user_id}"))]
    con = sqlite3.connect(store.db_path)
    try:
        pages = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        last = con.execute("SELECT ran_at, status FROM ingestions ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        pages, last = 0, None
    finally:
        con.close()
    out = []
    when = f", last indexed {_ago(last[0])}" if last else ""
    if pages:
        out.append(Check(OK, f"Library: {pages} page{'s' if pages != 1 else ''}{when}"))
    else:
        out.append(Check(FAIL, "Library is empty: nothing has been indexed yet",
                         _cmd(f"munkymind ingest -c files --user {store.user_id}")))
    if last and last[1] != "ok":
        out.append(Check(FAIL, f"Last indexing run failed ({last[1]})",
                         "See: docker compose logs api, then re-run the ingest"))
    every = sync.interval()
    if not in_docker():
        out.append(Check(SKIP, "Automatic sync runs inside the API server; without it use: munkymind watch"))
    elif every <= 0:
        out.append(Check(WARN, "Automatic sync is off (MM_SYNC_INTERVAL=0): edited notes aren't picked up",
                         "Set MM_SYNC_INTERVAL=300 in .env, then: docker compose up -d"))
    else:
        out.append(Check(OK, f"Automatic sync on (every {every // 60 or every} {'min' if every >= 60 else 's'})"))
    return out


def _key_check(name: str, provider: str, model: str) -> Check:
    """One cheap authenticated call per provider (lists models; no tokens spent)."""
    from mm.config.env import ollama_url

    what = f"{name} ({provider}/{model})" if model else f"{name} ({provider})"
    try:
        if provider == "openai":
            key = os.environ.get("OPENAI_API_KEY", "")
            if not key or key == "sk-...":
                return Check(FAIL, f"{what}: no OpenAI key", "Add OPENAI_API_KEY to .env, then: docker compose up -d")
            r = _get("https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"})
        elif provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                return Check(FAIL, f"{what}: no Anthropic key", "Add ANTHROPIC_API_KEY to .env, then: docker compose up -d")
            r = _get("https://api.anthropic.com/v1/models", {"x-api-key": key, "anthropic-version": "2023-06-01"})
        elif provider == "ollama":
            r = _get(f"{ollama_url()}/api/tags")
            if not r.ok:
                raise RuntimeError(f"HTTP {r.status_code}")
            names = {m.get("name", "").split(":")[0] for m in r.json().get("models", [])}
            if model and model.split(":")[0] not in names:
                return Check(FAIL, f"{what}: model isn't pulled in Ollama", f"ollama pull {model}")
            return Check(OK, f"{what}: Ollama reachable")
        else:
            return Check(SKIP, f"{what}: not checked")
    except Exception as exc:  # noqa: BLE001
        if provider == "ollama":
            return Check(FAIL, f"{what}: Ollama isn't reachable at {ollama_url()}",
                         "Start Ollama (ollama serve), or set OLLAMA_URL in .env")
        return Check(WARN, f"{what}: couldn't reach {provider} ({type(exc).__name__}); network?")
    if r.status_code in (401, 403):
        return Check(FAIL, f"{what}: key rejected ({r.status_code})", f"Check the key in .env (typo, revoked, or no credit)")
    if r.status_code == 429:
        return Check(WARN, f"{what}: rate limited or out of credit (429)", "Check your billing / usage limits")
    if not r.ok:
        return Check(WARN, f"{what}: unexpected answer (HTTP {r.status_code})")
    return Check(OK, f"{what}: key works")


def check_providers(cfg) -> list[Check]:
    seen, out = set(), []
    for name, prov, model in (("Answers", cfg.llm.provider, cfg.llm.model),
                              ("Search", cfg.embedding.provider, cfg.embedding.model)):
        if (prov, model) in seen:
            continue
        seen.add((prov, model))
        out.append(_key_check(name, prov, model))
    return out


def _claude_desktop_configs() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Claude/claude_desktop_config.json"]
    if os.name == "nt":
        return [Path(os.environ.get("APPDATA", "")) / "Claude/claude_desktop_config.json"]
    paths = [Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "Claude/claude_desktop_config.json"]
    try:
        if "microsoft" in Path("/proc/version").read_text().lower():  # WSL: the Windows app's config
            paths += sorted(Path("/mnt/c/Users").glob("*/AppData/Roaming/Claude/claude_desktop_config.json"))
    except OSError:
        pass
    return paths


def check_claude_desktop(username: str) -> Check:
    if in_docker():
        return Check(SKIP, "Claude Desktop: can't be checked from inside Docker. "
                           "If it can't see your notes, run ./setup-mcp.sh on your computer")
    found = [p for p in _claude_desktop_configs() if p.is_file()]
    if not found:
        return Check(SKIP, "Claude Desktop: not installed here (or never opened)")
    for p in found:
        try:
            servers = json.loads(p.read_text()).get("mcpServers") or {}
        except (OSError, ValueError, AttributeError):
            continue
        # Any entry that runs the Munkymind server, whatever it's called (older installs: "monkey-mind")
        entries = [e for name, e in servers.items() if isinstance(e, dict) and (
            name in ("munkymind", "monkey-mind") or "mm.mcp.server" in " ".join(e.get("args") or []))]
        for entry in entries:
            args = " ".join(entry.get("args") or [])
            user = (entry.get("env") or {}).get("USER_ID")
            if "USER_ID=" in args:
                user = args.split("USER_ID=", 1)[1].split()[0]
            if user in (None, username):
                return Check(OK, "Claude Desktop: Munkymind is configured (restart Claude Desktop after changes)")
        if entries:
            return Check(FAIL, f"Claude Desktop connects as a different user than '{username}'",
                         "Run ./setup-mcp.sh, then restart Claude Desktop")
    return Check(FAIL, "Claude Desktop: Munkymind isn't configured",
                 "Run ./setup-mcp.sh, then restart Claude Desktop")


# ------------------------------------------------------------------ #
# Runner
# ------------------------------------------------------------------ #

def _local_env() -> dict:
    from mm.cli.main import DATA_ROOT
    env = {}
    try:
        for line in (DATA_ROOT / ".env").read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def _ago(iso: str | None) -> str:
    try:
        d = dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return "at an unknown time"
    d = d if d.tzinfo else d.replace(tzinfo=dt.UTC)
    mins = int((dt.datetime.now(dt.UTC) - d).total_seconds() // 60)
    if mins < 2:
        return "just now"
    if mins < 120:
        return f"{mins} minutes ago"
    hours = mins // 60
    return f"{hours} hours ago" if hours < 48 else f"{hours // 24} days ago"


def run(data_root: Path, username: str) -> list[Check]:
    from mm.config.user import UserConfig
    from mm.core.store import UserStore

    checks: list[Check] = [check_api()]
    store = UserStore(data_root, username)
    if not store.config_path.exists():
        users = sorted(p.name for p in (data_root / "users").iterdir()
                       if (p / "config.yaml").exists()) if (data_root / "users").is_dir() else []
        hint = f" Users here: {', '.join(users)}." if users else ""
        checks.append(Check(FAIL, f"No user '{username}' in {data_root}.{hint}",
                            "Pass --user <name>, or run setup: ./setup.sh"))
        return checks
    cfg = UserConfig.load(store.config_path)
    checks += check_mcp(username)
    checks.append(check_api_key(store))
    checks += check_notes(cfg)
    checks += check_library(store)
    checks += check_providers(cfg)
    checks.append(check_claude_desktop(username))
    return checks
