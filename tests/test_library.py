"""Tests for the read-only library views (mm.core.library), their API routes, CLI and /ui."""
from __future__ import annotations

import datetime
import sqlite3
from unittest.mock import patch

import chromadb
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from mm.connectors.base import ConnectorPage
from mm.core import library
from mm.core.store import UserStore
from mm.embedding.pipeline import CHUNK_OVERLAP_CHARS, build_chunks


class FakeEmbed:
    """Deterministic 3-d embedding: counts of a few marker words."""

    def embed(self, texts):
        return [[t.lower().count("run") + 0.01, t.lower().count("budget") + 0.01, 1.0] for t in texts]


def _iso(days_ago: int) -> str:
    return (datetime.datetime.utcnow() - datetime.timedelta(days=days_ago)).isoformat()


def _add_page(store: UserStore, page: ConnectorPage, days_ago: int) -> None:
    chunks = build_chunks([page])
    for c in chunks:
        c.metadata["updated_at"] = _iso(days_ago)
    client = chromadb.PersistentClient(path=str(store.chroma_dir))
    col = client.get_or_create_collection(store.collection_name())
    col.upsert(ids=[c.id for c in chunks], documents=[c.text for c in chunks],
               metadatas=[c.metadata for c in chunks],
               embeddings=FakeEmbed().embed([c.text for c in chunks]))
    con = sqlite3.connect(store.db_path)
    con.execute(
        "INSERT INTO pages (id, domain, type, title, source, connector, created_at, updated_at,"
        " staleness_threshold_days, confidence, tags, raw_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (page.id, page.domain, page.type, page.title, page.source, page.connector,
         _iso(days_ago), _iso(days_ago), page.staleness_threshold_days, page.confidence,
         ",".join(page.tags), "/home/someone/notes/" + page.id))
    con.commit()
    con.close()


@pytest.fixture
def store(tmp_path):
    s = UserStore(tmp_path, "alice")
    s.init()
    s.get_config()
    long_body = "".join(f"line {i:05d} about running.\n" for i in range(1400))  # > 24k chars
    _add_page(s, ConnectorPage(
        id="health/training", domain="health", type="note", title="Training plan",
        summary="Run three times a week.", detail_sections={"Log": long_body, "Goals": "Sub-25 5k."},
        source="files", source_ref="training.md", connector="files", tags=["running"],
        staleness_threshold_days=30), days_ago=2)
    _add_page(s, ConnectorPage(
        id="professional/budget", domain="professional", type="note", title="Budget 2026",
        summary="The budget is tight.", detail_sections={}, source="files", source_ref="budget.md",
        connector="files", staleness_threshold_days=30), days_ago=45)
    _add_page(s, ConnectorPage(
        id="professional/okrs", domain="professional", type="note", title="OKRs",
        summary="Ship the thing.", detail_sections={}, source="files", source_ref="okrs.md",
        connector="files", staleness_threshold_days=30), days_ago=20)
    return s


# ── freshness ────────────────────────────────────────────────────────────────

def test_freshness_levels():
    now = datetime.datetime(2026, 10, 3)
    assert library.freshness("2026-10-01T00:00:00", 30, now)["level"] == "fresh"
    assert library.freshness("2026-09-13T00:00:00", 30, now)["level"] == "ripening"
    assert library.freshness("2026-08-01T00:00:00", 30, now)["level"] == "stale"
    assert library.freshness("2026-10-01T00:00:00+00:00", 30, now)["age_days"] == 2
    assert library.freshness(None, 30, now)["level"] == "stale"
    assert library.freshness("not a date", None, now)["threshold_days"] == 30


# ── pages ────────────────────────────────────────────────────────────────────

def test_list_pages_filters(store):
    assert {p["id"] for p in library.list_pages(store)} == {
        "health/training", "professional/budget", "professional/okrs"}
    assert [p["id"] for p in library.list_pages(store, domain="health")] == ["health/training"]
    assert [p["id"] for p in library.list_pages(store, q="budg")] == ["professional/budget"]
    assert [p["id"] for p in library.list_pages(store, q="running")] == ["health/training"]  # tag
    assert [p["id"] for p in library.list_pages(store, stale_only=True)] == ["professional/budget"]


def test_list_pages_hides_local_paths(store):
    assert all("raw_path" not in p for p in library.list_pages(store))


def test_get_page_rebuilds_sections_without_overlap(store):
    page = library.get_page(store, "health/training")
    headings = [s["heading"] for s in page["sections"]]
    assert headings[0] == "Summary"
    assert set(headings) == {"Summary", "Log", "Goals"}
    log = next(s for s in page["sections"] if s["heading"] == "Log")["text"]
    # The long section was split with overlap; re-joined it must read exactly once, in order.
    assert log.count("line 00000 ") == 1 and log.count("line 01399 ") == 1
    nums = [int(line.split()[1]) for line in log.splitlines()]
    assert nums == list(range(1400))
    assert not log.startswith("[DETAIL")
    assert page["tokens"] == sum(s["tokens"] for s in page["sections"])
    assert CHUNK_OVERLAP_CHARS > 0


