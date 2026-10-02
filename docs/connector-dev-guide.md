# Connector Developer Guide

Build a Munkymind connector and add new data sources to the ecosystem.

---

## Overview

Connectors are the extensibility point of Munkymind. The core engine never changes when a new connector is added — connectors plug in via Python entry points.

**Examples of connectors the community could build:**
- Obsidian vault connector
- Notion database connector
- Gmail connector (OAuth)
- Readwise highlights connector
- Linear/Jira issue connector
- Apple Health connector

---

## The Interface

Every connector implements `BaseConnector` from `mm.connectors.base`:

```python
from mm.connectors.base import BaseConnector, ConnectorPage
from typing import Any

class MyConnector(BaseConnector):
    connector_id = "myconnector"           # unique slug
    display_name = "My Connector"
    description = "Ingests data from My Source"

    def validate(self) -> tuple[bool, str]:
        """
        Check that the connector is properly configured and can reach its source.
        Called by the CLI wizard before running ingest — gives the user early
        feedback if something is wrong.

        Returns:
            (True, "ok") if everything is valid
            (False, "human-readable error message") if something is wrong
        """
        ...

    def schema(self) -> dict[str, Any]:
        """
        Return a JSON Schema dict describing the config block for this connector.
        Used by the CLI wizard to prompt for configuration.

        Example:
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "Directory to ingest"}
            }, "required": ["path"]}
        """
        ...

    def ingest(self, progress_cb=None) -> list[ConnectorPage]:
        """
        Fetch content from the source and return ConnectorPage objects.

        Args:
            progress_cb: Optional callback(current, total, message) for CLI progress.

        Returns:
            list of ConnectorPage objects ready for embedding.

        Must be idempotent: running again updates pages, does not duplicate.
        Raises ConnectorError on unrecoverable failure.
        """
        ...
```

---

## ConnectorPage

The output of `ingest()` is a list of `ConnectorPage` objects:

```python
@dataclass
class ConnectorPage:
    id: str                          # stable unique ID, e.g. "obsidian::Daily Notes::2026-08-01"
    domain: str                      # target domain slug: "health", "professional", etc.
    type: str                        # page type: "note", "profile", "repo", "highlight"
    title: str
    summary: str                     # 100-200 token self-contained summary
    detail_sections: dict[str, str]  # heading → body text
    source: str                      # human-readable: "Obsidian vault at ~/notes"
    source_ref: str                  # machine-readable: file path, URL, username
    connector: str                   # connector_id
    confidence: str = "medium"       # low | medium | high
    tags: list[str] = field(default_factory=list)
    staleness_threshold_days: int = 30
```

**Key rules:**
- `id` must be stable across runs — same content → same id. Use a hash or a natural key (file path, URL).
- `summary` must be self-contained (100–200 tokens). It's embedded as the retrieval anchor.
- `detail_sections` provide depth. Each key becomes a `[DETAIL:{key}]` chunk.
- Map content to the most appropriate `domain`. Use keyword heuristics or let the user configure `domain_map`.

---

## Example: Minimal Connector

```python
from mm.connectors.base import BaseConnector, ConnectorPage

class HelloWorldConnector(BaseConnector):
    connector_id = "hello"
    display_name = "Hello World"
    description = "Returns a single test page"

    def validate(self) -> tuple[bool, str]:
        return True, "ok"

    def schema(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    def ingest(self, progress_cb=None) -> list[ConnectorPage]:
        return [
            ConnectorPage(
                id="hello::world",
                domain="personal",
                type="note",
                title="Hello World",
                summary="A test page from the Hello World connector.",
                detail_sections={"Content": "This is a test page."},
                source="Hello World connector",
                source_ref="hello://world",
                connector=self.connector_id,
            )
        ]
```

---

## Registering Your Connector

Connectors register via Python entry points — no changes to core Munkymind required.

In your connector package's `pyproject.toml`:

```toml
[project.entry-points."munkymind.connectors"]
hello = "my_package.connector:HelloWorldConnector"
```

After installing your package (`pip install my-munkymind-hello`), Munkymind discovers it automatically at startup.

Test discovery:
```python
import importlib.metadata
connectors = {
    ep.name: ep.load()
    for ep in importlib.metadata.entry_points(group="munkymind.connectors")
}
print(connectors)  # Should include your connector
```

---

## Testing Your Connector

```python
import tempfile
from pathlib import Path
from mm.core.store import UserStore

# Create a test store
with tempfile.TemporaryDirectory() as tmp:
    store = UserStore(Path(tmp), "testuser")
    store.init()
    config = store.get_config()

    # Instantiate your connector
    connector = MyConnector(
        config={"my_setting": "my_value"},
        user_config=config,
    )

    # Validate
    ok, msg = connector.validate()
    assert ok, msg

    # Ingest
    pages = connector.ingest()
    assert len(pages) > 0
    for page in pages:
        assert page.id
        assert page.summary
        assert page.domain in [d.id for d in config.domains]
```

Run the full test suite to make sure your connector doesn't break anything:
```bash
python -m pytest tests/ -v
```

---

## Idempotency

**Connectors must be idempotent.** Running `ingest()` twice must produce the same result — not duplicate pages.

The easiest way: use a stable, content-based `id`. The ingestion runner uses `id` as the primary key in SQLite — re-running updates the row rather than inserting a new one.

```python
# Good: stable id based on natural key
page_id = f"obsidian::{vault_name}::{note_path.stem}"

# Bad: uuid generated fresh each run — creates duplicates
page_id = str(uuid.uuid4())
```

---

## Domain Mapping

Map content to the right domain. Built-in domains:

| ID | Label | What belongs here |
|----|-------|------------------|
| `health` | Health | Medical, fitness, wellbeing, mental health |
| `professional` | Professional | Work, career, skills, projects |
| `personal` | Personal | Relationships, hobbies, personal goals |
| `strategic` | Strategic | Long-term plans, decisions, values |
| `temporal` | Temporal | Calendar, upcoming events, deadlines |
| `projects` | Projects | Active project notes and tasks |

Users can add custom domains. Your connector should respect `user_config.domains` and map to whatever domains exist.

For automatic mapping, use keyword heuristics on file paths / titles:
```python
DOMAIN_KEYWORDS = {
    "health": ["health", "medical", "fitness", "gym", "diet", "mental"],
    "professional": ["work", "career", "cv", "resume", "job", "project"],
    # ...
}

def _auto_domain(text: str) -> str:
    text_lower = text.lower()
    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return domain
    return "personal"  # default
```

---

## Publishing

Share your connector with the community:

1. Create a public GitHub repo named `munkymind-{source}` (e.g. `munkymind-obsidian`)
2. Publish to PyPI: `pip install munkymind-obsidian`
3. Open a PR to add it to the [community connectors list](https://github.com/munkymind/munkymind#connectors) in the README

The goal: `pip install munkymind-obsidian` → connector available immediately, no forking.
