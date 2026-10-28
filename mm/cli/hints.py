"""Short, actionable hints printed after an ingest."""
from __future__ import annotations

from collections import Counter

SKEW_SHARE = 0.6
SKEW_MIN_PAGES = 10


def domain_skew_hint(store, username: str) -> str | None:
    """A hint when most of the library fell through to 'personal', the catch-all domain."""
    try:
        from mm.core.library import list_pages
        counts = Counter(p["domain"] for p in list_pages(store))
    except Exception:  # noqa: BLE001  (a hint must never fail an ingest)
        return None
    total = sum(counts.values())
    if total < SKEW_MIN_PAGES or counts["personal"] / total < SKEW_SHARE:
        return None
    share = round(100 * counts["personal"] / total)
    return (
        f"  ℹ {share}% of your notes landed in 'personal', the catch-all. To sort them, map folders\n"
        f"    to domains, e.g.: munkymind domain map professional \"Work\" --user {username}\n"
        "    or add `domain: health` to a note's frontmatter. Then re-run ingest."
    )
