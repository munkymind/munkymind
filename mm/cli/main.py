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
    icon: str = typer.Option('', '--icon', help='Emoji for this brain in the viewer'),
):
    """Add a new domain to config.yaml."""
    from mm.config.user import DomainConfig

    cfg, store = _load_user_config(username)

    # Check for duplicate
    if any(d.id == domain_id for d in cfg.domains):
        typer.echo(f"Domain '{domain_id}' already exists.", err=True)
        raise typer.Exit(1)

    cfg.domains.append(DomainConfig(id=domain_id, label=label, staleness_threshold_days=staleness,
                                    icon=icon))
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
# library viewer: status / pages / show / peek (read-only)
# ─────────────────────────────────────────────────────────────────────────────

_HUNGER_COLOUR = {"fresh": "green", "ripening": "yellow", "stale": "red"}


def _say(console, key: str, **values) -> None:
    """Print an event's message: the joke (if any), then the plain fact or fix, dimmed."""
    from rich.markup import escape
    from mm import voice

    joke, plain = voice.say(key, **values)
    if joke:
        console.print(escape(joke), highlight=False)
    if plain:
        console.print(f"[dim]{escape(plain)}[/dim]", highlight=False)


def _open_store(username: str):
    from mm.core.store import UserStore

    store = UserStore(DATA_ROOT, username)
    if not store.config_path.exists():
        typer.echo(f"No config found for user '{username}'. Run 'munkymind setup' first.", err=True)
        raise typer.Exit(1)
    return store


def _brain(domain: str, labels: dict) -> str:
    label = labels.get(domain) or domain.title()
    return label if label.lower().endswith("brain") else f"{label} Brain"


def _hunger(freshness: dict) -> str:
    from mm import voice

    level = freshness["level"]
    colour = _HUNGER_COLOUR[level]
    return f"[{colour}]{voice.label('fresh.' + level) or level}[/{colour}]"


def _age(freshness: dict) -> str:
    age = freshness.get("age_days")
    return "unknown" if age is None else ("today" if age == 0 else f"{age}d ago")


@app.command()
def status(username: str = typer.Option(..., '--user', '-u', help='Username')):
    """How your brains are doing: pages, freshness and recent feeding."""
    from rich.console import Console
    from rich.table import Table
    from mm.core import library

    console = Console()
    st = library.status(_open_store(username))
    if not st["pages"]:
        _say(console, "library.empty", fix=f"munkymind ingest -c files -u {username}")
        return
    fresh = st["pages"] - st["stale"] - st["ripening"]
    console.print(f"[bold]{st['pages']} pages[/bold] · ≈ {st['library_tokens']:,} tokens · "
                  f"{fresh} {_hunger({'level': 'fresh'})} · "
                  f"{st['ripening']} {_hunger({'level': 'ripening'})} · "
                  f"{st['stale']} {_hunger({'level': 'stale'})}")
    table = Table(show_edge=False, header_style="bold")
    for col in ("Brain", "Pages", "Peckish", "Starving", "Last fed"):
        table.add_column(col)
    for d in st["domains"]:
        label = d["label"] if d["label"].lower().endswith("brain") else f"{d['label']} Brain"
        table.add_row(f"{d['icon']} {label}", str(d["pages"]), str(d["ripening"]), str(d["stale"]),
                      (d["last_fed"] or "never")[:10])
    console.print(table)
    hungry = [d for d in st["domains"] if d["stale"]]
    if hungry:
        worst = max(hungry, key=lambda d: d["stale"] / max(d["pages"], 1))
        label = worst["label"] if worst["label"].lower().endswith("brain") else f"{worst['label']} Brain"
        _say(console, "fresh.stale_brain", brain=label, n=worst["stale"],
             date=(worst["last_fed"] or "never")[:10])
    else:
        _say(console, "fresh.all_good")


