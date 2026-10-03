"""FilesConnector — ingest markdown, text, and PDF files from a local path."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from mm.connectors.base import BaseConnector, ConnectorPage

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "health": ["health", "medical", "fitness", "workout", "nutrition", "sleep", "lab", "doctor"],
    "professional": ["work", "career", "cv", "resume", "job", "project", "client", "meeting"],
    "personal": ["personal", "family", "friend", "diary", "journal", "hobby"],
    "strategic": ["strategy", "goal", "vision", "plan", "okr", "objective"],
    "temporal": ["today", "week", "month", "schedule", "calendar", "upcoming", "deadline"],
    "projects": ["project", "build", "ship", "launch", "feature", "repo"],
}

SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".mypy_cache"}

DEFAULT_EXTENSIONS = [".md", ".txt", ".pdf"]

_APPROX_TOKENS_PER_CHAR = 0.25  # rough: 1 token ≈ 4 chars


def _approx_tokens(text: str) -> int:
    return int(len(text) * _APPROX_TOKENS_PER_CHAR)


def _detect_domain(path: Path, domain_map: dict[str, list[str]] | None = None,
                   root: Path | None = None, domain_ids: set[str] | None = None) -> str:
    """Pick a domain for a file.

    1. A folder named after a domain wins (`notes/health/sleep.md` → health).
    2. Then the user's domain_map keywords, then the built-in keywords.
    Only the part of the path inside the notes folder counts, so a notes folder that
    happens to live under e.g. ~/projects/ doesn't push everything into "projects".
    """
    rel = path
    if root is not None:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = Path(path.name)
    ids = {d.lower() for d in (domain_ids or set(DOMAIN_KEYWORDS))}
    for folder in rel.parts[:-1]:
        if folder.lower() in ids:
            return folder.lower()
    needle = (path.stem + " " + str(rel)).lower()

    # Check user-supplied domain_map first
    if domain_map:
        for domain, keywords in domain_map.items():
            if any(kw.lower() in needle for kw in keywords):
                return domain

    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(kw in needle for kw in keywords):
            return domain

    return "personal"


def _page_id(path: Path) -> str:
    h = hashlib.sha1(str(path).encode()).hexdigest()[:8]
    return f"files-{path.stem[:30]}-{h}"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Strip YAML frontmatter and return (meta_dict, body)."""
    meta: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm_block = text[3:end].strip()
            for line in fm_block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    meta[k.strip()] = v.strip()
            text = text[end + 4:].lstrip()
    return meta, text


def _split_by_headings(body: str) -> dict[str, str]:
    """Split markdown body by ## headings into {heading: body} dict."""
    sections: dict[str, str] = {}
    current_heading = "Introduction"
    current_lines: list[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            if current_lines:
                sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections[current_heading] = "\n".join(current_lines).strip()

    return {k: v for k, v in sections.items() if v}


def _summary(text: str, max_tokens: int = 200) -> str:
    words = text.split()
    # roughly 1 token per word
    return " ".join(words[:max_tokens])


def _ingest_text_file(path: Path, domain: str) -> ConnectorPage:
    raw = path.read_text(encoding="utf-8", errors="replace")

    if path.suffix == ".md":
        meta, body = _parse_frontmatter(raw)
        title = meta.get("title", path.stem)
        sections = _split_by_headings(body)
    else:
        meta = {}
        body = raw
        title = path.stem
        sections = {"Content": body}

    summary = _summary(body)

    return ConnectorPage(
        id=_page_id(path),
        domain=domain,
        type="document",
        title=title,
        summary=summary,
        detail_sections=sections,
        source="files",
        source_ref=str(path),
        connector="files",
        tags=list(meta.keys()),
    )


def _ingest_pdf_file(path: Path, domain: str) -> list[ConnectorPage]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise ImportError("pdfplumber is required for PDF ingestion: pip install pdfplumber") from exc

    pages_out: list[ConnectorPage] = []
    with pdfplumber.open(path) as pdf:
        # Group PDF pages into chunks of ≤2000 tokens
        group_texts: list[str] = []
        group_start = 1

        def _flush(group_texts: list[str], group_start: int, group_end: int) -> ConnectorPage:
            combined = "\n\n".join(group_texts)
            sections = {"Content": combined}
            title = f"{path.stem} (pp. {group_start}–{group_end})" if group_start != group_end else f"{path.stem} p.{group_start}"
            return ConnectorPage(
                id=_page_id(path) + f"-p{group_start}",
                domain=domain,
                type="document",
                title=title,
                summary=_summary(combined),
                detail_sections=sections,
                source="files",
                source_ref=str(path),
                connector="files",
            )

        group_token_count = 0
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            tok = _approx_tokens(text)
            if group_texts and group_token_count + tok > 2000:
                pages_out.append(_flush(group_texts, group_start, i - 1))
                group_texts = [text]
                group_token_count = tok
                group_start = i
            else:
                group_texts.append(text)
                group_token_count += tok

        if group_texts:
            pages_out.append(_flush(group_texts, group_start, len(pdf.pages)))

    return pages_out


class FilesConnector(BaseConnector):
    connector_id = "files"
    display_name = "File / Directory"
    description = "Ingest markdown, text, and PDF files from a local directory."

    def validate(self) -> tuple[bool, str]:
        path = Path(self.config.get("path", "")).expanduser()
        if not path.exists():
            return False, f"Path does not exist: {path}"
        return True, "ok"

    def schema(self) -> dict[str, Any]:
        return {
            "path": {"type": "string", "description": "Directory or file path to ingest"},
            "extensions": {"type": "array", "default": [".md", ".txt", ".pdf"]},
            "domain_map": {"type": "object", "description": "keyword lists per domain for auto-mapping"},
        }

    def ingest(self, progress_cb=None) -> list[ConnectorPage]:
        path = Path(self.config.get("path", "")).expanduser()
        extensions = self.config.get("extensions", DEFAULT_EXTENSIONS)
        domain_map = self.config.get("domain_map", {})
        domain_ids = set(DOMAIN_KEYWORDS) | {
            d.id for d in getattr(self.user_config, "domains", None) or []}

        if path.is_file():
            files = [path]
        else:
            files = []
            _walk = path.rglob("*")
            while True:
                try:
                    f = next(_walk)
                except StopIteration:
                    break
                except PermissionError as exc:
                    import warnings
                    warnings.warn(f"Skipping unreadable path: {exc}")
                    continue
                # Skip hidden / noise dirs
                if any(part in SKIP_DIRS for part in f.parts):
                    continue
                if f.is_file() and f.suffix in extensions:
                    files.append(f)

        pages: list[ConnectorPage] = []
        self.skipped: list[str] = []
        for i, f in enumerate(sorted(files)):
            domain = _detect_domain(f, domain_map, root=None if path.is_file() else path,
                                    domain_ids=domain_ids)
            try:
                if f.suffix == ".pdf":
                    pages.extend(_ingest_pdf_file(f, domain))
                else:
                    pages.append(_ingest_text_file(f, domain))
            except ImportError:
                raise  # a missing dependency is a setup problem, not one bad file
            except Exception as exc:  # one corrupt or unreadable file must not stop the folder
                import warnings
                self.skipped.append(f"{f}: {type(exc).__name__}: {exc}")
                warnings.warn(f"Skipping {f.name}: {type(exc).__name__}: {exc}")
            if progress_cb:
                progress_cb(i + 1, len(files))

        return pages
