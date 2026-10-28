"""Where a note lands: explicit signals first, then names, tags and the note's own text."""
from __future__ import annotations

from mm.connectors.files import FilesConnector, _detect_domain


def _write(root, rel, text):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def _domains(root, **config):
    pages = FilesConnector({"path": str(root), **config}, None).ingest()
    return {p.source_ref.removeprefix(str(root) + "/"): p.domain for p in pages}


def test_obsidian_vault_is_not_mostly_personal(tmp_path):
    _write(tmp_path, "Daily Notes/2026-10-20.md", "---\ntags:\n  - fitness\n---\n# Morning\nRan 5k.\n")
    _write(tmp_path, "Daily Notes/2026-10-21.md", "# Day\nMeeting with the client, then a client follow-up.\n")
    _write(tmp_path, "Inbox/Ideas.md", "# Ideas\nShip the feature, launch beta, fix the repo.\n")
    _write(tmp_path, "Resources/Direction.md", "---\ndomain: strategic\n---\n# Direction\nThree years.\n")
    _write(tmp_path, ".trash/old.md", "# Deleted in Obsidian\n")
    _write(tmp_path, ".obsidian/notes.md", "# App settings\n")
    assert _domains(tmp_path) == {
        "Daily Notes/2026-10-20.md": "health",        # frontmatter tag list
        "Daily Notes/2026-10-21.md": "professional",  # the note's own text
        "Inbox/Ideas.md": "projects",
        "Resources/Direction.md": "strategic",        # frontmatter domain:
    }


def test_keywords_match_whole_words_only(tmp_path):
    assert _detect_domain(tmp_path / "Labelling.md", root=tmp_path) == "personal"   # not "lab"
    assert _detect_domain(tmp_path / "planning.md", root=tmp_path) == "strategic"   # plan + ning


def test_a_passing_mention_does_not_decide(tmp_path):
    assert _detect_domain(tmp_path / "x.md", root=tmp_path, text="Had a meeting.") == "personal"


def test_folder_named_after_domain_beats_content(tmp_path):
    f = tmp_path / "health" / "notes.md"
    assert _detect_domain(f, root=tmp_path, text="client meeting client project") == "health"


def test_domain_map_folders(tmp_path):
    _write(tmp_path, "Areas/Day Job/Notes.md", "# Notes\nNothing telling here.\n")
    assert _domains(tmp_path, domain_map={"professional": ["day job"]}) == {
        "Areas/Day Job/Notes.md": "professional"}
