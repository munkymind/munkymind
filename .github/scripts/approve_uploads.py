"""Approve every proposal in an /ingest/files response; print the chunks indexed.

Reads the upload response on stdin. Uses MM_API_KEY and MM_API_URL (default localhost:8000).
A response from a server with MM_REVIEW_UPLOADS=off (already indexed) is passed through.
"""
import json
import os
import sys
import urllib.request

body = json.load(sys.stdin)
if body.get("status") != "queued":
    print(body.get("chunks_total", 0))
    sys.exit(0)

base = os.environ.get("MM_API_URL", "http://localhost:8000").rstrip("/")
total = 0
for proposal in body.get("proposals", []):
    req = urllib.request.Request(
        f"{base}/review/{proposal['id']}", method="POST",
        data=json.dumps({"action": "approve"}).encode(),
        headers={"X-API-Key": os.environ["MM_API_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.load(resp)
    print(f"Approved {proposal['path']}: {result.get('chunks_total', 0)} chunks", file=sys.stderr)
    total += int(result.get("chunks_total", 0))
print(total)
