"""Interactive setup wizard for Monkey Mind.

Fixes:
  F2 – clearly display created username, generate+display API key 'shown once',
       warn if the specific requested user already exists.
  F3 – actually configure connectors (write to config), run full ingest
       pipeline (embed → ChromaDB), run a test query before declaring success.
"""
from __future__ import annotations

import os
from pathlib import Path

import typer

import os
DATA_ROOT = Path(os.environ.get('DATA_ROOT', str(Path.home() / '.monkey-mind')))


def run_wizard() -> None:
    """Walk user through initial configuration interactively."""
    typer.echo("\n🐒 Welcome to Monkey Mind Setup Wizard\n")

    # ── Step 1: Username ─────────────────────────────────────────────────────
    system_user = os.environ.get("USER", os.environ.get("LOGNAME", "user"))
    username = typer.prompt(
        "Step 1 · Enter username (alphanumeric + hyphens)",
        default=system_user,
    ).strip()
    if not username:
        typer.echo("Username cannot be empty.", err=True)
        raise typer.Exit(1)

    from mm.core.store import UserStore
    from mm.auth.keys import generate_key, save_key_hash
    from mm.config.user import UserConfig, LLMConfig, EmbedConfig

    store = UserStore(DATA_ROOT, username)

    # ── Step 2: Check for existing user — warn and offer abort ───────────────
    user_already_exists = store.config_path.exists()
    if user_already_exists:
        typer.echo(
            f"\n⚠️  Warning: User '{username}' already exists.",
            err=False,
        )
        choice = typer.confirm(
            "  Continue and reconfigure? (API key will NOT be regenerated)",
            default=False,
        )
        if not choice:
            typer.echo("Setup aborted. Existing user unchanged.")
            raise typer.Exit(0)

    store.init()

    # ── Create user / load config, generate API key for new users ────────────
    raw_key: str | None = None
    if user_already_exists:
        cfg = UserConfig.load(store.config_path)
        typer.echo(f"\n✓ Reconfiguring existing user: '{username}'")
    else:
        cfg = UserConfig.default(username)
        raw_key, hashed = generate_key()
        save_key_hash(store.user_dir, hashed)
        typer.echo(f"\n✓ User '{username}' created.")
        typer.echo("━" * 60)
        typer.echo("  Save this API key — it is shown ONCE and cannot be recovered:")
        typer.echo(f"\n    {raw_key}\n")
        typer.echo("━" * 60)

    # ── LLM provider ─────────────────────────────────────────────────────────
    llm_providers = ["anthropic", "openai", "ollama"]
    typer.echo("\nLLM provider")
    for i, p in enumerate(llm_providers, 1):
        typer.echo(f"  {i}. {p}")
    # Default to a provider the user already has a key for: one OpenAI key should just work.
    if os.environ.get("ANTHROPIC_API_KEY"):
        default_llm = "1"
    elif os.environ.get("OPENAI_API_KEY"):
        default_llm = "2"
    else:
        default_llm = "3" if _ollama_available() else "1"
    llm_choice = typer.prompt("Choose [1-3]", default=default_llm)
    try:
        llm_provider = llm_providers[int(llm_choice) - 1]
    except (ValueError, IndexError):
        llm_provider = "anthropic"

    if llm_provider != "ollama":
        llm_key = typer.prompt(f"Enter {llm_provider} API key", hide_input=True, default="")
        if llm_key:
            _set_env_var(f"{llm_provider.upper()}_API_KEY", llm_key)

    cfg.llm = LLMConfig(provider=llm_provider, model=_default_model(llm_provider))

    # ── Embedding provider ───────────────────────────────────────────────────
    if _ollama_available():
        embed_providers = ["local (ollama)", "openai"]
        default_embed = "1"
    else:
        embed_providers = ["openai", "local (ollama)"]
        default_embed = "1"

    typer.echo("\nEmbedding provider")
    for i, p in enumerate(embed_providers, 1):
        typer.echo(f"  {i}. {p}")
    embed_choice = typer.prompt("Choose [1-2]", default=default_embed)
    try:
        embed_choice_name = embed_providers[int(embed_choice) - 1]
    except (ValueError, IndexError):
        embed_choice_name = embed_providers[0]

    if "openai" in embed_choice_name:
        embed_provider_name = "openai"
        embed_model = "text-embedding-3-small"
        embed_key = typer.prompt(
            "Enter OpenAI API key (or leave blank if already set)", hide_input=True, default=""
        )
        if embed_key:
            _set_env_var("OPENAI_API_KEY", embed_key)
    else:
        embed_provider_name = "ollama"
        embed_model = "nomic-embed-text"

    cfg.embedding = EmbedConfig(provider=embed_provider_name, model=embed_model)

    # ── Connectors — MANDATORY: configure at least one ───────────────────────
    typer.echo("\nWhere is your context?")
    typer.echo("  1. Local files (directory of markdown / text files)")
    typer.echo("  2. GitHub repository")
    typer.echo("  3. Both")
    connector_choice = typer.prompt("Choose [1-3]", default="1")

    if connector_choice == "2":
        connector_ids = ["github"]
    elif connector_choice == "3":
        connector_ids = ["files", "github"]
    else:
        connector_ids = ["files"]

    connector_configs: list[dict] = []
    for cid in connector_ids:
        if cid == "files":
            connector_configs.append(_configure_files_connector())
        elif cid == "github":
            connector_configs.append(_configure_github_connector())

    # Persist connectors to config NOW — before ingest
    cfg.connectors = connector_configs
    cfg.save(store.config_path)
    typer.echo(f"\n✓ Configuration saved ({len(connector_configs)} connector(s) configured).")

    # ── First ingestion — run automatically ──────────────────────────────────
    typer.echo("\n⏳ Running first ingestion…")
    total_pages = 0
    domains_seen: set[str] = set()
    ingest_ok = False

    try:
        from mm.ingestion.runner import run_connector as _run_connector
        from mm.embedding.providers import EmbeddingProvider

        embed_provider = EmbeddingProvider.from_config(
            {"provider": embed_provider_name, "model": embed_model}
        )

        for conn_cfg in connector_configs:
            result = _do_ingest(conn_cfg, store, embed_provider)
            pages_n = result.get("pages_created", 0) + result.get("pages_updated", 0)
            total_pages += pages_n
            typer.echo(
                f"  ✓ '{conn_cfg['connector']}' — {pages_n} page(s) ingested "
                f"({result['chunks_total']} chunks)."
            )
            domains_seen.update(_collect_domains(conn_cfg))

        ingest_ok = True
    except Exception as exc:  # noqa: BLE001
        typer.echo(f"\n  ✗ Ingestion failed: {exc}", err=True)
        typer.echo(
            "  Fix the issue and run: monkey-mind ingest --connector <name> --user "
            f"{username}",
            err=True,
        )

    # ── Test query — prove retrieval works ───────────────────────────────────
    query_ok = False
    if ingest_ok and total_pages > 0:
        typer.echo("\n🔍 Running test query to verify setup…")
        try:
            from mm.api.query import QueryEngine

            engine = QueryEngine()
            chunks = engine.retrieve(store, "What is this context about?", limit=3)
            if chunks:
                typer.echo(f"  ✓ Retrieval works — {len(chunks)} chunk(s) returned.")
                query_ok = True
            else:
                typer.echo(
                    "  ⚠ Retrieval returned 0 chunks. Embedding may need time to index.",
                    err=True,
                )
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"  ✗ Test query failed: {exc}", err=True)
    elif ingest_ok and total_pages == 0:
        typer.echo(
            "\n  ⚠ No pages were ingested (empty source?). "
            "Add content and re-run ingest.",
            err=True,
        )

    # ── Summary ──────────────────────────────────────────────────────────────
    n_domains = len(domains_seen) or len(cfg.domains)
    typer.echo("\n" + "━" * 60)
    if ingest_ok and (total_pages > 0 or query_ok):
        typer.echo("🎉 Your context library is ready!")
    else:
        typer.echo("⚠️  Setup partially complete — see warnings above before querying.")

    typer.echo(f"   User:       {username}")
    if raw_key:
        typer.echo(f"   API key:    {raw_key}  ← stored securely above")
    typer.echo(f"   LLM:        {cfg.llm.provider} / {cfg.llm.model}")
    typer.echo(f"   Embedding:  {cfg.embedding.provider}")
    typer.echo(
        f"   Connectors: {', '.join(c['connector'] for c in connector_configs)}"
    )
    typer.echo(f"   Pages:      {total_pages} ingested across {n_domains} domain(s)")
    typer.echo(f"\n   Try: monkey-mind query --user {username} \"<your question>\"")
    typer.echo("━" * 60)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ollama_available() -> bool:
    """Return True if local Ollama server responds."""
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        return resp.ok
    except Exception:
        return False


