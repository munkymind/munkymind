"""Load the API keys the setup wizard saves to <DATA_ROOT>/.env."""
from __future__ import annotations

import os
from pathlib import Path


def load_data_root_env(data_root: Path) -> None:
    """Set KEY=value lines from <data_root>/.env into os.environ.

    Existing environment variables win, so Docker/.env or shell exports
    always take precedence over what the wizard saved.
    """
    env_path = Path(data_root) / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def ollama_url() -> str:
    """Base URL of the Ollama server. In Docker, localhost is the container itself, so
    Compose sets OLLAMA_URL to the host (http://host.docker.internal:11434)."""
    return (os.environ.get("OLLAMA_URL") or "http://localhost:11434").rstrip("/")
