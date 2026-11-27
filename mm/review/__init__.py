"""The Review queue: proposed changes wait here until the user approves or rejects them.

- queue.py: proposals, the page-changed conflict, approve/reject, indexing approved pages
- capture.py: the MCP `capture` tool and POST /capture (validated, screened, rate-limited)
- check.py: the capture check ("this looks like a change of mind")
"""
from __future__ import annotations

import os

_OFF = ("0", "off", "false", "no")


def switched_on(name: str, default: bool = True) -> bool:
    """An on/off environment switch. Unset or empty means `default`."""
    value = (os.environ.get(name) or "").strip().lower()
    if not value or value.startswith("#"):  # compose turns `X=   # comment` into "# comment"
        return default
    return value not in _OFF
