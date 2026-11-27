"""Capture: AI tools save an idea, decision, preference or fact while you talk.

A capture never writes to the library. It becomes a proposal in the Review queue (one short
page), and reaches the library only when the user approves it.

Guardrails, all before anything is queued:
  - auth: capture needs a read-write key (read-only keys and browser sessions can't capture)
  - validation: kind is one of KINDS, text is 1..4,000 chars, source_client is a short name
  - rate limits per user and client: 10 a minute, 200 a day
  - text that looks like a secret or a prompt-injection attempt is held back in
    review/quarantine.jsonl (secrets redacted), never queued
  - decisions, preferences and facts get the capture check (check.py); a failed check never
    blocks a capture
  - MM_CAPTURE=off refuses every capture
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
import time
from collections import defaultdict, deque

from mm.core.store import UserStore
from mm.review import check as capture_check
from mm.review import queue, switched_on

KINDS = ("idea", "decision", "preference", "fact")
MAX_TEXT = 4_000
MAX_TITLE = 120
PER_MINUTE = 10
PER_DAY = 200
_CLIENT = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")

_INJECTION = [re.compile(p, re.IGNORECASE) for p in (
    (r"(ignore|disregard|forget|override)\s+(all\s+|any\s+|your\s+|the\s+|of\s+)*"
     r"(previous|prior|above|earlier|original|system|existing)\s+(instructions|prompts?|rules)"),
    r"(reveal|print|show|repeat|dump|leak)\s+(me\s+)?(your|the)\s+(system\s+prompt|hidden\s+instructions)",
    r"(?m)^\s*[\[<(]?\s*system(\s+prompt)?\s*[\]>)]?\s*:",
    r"<\|?\s*(im_start|system)\s*\|?>",
    r"you\s+are\s+now\s+(an?\s+)?(unrestricted|jailbroken|DAN|uncensored)",
    r"new\s+instructions?\s*:",
)]

_SECRETS = [re.compile(p) for p in (
    r"\bmm_(?:sk|ss|lt)_[1-9A-HJ-NP-Za-km-z]{16,}",
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAIza[0-9A-Za-z_-]{30,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"(?i)\b(?:password|passwd|api[_-]?key|secret|(?:access[_-]?|auth[_-]?)?token)\s*[:=]\s*\S{6,}",
)]

_lock = threading.Lock()
_recent: dict[tuple[str, str], deque] = defaultdict(deque)


class CaptureError(ValueError):
    """A refused capture; the message goes back to the calling tool."""


class RateLimited(CaptureError):
    """Too many captures; try again later."""


def enabled() -> bool:
    return switched_on("MM_CAPTURE")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def redact(text: str) -> str:
    for p in _SECRETS:
        text = p.sub("[REDACTED]", text)
    return text


def screen(text: str) -> str:
    """Why this text must not be queued, or '' when it's fine."""
    if any(p.search(text) for p in _SECRETS):
        return "looks like a secret or credential"
    if any(p.search(text) for p in _INJECTION):
        return "looks like instructions to an AI rather than something about you"
    return ""


def _validate(text, kind, source_client, title) -> tuple[str, str, str, str]:
    kind = (kind or "fact").strip().lower()
    source_client = (source_client or "other").strip().lower()
    text = (text or "").strip()
    title = " ".join((title or "").split())
    if kind not in KINDS:
        raise CaptureError(f"kind must be one of: {', '.join(KINDS)}")
    if not _CLIENT.match(source_client):
        raise CaptureError("source_client must be a short name like claude.ai, chatgpt or cursor "
                           "(letters, digits, dot, dash; up to 40)")
    if not text:
        raise CaptureError("text is empty")
    if len(text) > MAX_TEXT:
        raise CaptureError(f"text is {len(text)} characters; the limit is {MAX_TEXT}. Capture the essence.")
    if len(title) > MAX_TITLE:
        raise CaptureError(f"title is over {MAX_TITLE} characters")
    return text, kind, source_client, title


def _quarantine_path(store: UserStore):
    return queue.review_dir(store) / "quarantine.jsonl"


def quarantined(store: UserStore) -> list[dict]:
    try:
        lines = _quarantine_path(store).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _today_count(store: UserStore, source_client: str, today: str) -> int:
    queued = sum(1 for p in queue.items(store) if p.get("origin") == "capture"
                 and p.get("source_client") == source_client and (p.get("created_at") or "")[:10] == today)
    held = sum(1 for q in quarantined(store) if q.get("source_client") == source_client
               and (q.get("captured_at") or "")[:10] == today)
    return queued + held


