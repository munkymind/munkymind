"""`munkymind setup` driven entirely by flags (used by setup.sh) must never prompt."""
from __future__ import annotations

import pytest
import typer
import yaml

from mm.cli import wizard


def _no_prompts(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard, "DATA_ROOT", tmp_path)

    def fail(text, *a, **k):
        raise AssertionError(f"prompted: {text}")

    monkeypatch.setattr(wizard.typer, "prompt", fail)
    monkeypatch.setattr(wizard.typer, "confirm", fail)
    monkeypatch.setattr(wizard, "_ollama_available", lambda: False)
    monkeypatch.setattr(wizard, "_do_ingest", lambda *a, **k: {"pages_created": 0, "chunks_total": 0})


def test_flags_answer_every_question(monkeypatch, tmp_path):
    _no_prompts(monkeypatch, tmp_path)
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("# A\nhello")
    answers = {"username": "u1", "llm": "openai", "embed": "openai", "notes": str(notes), "yes": True}
    wizard.run_wizard(answers)
    # A re-run with the same answers reconfigures without asking
    wizard.run_wizard(answers)

    cfg = yaml.safe_load((tmp_path / "users" / "u1" / "config.yaml").read_text())
    assert cfg["llm"]["provider"] == "openai"
    assert cfg["embedding"]["provider"] == "openai"
    assert cfg["connectors"][0]["path"] == str(notes)


def test_bad_notes_path_fails_instead_of_asking(monkeypatch, tmp_path):
    _no_prompts(monkeypatch, tmp_path)
    answers = {"username": "u2", "llm": "openai", "embed": "openai",
               "notes": str(tmp_path / "missing"), "yes": True}
    with pytest.raises(typer.Exit) as exc:
        wizard.run_wizard(answers)
    assert exc.value.exit_code == 2
