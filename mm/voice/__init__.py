"""User-facing messages: a plain-language line for every event, plus the personality layer.

- plain.json (Apache 2.0): the literal fact or fix for each event. Always shown.
- catalogue.json (CC BY-NC-ND 4.0, see LICENSE in this folder): the jokes and labels. A
  "joke" may be a list of variants; one is picked at random each time.
  Optional: if it's missing, or MM_VOICE=plain is set, only the plain lines are used.

Only human-facing surfaces (CLI, /ui) use this. API and MCP output stays plain.
"""
from __future__ import annotations

import json
import os
import random
import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def _read(name: str) -> dict:
    try:
        return json.loads((_DIR / name).read_text(encoding="utf-8")).get("lines", {})
    except (OSError, ValueError):
        return {}


def plain_mode() -> bool:
    return os.environ.get("MM_VOICE", "").strip().lower() == "plain"


@lru_cache(maxsize=2)
def _load(plain: bool) -> dict[str, dict]:
    lines = {k: dict(v) for k, v in _read("plain.json").items()}
    if not plain:
        for key, entry in _read("catalogue.json").items():
            lines.setdefault(key, {}).update(entry)
    return lines


def lines() -> dict[str, dict]:
    """All messages: {key: {joke?, plain?, label?}}."""
    return _load(plain_mode())


def fill(text: str, **values) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text or "")


def say(key: str, **values) -> tuple[str, str]:
    """(joke, plain) for an event, placeholders filled. Either may be ''."""
    entry = lines().get(key, {})
    joke = entry.get("joke", "")
    if isinstance(joke, list):  # rotating variants: pick one each time
        joke = random.choice(joke) if joke else ""
    return fill(joke, **values), fill(entry.get("plain", ""), **values)


def label(key: str) -> str:
    return lines().get(key, {}).get("label", "")


def brain_name(domain_id: str, label: str = "", custom: str = "") -> str:
    """Display name for a domain: the user's own name for it, the cast name from the
    catalogue (health → Body Brain), '<Label> Brain', or just the label in plain mode."""
    if custom:
        return custom
    label = label or domain_id.replace("-", " ").title()
    entry = lines().get(f"cast.{domain_id}") or lines().get("cast._other") or {}
    return fill(entry.get("name", "{label}"), label=label)
