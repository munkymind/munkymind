#!/usr/bin/env python3
"""Fail if the public repo contains personal or internal content that must stay private.

Maintainers' personal data and internal working notes live elsewhere. This scan keeps
it from leaking here. The list of personal terms is stored only as salted SHA-256 hashes
(.github/leak-hashes.txt), so this check doesn't publish what it protects. Every word and
2–3 word phrase in tracked text files is hashed and compared.

    python scripts/leak_scan.py              # scan; exit 1 on a match
    python scripts/leak_scan.py --add "term" # print the hash line to append for a new term
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

SALT = "monkey-mind-oss/leak-scan/v1:"
HASHES = Path(__file__).resolve().parent.parent / ".github" / "leak-hashes.txt"
SKIP = {"uv.lock", ".github/leak-hashes.txt"}
_WORD = re.compile(r"[a-z0-9]+")


def digest(term: str) -> str:
    norm = " ".join(_WORD.findall(term.lower()))
    return hashlib.sha256((SALT + norm).encode()).hexdigest()


def phrases(line: str):
    words = _WORD.findall(line.lower())
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            yield " ".join(words[i:i + n])


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "--add":
        print(digest(" ".join(argv[1:])))
        return 0
    banned = {l.split()[0] for l in HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")}
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for name in files:
        if name in SKIP:
            continue
        try:
            text = Path(name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary
        for n, line in enumerate(text.splitlines(), 1):
            for p in phrases(line):
                h = digest(p)
                if h in banned:
                    hits.append(f"{name}:{n}: matches a personal term (hash {h[:12]}…)")
    for h in hits:
        print(h)
    print(f"leak scan: {len(files)} files, {len(banned)} protected terms, {len(hits)} hit(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