def _default_model(provider: str) -> str:
    return {
        "anthropic": "claude-haiku-4-5",
        "openai": "gpt-4o-mini",
        "ollama": "qwen2.5",
    }.get(provider, "claude-haiku-4-5")


def _set_env_var(key: str, value: str) -> None:
    """Write key=value to ~/.monkey-mind/.env and set in current process."""
    os.environ[key] = value
    env_path = DATA_ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    lines = [l for l in lines if not l.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lines) + "\n")
    typer.echo(f"  ✓ {key} saved to {env_path}")


def _configure_files_connector() -> dict:
    """Prompt for files connector config, validate path."""
    typer.echo("\nFile connector — local directory of markdown/text files")
    while True:
        dir_path = typer.prompt("Enter directory path to ingest").strip()
        p = Path(dir_path).expanduser()
        if p.exists() and p.is_dir():
            typer.echo(f"  ✓ Path exists: {p}")
            return {"connector": "files", "path": str(p)}
        typer.echo(f"  ✗ Not a directory: {p}. Please try again.", err=True)


def _configure_github_connector() -> dict:
    """Prompt for GitHub connector config, call validate()."""
    from mm.connectors.github import GitHubConnector

    typer.echo("\nGitHub connector — crawl a public (or token-accessible) repo")
    while True:
        gh_user = typer.prompt("Enter GitHub username").strip()
        token = typer.prompt(
            "Enter GitHub token (optional, press Enter to skip)", default=""
        )
        config: dict = {"username": gh_user}
        if token:
            config["github_token"] = token

        connector = GitHubConnector(config, None)
        typer.echo(f"  Validating GitHub user '{gh_user}'…")
        ok, msg = connector.validate()
        if ok:
            typer.echo(f"  ✓ GitHub user '{gh_user}' validated.")
            return {"connector": "github", **config}
        typer.echo(f"  ✗ Validation failed: {msg}. Please try again.", err=True)


def _do_ingest(conn_cfg: dict, store, embed_provider) -> dict:
    """Run the full ingestion pipeline for one connector config."""
    from mm.connectors.files import FilesConnector
    from mm.connectors.github import GitHubConnector
    from mm.ingestion.runner import run_connector

    cid = conn_cfg.get("connector", "")
    cfg_copy = {k: v for k, v in conn_cfg.items() if k != "connector"}

    if cid == "files":
        connector = FilesConnector(cfg_copy, None)
    elif cid == "github":
        connector = GitHubConnector(cfg_copy, None)
    else:
        raise ValueError(f"Unknown connector type: '{cid}'")

    return run_connector(connector, store, embed_provider)


def _collect_domains(conn_cfg: dict) -> list[str]:
    """Best-effort: return domain names for a connector config."""
    # Files connector pages typically end up in 'personal'; GitHub in 'projects'
    cid = conn_cfg.get("connector", "")
    return {"files": ["personal"], "github": ["projects"]}.get(cid, [])
