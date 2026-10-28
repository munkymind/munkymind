"""FilesConnector — ingest markdown, text, and PDF files from a local path."""
from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from mm.connectors.base import BaseConnector, ConnectorPage

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "health": ["health", "medical", "fitness", "workout", "nutrition", "sleep", "lab", "doctor"],
    "professional": ["work", "career", "cv", "resume", "job", "project", "client", "meeting"],
    "personal": ["personal", "family", "friend", "diary", "journal", "hobby"],
    "strategic": ["strategy", "goal", "vision", "plan", "okr", "objective"],
    "temporal": ["today", "week", "month", "schedule", "calendar", "upcoming", "deadline"],
    "projects": ["project", "build", "ship", "launch", "feature", "repo"],
}

# .trash holds notes deleted in Obsidian; .obsidian is the app's own settings
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", ".mypy_cache", ".obsidian", ".trash"}

DEFAULT_EXTENSIONS = [".md", ".txt", ".pdf"]

_APPROX_TOKENS_PER_CHAR = 0.25  # rough: 1 token ≈ 4 chars


def _approx_tokens(text: str) -> int:
    return int(len(text) * _APPROX_TOKENS_PER_CHAR)


def _word_re(word: str) -> str:
    """`word` as a whole word or a simple inflection (plan → plans, planning), never inside
    another word ("lab" must not match "label" or "collaboration")."""
    return r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?:s|es|ed|ing|ned|ning)?(?![a-z0-9])"


def _has_word(text: str, word: str) -> bool:
    return re.search(_word_re(word), text) is not None


def _content_domain(text: str, keywords: dict[str, list[str]]) -> str | None:
    """The domain whose keywords appear most often in the note, if it clearly leads.

    Needs at least two hits and a lead over the runner-up, so a passing mention
    doesn't decide where a note goes.
    """
    sample = text[:4000].lower()
    scores = {}
    for domain, kws in keywords.items():
        n = sum(len(re.findall(_word_re(kw), sample)) for kw in kws)
        if n:
            scores[domain] = n
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    best, n = ranked[0]
    if n >= 2 and (len(ranked) == 1 or n > ranked[1][1]):
        return best
    return None


def _tag_list(meta: dict[str, str]) -> list[str]:
    raw = meta.get("tags") or meta.get("tag") or ""
    return [t.strip(" #'\"").lower() for t in re.split(r"[,\s\[\]]+", raw) if t.strip(" #'\"")]


def _detect_domain(path: Path, domain_map: dict[str, list[str]] | None = None,
                   root: Path | None = None, domain_ids: set[str] | None = None,
                   meta: dict[str, str] | None = None, text: str = "") -> str:
    """Pick a domain for a file, most explicit signal first.

    1. `domain:` in the note's frontmatter.
    2. A folder named after a domain (`notes/health/sleep.md` → health).
    3. The user's domain_map: keywords or folder names matched against the path.
    4. Built-in keywords in the file or folder names.
    5. Frontmatter tags (`tags: [health]`).
    6. The note's own text, when one domain's keywords clearly lead.
    7. Otherwise "personal".
    Only the part of the path inside the notes folder counts, so a notes folder that
    happens to live under e.g. ~/projects/ doesn't push everything into "projects".
    """
    meta = meta or {}
    rel = path
    if root is not None:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = Path(path.name)
    ids = {d.lower() for d in (domain_ids or set(DOMAIN_KEYWORDS))}

    declared = meta.get("domain", "").strip(" '\"").lower()
    if declared in ids:
        return declared
    for folder in rel.parts[:-1]:
        if folder.lower() in ids:
            return folder.lower()
    needle = " ".join([path.stem, *rel.parts]).lower()

    if domain_map:
        for domain, keywords in domain_map.items():
            if any(_has_word(needle, kw) for kw in keywords):
                return domain

    for domain, keywords in DOMAIN_KEYWORDS.items():
        if any(_has_word(needle, kw) for kw in keywords):
            return domain

    tags = _tag_list(meta)
    for tag in tags:
        if tag in ids:
            return tag
    keywords = {**DOMAIN_KEYWORDS, **(domain_map or {})}
    for tag in tags:
        for domain, kws in keywords.items():
            if any(_has_word(tag, kw) for kw in kws):
                return domain

    return _content_domain(text, keywords) or "personal"


def _mtime(path: Path) -> str | None:
    """The file's modification time (UTC ISO): what freshness should reflect, not the ingest time."""
    try:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.UTC).replace(tzinfo=None).isoformat()
    except OSError:
        return None


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
            key = None
            for line in fm_block.splitlines():
                item = line.strip()
                if item.startswith("- ") and key:  # YAML list item under the last key
                    meta[key] = ", ".join(x for x in (meta[key], item[2:].strip()) if x)
                elif ":" in line:
                    k, _, v = line.partition(":")
                    key = k.strip()
                    meta[key] = v.strip()
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


def _ingest_text_file(path: Path, domain: str | Callable[[dict, str], str]) -> ConnectorPage:
    """`domain` is a domain id, or a function of (frontmatter, text) that picks one."""
    raw = path.read_text(encoding="utf-8", errors="replace")

    if path.suffix == ".md":
        meta, body = _parse_frontmatter(raw)
        h1 = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
        title = meta.get("title") or h1 or path.stem
        sections = _split_by_headings(body)
    else:
        meta = {}
        body = raw
        title = path.stem
        sections = {"Content": body}

    summary = _summary(body)
    if callable(domain):
        domain = domain(meta, title + "\n" + body)

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
        source_updated_at=_mtime(path),
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
                source_updated_at=_mtime(path),
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
            root = None if path.is_file() else path

            def detect(meta, text, f=f, root=root):
                return _detect_domain(f, domain_map, root=root, domain_ids=domain_ids, meta=meta, text=text)

            try:
                if f.suffix == ".pdf":
                    pages.extend(_ingest_pdf_file(f, detect({}, "")))
                else:
                    pages.append(_ingest_text_file(f, detect))
            except ImportError:
                raise  # a missing dependency is a setup problem, not one bad file
            except Exception as exc:  # one corrupt or unreadable file must not stop the folder
                import warnings
                self.skipped.append(f"{f}: {type(exc).__name__}: {exc}")
                warnings.warn(f"Skipping {f.name}: {type(exc).__name__}: {exc}")
            if progress_cb:
                progress_cb(i + 1, len(files))

        return pages
