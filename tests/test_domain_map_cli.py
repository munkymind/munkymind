"""`munkymind domain map` sends notes in named folders to a domain on the next ingest."""
from __future__ import annotations

import yaml
from typer.testing import CliRunner

from mm.cli import main
from mm.config.env import ollama_url


def test_domain_map_adds_words_once(monkeypatch, tmp_path):
    user = tmp_path / "users" / "u1"
    user.mkdir(parents=True)
    (user / "config.yaml").write_text(yaml.safe_dump(
        {"user_id": "u1", "connectors": [{"connector": "files", "path": "/notes"}]}))
    monkeypatch.setattr(main, "DATA_ROOT", tmp_path)
    runner = CliRunner()
    for _ in range(2):
        r = runner.invoke(main.app, ["domain", "map", "professional", "Day Job", "meetings", "--user", "u1"])
        assert r.exit_code == 0, r.output
    cfg = yaml.safe_load((user / "config.yaml").read_text())
    assert cfg["connectors"][0]["domain_map"] == {"professional": ["Day Job", "meetings"]}


def test_ollama_url_is_configurable(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert ollama_url() == "http://localhost:11434"
    monkeypatch.setenv("OLLAMA_URL", "http://host.docker.internal:11434/")
    assert ollama_url() == "http://host.docker.internal:11434"