def test_get_page_missing(store):
    assert library.get_page(store, "nope") is None


# ── status / preview ─────────────────────────────────────────────────────────

def test_status(store):
    st = library.status(store)
    assert st["pages"] == 3 and st["stale"] == 1 and st["ripening"] == 1
    prof = next(d for d in st["domains"] if d["id"] == "professional")
    assert prof["pages"] == 2 and prof["stale"] == 1
    assert prof["threshold_days"] == next(
        d.staleness_threshold_days for d in store.get_config().domains if d.id == "professional")
    assert st["library_tokens"] > 0


def test_status_empty_library(tmp_path):
    s = UserStore(tmp_path, "bob")
    s.init()
    st = library.status(s)
    assert st["pages"] == 0 and st["library_tokens"] == 0 and st["recent_ingestions"] == []


def test_preview_is_retrieval_only(store):
    with patch("mm.embedding.providers.EmbeddingProvider.from_config", return_value=FakeEmbed()), \
         patch("mm.api.query.QueryEngine._call_llm") as llm:
        r = library.preview(store, "budget", limit=2)
    llm.assert_not_called()
    assert r["chunks"][0]["page_id"] == "professional/budget"
    assert r["tokens_sent"] == sum(c["tokens"] for c in r["chunks"])
    assert 0 < r["share"] < 1
    assert r["chunks"][0]["freshness"]["level"] == "stale"


# ── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(store):
    from mm.api.server import app, get_user_store

    app.dependency_overrides[get_user_store] = lambda: store
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_routes(client):
    assert client.get("/status").json()["pages"] == 3
    assert [p["id"] for p in client.get("/pages?stale=true").json()["pages"]] == ["professional/budget"]
    assert [p["id"] for p in client.get("/pages?q=okr").json()["pages"]] == ["professional/okrs"]
    page = client.get("/pages/health/training?content=true").json()
    assert page["sections"] and page["freshness"]["level"] == "fresh"
    assert "sections" not in client.get("/pages/health/training").json()
    assert client.get("/pages/missing").status_code == 404
    with patch("mm.embedding.providers.EmbeddingProvider.from_config", return_value=FakeEmbed()):
        r = client.post("/preview", json={"query": "run", "limit": 3}).json()
    assert r["chunks"][0]["page_id"] == "health/training"


def test_library_routes_need_a_key():
    from mm.api.server import app

    c = TestClient(app)
    for method, path in (("get", "/status"), ("post", "/preview"), ("get", "/pages?q=x")):
        assert getattr(c, method)(path).status_code in (401, 422)


def test_ui_served_locked_down():
    from mm.api.server import app

    c = TestClient(app)
    r = c.get("/ui")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    body = r.text
    # No third-party resources and no innerHTML: library content is rendered as text.
    import re as _re
    assert not _re.search(r'<(?:script|link|img|iframe)[^>]+(?:src|href)="https?://', body)
    assert "innerHTML" not in body and "fonts.googleapis" not in body
    assert c.get("/", follow_redirects=False).headers["location"] == "/ui"


def test_every_joke_has_a_plain_line():
    import json
    from pathlib import Path

    import mm

    d = Path(mm.__file__).parent / "voice"
    jokes = json.loads((d / "catalogue.json").read_text())["lines"]
    plain = json.loads((d / "plain.json").read_text())["lines"]
    assert len(jokes) > 15
    for key, entry in jokes.items():
        if key.startswith(("error.", "query.", "library.", "search.", "peek.", "fresh.")):
            assert plain.get(key, {}).get("plain"), f"{key} has a joke but no plain fact/fix"
        if "label" in entry:
            assert plain[key].get("label"), f"{key} has no plain label"


def test_voice_licences_are_marked():
    import json
    from pathlib import Path

    import mm

    d = Path(mm.__file__).parent / "voice"
    assert "CC BY-NC-ND 4.0" in json.loads((d / "catalogue.json").read_text())["license"]
    assert "Apache" in json.loads((d / "plain.json").read_text())["license"]
    assert "by-nc-nd/4.0" in (d / "LICENSE").read_text()


def test_plain_mode_and_missing_catalogue(monkeypatch):
    from mm import voice

    voice._load.cache_clear()
    joke, plain = voice.say("query.empty")
    assert joke and plain
    monkeypatch.setenv("MM_VOICE", "plain")
    assert voice.say("query.empty") == ("", plain)
    assert voice.label("fresh.stale") == "Stale"
    monkeypatch.delenv("MM_VOICE")
    monkeypatch.setattr(voice, "_read", lambda name: {} if name == "catalogue.json"
                        else {"query.empty": {"plain": "nothing"}})
    voice._load.cache_clear()
    assert voice.say("query.empty") == ("", "nothing")
    voice._load.cache_clear()