@app.command()
def pages(
    username: str = typer.Option(..., '--user', '-u', help='Username'),
    domain: str = typer.Option(None, '--domain', '-d', help='Only this domain'),
    search: str = typer.Option(None, '--search', '-s', help='Match titles, ids and tags'),
    stale: bool = typer.Option(False, '--stale', help='Only pages past their freshness window'),
):
    """List what's in your library, newest first."""
    from rich.console import Console
    from rich.table import Table
    from mm.core import library

    console = Console()
    store = _open_store(username)
    rows = library.list_pages(store, domain=domain, q=search, stale_only=stale)
    if not rows:
        _say(console, "search.none", q=search or "")
        return
    labels = {d.id: d.label for d in store.get_config().domains}
    table = Table(show_edge=False, header_style="bold")
    for col in ("Page", "Title", "Brain", "Freshness", "Updated"):
        table.add_column(col, overflow="fold")
    for p in rows:
        table.add_row(p["id"], p.get("title") or "", _brain(p["domain"], labels),
                      _hunger(p["freshness"]), _age(p["freshness"]))
    console.print(table)
    console.print(f"[dim]{len(rows)} page(s). Read one with: munkymind show <page> -u {username}[/dim]")


@app.command()
def show(
    page_id: str = typer.Argument(..., help='Page id (from `munkymind pages`)'),
    username: str = typer.Option(..., '--user', '-u', help='Username'),
):
    """Read one page, with where it came from and how fresh it is."""
    from rich.console import Console
    from mm.core import library

    console = Console()
    store = _open_store(username)
    page = library.get_page(store, page_id)
    if page is None:
        _say(console, "error.missing")
        console.print(f"[dim]List pages with: munkymind pages -u {username}[/dim]")
        raise typer.Exit(1)
    labels = {d.id: d.label for d in store.get_config().domains}
    f = page["freshness"]
    console.print(f"[bold]{page.get('title') or page_id}[/bold]")
    console.print(f"{_brain(page['domain'], labels)} · {_hunger(f)} (updated {_age(f)}, "
                  f"stale after {f['threshold_days']}d) · ≈ {page['tokens']:,} tokens")
    console.print(f"[dim]Source: {page.get('source') or '—'} · fed by {page.get('connector') or '—'}"
                  f" · confidence {page.get('confidence') or '—'}[/dim]\n")
    for section in page["sections"]:
        console.print(f"[bold magenta]## {section['heading']}[/bold magenta]")
        console.print(section["text"], markup=False, highlight=False)
        console.print()


@app.command()
def peek(
    q: str = typer.Argument(..., help='A question your AI might ask'),
    username: str = typer.Option(..., '--user', '-u', help='Username'),
    limit: int = typer.Option(8, '--limit', help='Max snippets'),
):
    """See exactly what your AI would be sent for a question. No LLM call."""
    from rich.console import Console
    from mm.core import library

    console = Console()
    result = library.preview(_open_store(username), q, limit=limit)
    if not result["chunks"]:
        _say(console, "query.empty")
        return
    share = result["share"] * 100
    top = result["chunks"][0]
    labels = {d.id: d.label for d in _open_store(username).get_config().domains}
    n = len(result["chunks"])
    _say(console, "peek.result_big" if result["share"] > 0.5 else "peek.result",
         brain=_brain(top["domain"], labels), chunks=f"{n} snippet{'s' if n != 1 else ''}",
         sent=f"{result['tokens_sent']:,}", total=f"{result['library_tokens']:,}",
         share=f"{'<1' if 0 < share < 1 else round(share)}%")
    console.print("[dim]No AI was called; this is the retrieval step only.[/dim]\n")
    for i, c in enumerate(result["chunks"], 1):
        console.print(f"[bold]{i}. {c['title'] or c['page_id']}[/bold] "
                      f"[dim]· {c['section']} · ≈ {c['tokens']:,} tokens · {c['page_id']}[/dim]")
        text = c["text"] if len(c["text"]) <= 400 else c["text"][:400] + " …"
        console.print(text, markup=False, highlight=False)
        console.print()


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