def _rate_check(store: UserStore, source_client: str, today: str) -> None:
    key = (store.user_id, source_client)
    now = time.monotonic()
    with _lock:
        q = _recent[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= PER_MINUTE:
            raise RateLimited(f"rate limit: {PER_MINUTE} captures a minute from {source_client}; "
                               "try again in a minute")
        if _today_count(store, source_client, today) >= PER_DAY:
            raise RateLimited(f"daily limit: {PER_DAY} captures from {source_client} today; try again tomorrow")
        q.append(now)


def _title_from(text: str) -> str:
    first = re.split(r"(?<=[.!?])\s|\n", text.strip(), maxsplit=1)[0].strip()
    first = " ".join(first.split()).rstrip(".!?")
    return first if len(first) <= 60 else first[:57].rsplit(" ", 1)[0] + "…"


def _pick_domain(store: UserStore, requested: str, text: str, related: list[dict]) -> str:
    from mm.connectors.files import DOMAIN_KEYWORDS, _content_domain

    ids = [d.id for d in store.get_config().domains]
    if requested:
        requested = requested.strip().lower()
        if requested not in ids:
            raise CaptureError(f"unknown domain {requested!r}; the user's domains are: {', '.join(ids)}")
        return requested
    keywords = {d: kws for d, kws in DOMAIN_KEYWORDS.items() if d in ids}
    for candidate in (_content_domain(text, keywords),
                      next((r["domain"] for r in related if r.get("domain") in ids), None)):
        if candidate:
            return candidate
    return "personal" if "personal" in ids else (ids[0] if ids else "personal")


def _free_path(store: UserStore, domain: str, base: str) -> str:
    taken = queue.pending_paths(store)
    path, n = f"{domain}/{base}.md", 2
    while path in taken or queue.current_text(store, path):
        path, n = f"{domain}/{base}-{n}.md", n + 1
    return path


def page_text(*, domain: str, kind: str, title: str, text: str, source_client: str,
              captured_at: str, changes: list[dict]) -> str:
    lines = ["---", f"domain: {domain}", "type: capture", f"kind: {kind}",
             f"captured_at: {captured_at}", f"source_client: {source_client}", "---", "",
             f"# {title}", "", "## Summary", "", text, ""]
    if changes:
        lines += ["## Replaces", ""]
        lines += [f"- {c['point']}: previously \"{c['previous']}\" ({c.get('page_title') or 'another page'})"
                  for c in changes]
        lines.append("")
    return "\n".join(lines)


def add(store: UserStore, text: str, kind: str = "fact", source_client: str = "other",
        title: str = "", domain: str = "", check_fn=None) -> dict:
    """Validate, screen and queue one capture.

    Returns {"status": "queued"|"held", "message", ...}. Raises CaptureError when refused.
    """
    if not enabled():
        raise CaptureError("capture is switched off on this server (MM_CAPTURE=off); nothing was saved")
    text, kind, source_client, title = _validate(text, kind, source_client, title)
    now = _now()
    today = now.date().isoformat()
    _rate_check(store, source_client, today)

    reason = screen(text + "\n" + title)
    if reason:
        f = _quarantine_path(store)
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"captured_at": now.isoformat(), "kind": kind, "source_client": source_client,
                                 "text": redact(text), "reason": reason}, ensure_ascii=False) + "\n")
        return {"status": "held", "reason": reason,
                "message": f"Not saved: the text {reason}. It was held back for the user to look at."}

    # Outside any lock: this may call the user's model.
    result = (check_fn or capture_check.check)(store, kind, text)
    changes = (result or {}).get("changes") or []
    related = (result or {}).get("related") or []
    domain = _pick_domain(store, domain, text, related)
    title = title or _title_from(text)
    path = _free_path(store, domain, f"{today}-{queue.slug(title, 50)}")
    flags = {"changes": [f"{c['point']}: {c['previous']} → {c['new']}" for c in changes]}
    if result is not None and not result.get("ok"):
        flags["warnings"] = ["the capture check couldn't run, so changes of mind weren't looked for"]
    proposal = queue.propose(
        store, path=path, domain=domain, title=title, origin="capture", kind=kind,
        source_client=source_client, flags=flags, changes=changes,
        change_note=f"A {kind} captured from {source_client}",
        proposed=page_text(domain=domain, kind=kind, title=title, text=text, source_client=source_client,
                           captured_at=now.isoformat(), changes=changes))
    return {"status": "queued", "id": proposal["id"], "path": path, "domain": domain,
            **({"changes": changes} if changes else {}),
            "message": f"Saved to the Review queue as a {kind}. Nothing reaches the library until the "
                       "user approves it." + capture_check.message(result)}
