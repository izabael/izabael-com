#!/usr/bin/env python3
"""
refresh_corpus — pull the latest cross-frontier corpus snapshots from
the upstream izaplayer repository into research/playground-corpus/.

Idempotent. Fails-safe: if a fetch errors (network down, file missing
upstream, parse error), the existing on-disk file is left alone. The
committed snapshots in research/playground-corpus/ are the floor.

Wire this into a daily cron once the upstream snapshots are landing
on izaplayer's main branch:

    30 1 * * *  cd /app && python3 scripts/refresh_corpus.py

Stdlib only. No auth. No state. No side effects beyond writing files.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UPSTREAM_BASE = (
    "https://raw.githubusercontent.com/izabael/izaplayer/main/agents/corpus/output"
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "research" / "playground-corpus"

USER_AGENT = "izabael-com-corpus-refresh/0.1 (+https://izabael.com)"
TIMEOUT_SECONDS = 30


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def _fetch(url: str) -> bytes | None:
    """Fetch a URL. Return bytes on success, None on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        _log(f"  ✗ {url} → HTTP {e.code}")
        return None
    except urllib.error.URLError as e:
        _log(f"  ✗ {url} → {e.reason}")
        return None
    except Exception as e:
        _log(f"  ✗ {url} → {type(e).__name__}: {e}")
        return None


def _save_if_valid_json(data: bytes, dest: Path) -> bool:
    """Validate as JSON, then write atomically. Skip on parse failure."""
    try:
        json.loads(data)
    except json.JSONDecodeError as e:
        _log(f"  ✗ {dest.name}: not valid JSON ({e})")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return True


def refresh_index_and_agents() -> dict | None:
    """Fetch the manifest files. Return parsed index dict if successful."""
    _log("fetching index.json + agents.json")
    new_index_bytes = None
    for name in ("index.json", "agents.json"):
        data = _fetch(f"{UPSTREAM_BASE}/{name}")
        if data is None:
            continue
        if _save_if_valid_json(data, CORPUS_DIR / name):
            _log(f"  ✓ {name} ({len(data)} bytes)")
            if name == "index.json":
                new_index_bytes = data
    if new_index_bytes is None:
        return None
    return json.loads(new_index_bytes)


def refresh_snapshots(index: dict) -> int:
    """Fetch any snapshots referenced by the index that we don't already have."""
    fetched = 0
    latest = index.get("latest_snapshot")
    if latest:
        # Daily snapshot
        daily_name = f"{latest}.json"
        daily_dest = CORPUS_DIR / "daily" / daily_name
        if not daily_dest.exists():
            _log(f"fetching daily/{daily_name}")
            data = _fetch(f"{UPSTREAM_BASE}/daily/{daily_name}")
            if data and _save_if_valid_json(data, daily_dest):
                _log(f"  ✓ daily/{daily_name} ({len(data)} bytes)")
                fetched += 1

        # Full snapshot
        full_name = f"full-snapshot-{latest}.json"
        full_dest = CORPUS_DIR / "full" / full_name
        if not full_dest.exists():
            _log(f"fetching full/{full_name}")
            data = _fetch(f"{UPSTREAM_BASE}/full/{full_name}")
            if data and _save_if_valid_json(data, full_dest):
                _log(f"  ✓ full/{full_name} ({len(data)} bytes)")
                fetched += 1
    return fetched


def refresh_methodology() -> bool:
    """Pull the methodology paper draft from izaplayer's launch directory."""
    url = (
        "https://raw.githubusercontent.com/izabael/izaplayer/main/launch/"
        "methodology-paper-draft.md"
    )
    _log("fetching methodology.md")
    data = _fetch(url)
    if data is None:
        return False
    dest = CORPUS_DIR / "methodology.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)
    _log(f"  ✓ methodology.md ({len(data)} bytes)")
    return True


def main() -> int:
    _log(f"refreshing corpus into {CORPUS_DIR}")
    index = refresh_index_and_agents()
    if index is None:
        _log("could not fetch index.json — leaving committed files untouched")
        # Exit 0: not an error, just nothing to do (cache miss is normal).
        return 0

    fetched = refresh_snapshots(index)
    refresh_methodology()
    _log(f"refresh complete: {fetched} new snapshot(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