def test_ui_gets_voice_from_server():
    from mm.api.server import app

    c = TestClient(app)
    v = c.get("/ui/voice.json").json()
    assert v["error.auth"]["joke"] and v["error.auth"]["plain"]
    assert "const VOICE = {" not in c.get("/ui").text  # copy lives in mm/voice, not the page


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_status_pages_show(store, tmp_path, monkeypatch):
    from mm.cli import main as cli

    monkeypatch.setattr(cli, "DATA_ROOT", tmp_path)
    runner = CliRunner()
    r = runner.invoke(cli.app, ["status", "-u", "alice"])
    assert r.exit_code == 0 and "3 pages" in r.output and "Body Brain" in r.output
    r = runner.invoke(cli.app, ["pages", "-u", "alice", "--stale"])
    assert r.exit_code == 0 and "professional/budget" in r.output and "health/training" not in r.output
    r = runner.invoke(cli.app, ["show", "health/training", "-u", "alice"])
    assert r.exit_code == 0 and "Sub-25 5k." in r.output
    r = runner.invoke(cli.app, ["show", "nope", "-u", "alice"])
    assert r.exit_code == 1


def test_cli_peek(store, tmp_path, monkeypatch):
    from mm.cli import main as cli

    monkeypatch.setattr(cli, "DATA_ROOT", tmp_path)
    with patch("mm.embedding.providers.EmbeddingProvider.from_config", return_value=FakeEmbed()):
        r = CliRunner().invoke(cli.app, ["peek", "budget", "-u", "alice"])
    assert r.exit_code == 0 and "Budget 2026" in r.output and "No AI was called" in r.output


def test_every_brain_has_an_icon(store):
    st = library.status(store)
    icons = {d["id"]: d["icon"] for d in st["domains"]}
    assert icons["health"] == library.DEFAULT_ICONS["health"]
    assert all(icons.values())
    assert library.domain_icon("gardening") == library.domain_icon("gardening")  # stable
    assert library.domain_icon("gardening") in library.ICON_POOL
    assert library.domain_icon("gardening", "🌱") == "🌱"


def test_freshness_moods(monkeypatch):
    from mm import voice

    voice._load.cache_clear()
    assert [voice.lines()[f"fresh.{lv}"]["mood"] for lv in ("fresh", "ripening", "stale")] == [
        "😋", "😐", "😴"]
    monkeypatch.setenv("MM_VOICE", "plain")
    assert "mood" not in voice.lines()["fresh.stale"]
    voice._load.cache_clear()


def test_brain_cast_names(monkeypatch):
    from mm import voice

    voice._load.cache_clear()
    assert voice.brain_name("health", "Health") == "Body Brain"
    assert voice.brain_name("strategic", "Strategic") == "Big Picture Brain"
    assert voice.brain_name("gardening", "Gardening") == "Gardening Brain"
    assert voice.brain_name("health", "Health", "My Body") == "My Body"
    monkeypatch.setenv("MM_VOICE", "plain")
    assert voice.brain_name("health", "Health") == "Health"
    voice._load.cache_clear()


def test_mcp_get_page_falls_back_to_stored_text(store):
    """Uploaded/moved files have no source on disk; get_page must still return the text."""
    import json as _json

    with patch("mm.mcp.server._get_store", return_value=store):
        from mm.mcp.server import get_page
        result = get_page("health/training")
    data = _json.loads(result["content"][0]["text"])
    assert "Sub-25 5k." in data["content"] and "## Goals" in data["content"]
    assert "raw_path" not in data["metadata"]


def test_sample_notes_land_in_the_right_brains():
    from pathlib import Path

    from mm.config.user import UserConfig
    from mm.connectors.files import FilesConnector

    root = Path(__file__).resolve().parent.parent / "examples" / "sample-notes"
    pages = FilesConnector({"path": str(root)}, UserConfig.default("t")).ingest()
    by_domain = {}
    for p in pages:
        by_domain.setdefault(p.domain, []).append(p.title)
    assert set(by_domain) == {"health", "professional", "personal", "projects", "strategic",
                              "temporal"}
    assert len(pages) == 8
    assert {p.domain for p in pages if "okrs" in p.id or "priya" in p.id} == {"professional"}
    assert [p.domain for p in pages if "five-year" in p.id] == ["strategic"]


def test_domain_folder_wins_and_parent_path_is_ignored(tmp_path):
    from mm.config.user import UserConfig
    from mm.connectors.files import FilesConnector

    root = tmp_path / "projects" / "my-notes"  # parent path contains a keyword
    (root / "health").mkdir(parents=True)
    (root / "health" / "plan.md").write_text("# Plan\n\nRun.")
    (root / "misc.md").write_text("# Misc\n\nThings.")
    pages = FilesConnector({"path": str(root)}, UserConfig.default("t")).ingest()
    assert {p.title: p.domain for p in pages} == {"Plan": "health", "Misc": "personal"}
