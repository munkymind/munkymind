"""Munkymind CLI entry point."""
import shutil
import typer
from pathlib import Path

app = typer.Typer(
    name="munkymind",
    help="Your AI tools forget you every conversation. Munkymind remembers everything.",
    no_args_is_help=True,
)

user_app = typer.Typer(help="Manage Munkymind users.")
domain_app = typer.Typer(help="Manage knowledge domains in config.yaml.")
app.add_typer(user_app, name="user")
app.add_typer(domain_app, name="domain")

import os
from mm import __version__
from mm.config.env import load_data_root_env

def _default_data_root() -> Path:
    # Renamed from Monkey Mind (v0.2.0): keep using an existing ~/.monkey-mind folder.
    new, old = Path.home() / '.munkymind', Path.home() / '.monkey-mind'
    return old if old.exists() and not new.exists() else new


DATA_ROOT = Path(os.environ.get('DATA_ROOT', str(_default_data_root())))
load_data_root_env(DATA_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# user subcommands
# ─────────────────────────────────────────────────────────────────────────────

@user_app.command('create')
def user_create(name: str = typer.Argument(..., help='Username (alphanumeric + hyphens)')):
    """Create user directory, generate API key, and print it once."""
    from mm.core.store import UserStore
    from mm.auth.keys import generate_key, save_key_hash

    store = UserStore(DATA_ROOT, name)
    store.init()
    cfg = store.get_config()
    cfg.save(store.config_path)

    raw_key, hashed = generate_key()
    save_key_hash(store.user_dir, hashed)

    typer.echo(f"✓ User '{name}' created.")
    typer.echo(f"  API key (shown once, store it safely):\n\n  {raw_key}\n")


@user_app.command('delete')
def user_delete(
    name: str = typer.Argument(...),
    confirm: bool = typer.Option(False, '--confirm', help='Required to actually delete'),
):
    """Remove all data for a user (API key immediately invalidated)."""
    from mm.core.store import UserStore

    if not confirm:
        typer.echo("Pass --confirm to actually delete the user.", err=True)
        raise typer.Exit(1)

    store = UserStore(DATA_ROOT, name)
    if store.user_dir.exists():
        shutil.rmtree(store.user_dir)
        typer.echo(f"✓ User '{name}' and all associated data deleted.")
    else:
        typer.echo(f"User '{name}' not found.", err=True)
        raise typer.Exit(1)


@user_app.command('rotate-key')
def user_rotate_key(name: str = typer.Argument(...)):
    """Generate a new API key, invalidating the old one."""
    from mm.core.store import UserStore
    from mm.auth.keys import generate_key, save_key_hash

    store = UserStore(DATA_ROOT, name)
    if not store.user_dir.exists():
        typer.echo(f"User '{name}' not found.", err=True)
        raise typer.Exit(1)

    raw_key, hashed = generate_key()
    save_key_hash(store.user_dir, hashed)
    typer.echo(f"✓ API key rotated for '{name}'.")
    typer.echo(f"  New API key (shown once):\n\n  {raw_key}\n")


# ─────────────────────────────────────────────────────────────────────────────
# domain subcommands
# ─────────────────────────────────────────────────────────────────────────────

def _load_user_config(username: str):
    """Helper: load UserConfig for a given user."""
    from mm.config.user import UserConfig
    from mm.core.store import UserStore

    store = UserStore(DATA_ROOT, username)
    if not store.config_path.exists():
        typer.echo(f"No config found for user '{username}'. Run 'munkymind setup' first.", err=True)
        raise typer.Exit(1)
    return UserConfig.load(store.config_path), store


@domain_app.command('add')
def domain_add(
    domain_id: str = typer.Argument(..., help='Domain identifier (e.g. health)'),
    label: str = typer.Argument(..., help='Human-readable label'),
    username: str = typer.Option(..., '--user', '-u', help='Username whose config to modify'),
    staleness: int = typer.Option(30, '--staleness', help='Staleness threshold in days'),
):
    """Add a new domain to config.yaml."""
    from mm.config.user import DomainConfig

    cfg, store = _load_user_config(username)

    # Check for duplicate
    if any(d.id == domain_id for d in cfg.domains):
        typer.echo(f"Domain '{domain_id}' already exists.", err=True)
        raise typer.Exit(1)

    cfg.domains.append(DomainConfig(id=domain_id, label=label, staleness_threshold_days=staleness))
    cfg.save(store.config_path)
    typer.echo(f"✓ Domain '{domain_id}' ({label}) added.")


@domain_app.command('rename')
def domain_rename(
    domain_id: str = typer.Argument(..., help='Domain identifier to rename'),
    new_label: str = typer.Argument(..., help='New label'),
    username: str = typer.Option(..., '--user', '-u', help='Username whose config to modify'),
):
    """Rename a domain label in config.yaml."""
    cfg, store = _load_user_config(username)

    for d in cfg.domains:
        if d.id == domain_id:
            old_label = d.label
            d.label = new_label
            cfg.save(store.config_path)
            typer.echo(f"✓ Domain '{domain_id}' renamed: '{old_label}' → '{new_label}'.")
            return

    typer.echo(f"Domain '{domain_id}' not found.", err=True)
    raise typer.Exit(1)


@domain_app.command('remove')
def domain_remove(
    domain_id: str = typer.Argument(..., help='Domain identifier to remove'),
    username: str = typer.Option(..., '--user', '-u', help='Username whose config to modify'),
):
    """Remove a domain from config.yaml."""
    cfg, store = _load_user_config(username)

    before = len(cfg.domains)
    cfg.domains = [d for d in cfg.domains if d.id != domain_id]
    if len(cfg.domains) == before:
        typer.echo(f"Domain '{domain_id}' not found.", err=True)
        raise typer.Exit(1)

    cfg.save(store.config_path)
    typer.echo(f"✓ Domain '{domain_id}' removed.")


# ─────────────────────────────────────────────────────────────────────────────
# setup
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def setup():
    """Interactive setup wizard — zero to working context library in < 30 minutes."""
    from mm.cli.wizard import run_wizard
    run_wizard()


# ─────────────────────────────────────────────────────────────────────────────
# ingest
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def ingest(
    connector: str = typer.Option(..., '--connector', '-c', help='Connector ID to run (e.g. files, github)'),
    username: str = typer.Option(..., '--user', '-u', help='Username to ingest for'),
    dry_run: bool = typer.Option(False, '--dry-run', help='Validate and ingest but skip writing to DB/vector store'),
):
    """Run a specific connector on demand."""
    from mm.core.store import UserStore
    from mm.config.user import UserConfig
    from mm.connectors.files import FilesConnector
    from mm.connectors.github import GitHubConnector

    store = UserStore(DATA_ROOT, username)
    if not store.config_path.exists():
        typer.echo(f"No config found for user '{username}'. Run 'munkymind setup' first.", err=True)
        raise typer.Exit(1)

    cfg = UserConfig.load(store.config_path)

    # Find matching connector config
    conn_cfgs = [c for c in cfg.connectors if c.get('connector') == connector]
    if not conn_cfgs:
        typer.echo(f"No connector '{connector}' configured for user '{username}'.", err=True)
        raise typer.Exit(1)

    conn_cfg = conn_cfgs[0]
    cfg_copy = {k: v for k, v in conn_cfg.items() if k != 'connector'}

    if connector == 'files':
        conn_obj = FilesConnector(cfg_copy, cfg)
    elif connector == 'github':
        conn_obj = GitHubConnector(cfg_copy, cfg)
    else:
        typer.echo(f"Unknown connector '{connector}'.", err=True)
        raise typer.Exit(1)

    typer.echo(f"Validating connector '{connector}'...")
    ok, msg = conn_obj.validate()
    if not ok:
        typer.echo(f"✗ Validation failed: {msg}", err=True)
        raise typer.Exit(1)

    from mm.embedding.providers import EmbeddingProvider
    from mm.ingestion.runner import run_connector

    typer.echo(f"Ingesting via '{connector}'...")
    embed_provider = EmbeddingProvider.from_config(
        {"provider": cfg.embedding.provider, "model": cfg.embedding.model}
    )
    result = run_connector(conn_obj, store, embed_provider, dry_run=dry_run)

    if dry_run:
        typer.echo(f"✓ Dry run: {result['chunks_total']} chunk(s) built, nothing written.")
    else:
        pages_n = result["pages_created"] + result["pages_updated"]
        typer.echo(
            f"✓ Ingested {pages_n} page(s) via '{connector}' "
            f"({result['pages_created']} new, {result['pages_updated']} updated, "
            f"{result['chunks_total']} chunks)."
        )


# ─────────────────────────────────────────────────────────────────────────────
# query
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def query(
    q: str = typer.Argument(..., help="Your question"),
    username: str = typer.Option(..., '--user', '-u', help='Username to query as'),
    limit: int = typer.Option(10, '--limit', help='Max chunks to retrieve'),
):
    """Query your context library."""
    from mm.api.query import QueryEngine
    from mm.core.store import UserStore

    store = UserStore(DATA_ROOT, username)
    if not store.config_path.exists():
        typer.echo(f"No config found for user '{username}'. Run 'munkymind setup' first.", err=True)
        raise typer.Exit(1)

    engine = QueryEngine()
    chunks = engine.retrieve(store, q, limit=limit)
    result = engine.synthesise(q, chunks, store.get_config())

    typer.echo(result["answer"])
    if result.get("sources"):
        typer.echo("\nSources:")
        for src in result["sources"]:
            domain = f" [{src['domain']}]" if src.get("domain") else ""
            typer.echo(f"  - {src['path']}{domain}")
    for warning in result.get("staleness_warnings", []):
        typer.echo(f"⚠ {warning}", err=True)


# ─────────────────────────────────────────────────────────────────────────────
# eval
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def eval(
    api_url: str = typer.Option("http://localhost:8000", '--api-url', help='Running Munkymind API'),
    api_key: str = typer.Option(..., '--api-key', envvar='MM_API_KEY', help='Your mm_sk_ API key'),
    output: str = typer.Option("text", help="Output format: text | json"),
):
    """Run the eval suite against a running Munkymind API."""
    from mm.eval.runner import print_results, run_all

    if output == "text":
        typer.echo("Running eval suite...")
    print_results(run_all(api_url=api_url, api_key=api_key), output=output)


# ─────────────────────────────────────────────────────────────────────────────
# version callback
# ─────────────────────────────────────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def version(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
):
    if version:
        typer.echo(f"munkymind {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


if __name__ == "__main__":
    app()
