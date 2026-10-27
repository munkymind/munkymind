"""Tests for FilesConnector and ingestion runner."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mm.connectors.files import FilesConnector
from mm.connectors.base import ConnectorPage
from mm.core.store import UserStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_connector(tmp_path: Path, extra: dict | None = None) -> FilesConnector:
    config = {"path": str(tmp_path)}
    if extra:
        config.update(extra)
    return FilesConnector(config=config, user_config=None)


def make_user_store(tmp_path: Path) -> UserStore:
    store = UserStore(data_root=tmp_path / "data", user_id="testuser")
    store.init()
    return store


def make_mock_embed(dim: int = 1536):
    mock = MagicMock()
    mock.embed.return_value = [[0.1] * dim]
    return mock


# ---------------------------------------------------------------------------
# Validate tests
# ---------------------------------------------------------------------------

def test_validate_valid_path(tmp_path):
    connector = make_connector(tmp_path)
    ok, msg = connector.validate()
    assert ok is True
    assert msg == "ok"


def test_validate_missing_path(tmp_path):
    connector = FilesConnector(
        config={"path": str(tmp_path / "nonexistent_dir_xyz")},
        user_config=None,
    )
    ok, msg = connector.validate()
    assert ok is False
    assert "does not exist" in msg.lower() or "nonexistent" in msg


# ---------------------------------------------------------------------------
# Ingest tests
# ---------------------------------------------------------------------------

def test_ingest_markdown(tmp_path):
    md_file = tmp_path / "notes.md"
    md_file.write_text(
        "---\ntitle: My Notes\nauthor: Test\n---\n"
        "Some intro text here.\n\n"
        "## Section One\nContent of section one.\n\n"
        "## Section Two\nContent of section two.\n"
    )
    connector = make_connector(tmp_path)
    pages = connector.ingest()
    assert len(pages) == 1
    page = pages[0]
    assert isinstance(page, ConnectorPage)
    assert page.summary  # non-empty
    assert "Section One" in page.detail_sections or "Introduction" in page.detail_sections
    # At least one of the sections should exist
    assert len(page.detail_sections) >= 1


def test_ingest_txt(tmp_path):
    txt_file = tmp_path / "readme.txt"
    txt_file.write_text("Hello world.\nThis is a plain text file.\n")
    connector = make_connector(tmp_path)
    pages = connector.ingest()
    assert len(pages) == 1
    page = pages[0]
    assert page.connector == "files"
    assert page.summary


def test_domain_mapping(tmp_path):
    f = tmp_path / "health_journal.md"
    f.write_text("# Health notes\nFelt great today.\n")
    connector = make_connector(tmp_path)
    pages = connector.ingest()
    assert pages[0].domain == "health"


def test_domain_map_override(tmp_path):
    # A file called 'notes.txt' normally maps to 'personal',
    # but domain_map config overrides to 'strategic' when 'notes' keyword is listed.
    f = tmp_path / "random_notes.txt"
    f.write_text("Just some text.")
    connector = FilesConnector(
        config={
            "path": str(tmp_path),
            "domain_map": {"strategic": ["random_notes"]},
        },
        user_config=None,
    )
    pages = connector.ingest()
    assert pages[0].domain == "strategic"


def test_provenance(tmp_path):
    f = tmp_path / "document.txt"
    f.write_text("Some content here.")
    connector = make_connector(tmp_path)
    pages = connector.ingest()
    assert len(pages) == 1
    page = pages[0]
    assert page.connector == "files"
    assert str(f) == page.source_ref


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------

def test_ingestion_idempotent(tmp_path):
    from mm.ingestion.runner import run_connector

    # Create some files
    (tmp_path / "files").mkdir()
    files_dir = tmp_path / "files"
    (files_dir / "alpha.txt").write_text("Alpha content about health and fitness.")
    (files_dir / "beta.txt").write_text("Beta content about work and career.")

    store = make_user_store(tmp_path)
    mock_embed = make_mock_embed()
    # Make embed return right number of vectors per call
    mock_embed.embed.side_effect = lambda texts: [[0.1] * 1536 for _ in texts]

    connector1 = FilesConnector(config={"path": str(files_dir)}, user_config=None)
    result1 = run_connector(connector1, store, mock_embed)
    assert result1["pages_created"] == 2
    assert result1["pages_updated"] == 0
    assert result1["status"] == "ok"

    # Second run — same files, nothing changed: nothing re-embedded
    connector2 = FilesConnector(config={"path": str(files_dir)}, user_config=None)
    result2 = run_connector(connector2, store, mock_embed)
    assert result2["pages_created"] == 0
    assert result2["pages_updated"] == 0
    assert result2["pages_unchanged"] == 2
    assert result2["chunks_total"] == 0
    assert result2["status"] == "ok"
