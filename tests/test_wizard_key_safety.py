"""Setup must never silently replace an existing API key (an interrupted setup used to)."""
from __future__ import annotations

import pytest

from mm.auth.keys import load_key_hash
from mm.cli import wizard


class Interrupted(Exception):
    pass


def _run_until_llm_prompt(monkeypatch, tmp_path, username="u1", reconfigure=True):
    """Run setup, answer the username (and reconfigure) prompts, then stop at the next prompt."""
    monkeypatch.setattr(wizard, "DATA_ROOT", tmp_path)

    def prompt(text, *a, **k):
        if text.startswith("Step 1"):
            return username
        raise Interrupted(text)

    monkeypatch.setattr(wizard.typer, "prompt", prompt)
    monkeypatch.setattr(wizard.typer, "confirm", lambda *a, **k: reconfigure)
    with pytest.raises(Interrupted):
        wizard.run_wizard()
    return tmp_path / "users" / username


def test_interrupted_first_setup_leaves_a_complete_user(monkeypatch, tmp_path):
    user_dir = _run_until_llm_prompt(monkeypatch, tmp_path)
    assert load_key_hash(user_dir) is not None
    assert (user_dir / "config.yaml").exists() or any(user_dir.glob("config*"))


def test_rerun_after_interruption_keeps_the_key(monkeypatch, tmp_path):
    user_dir = _run_until_llm_prompt(monkeypatch, tmp_path)
    first = load_key_hash(user_dir)
    _run_until_llm_prompt(monkeypatch, tmp_path)
    assert load_key_hash(user_dir) == first


def test_key_without_config_is_kept(monkeypatch, tmp_path, capsys):
    # The tester's exact state: key hash written, config never saved
    user_dir = _run_until_llm_prompt(monkeypatch, tmp_path)
    first = load_key_hash(user_dir)
    for f in user_dir.glob("config*"):
        f.unlink()
    _run_until_llm_prompt(monkeypatch, tmp_path)
    assert load_key_hash(user_dir) == first
    assert "Keeping it" in capsys.readouterr().out
