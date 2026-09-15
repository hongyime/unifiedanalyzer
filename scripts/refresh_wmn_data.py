"""Refresh data/wmn-data.json from the WhatsMyName upstream repo.

Usage (from repo root):
    python scripts/refresh_wmn_data.py

Fetches https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json
and writes it atomically to data/wmn-data.json.  The file is vendored so the
runtime does not depend on GitHub availability.

Safe to re-run: writes to a temp file then os.replace.  Exits non-zero on
network / JSON validation failure so a CI job wrapper can catch stale data.

Licensed CC0 by WebBreacher/WhatsMyName maintainers — see
https://github.com/WebBreacher/WhatsMyName/blob/main/LICENSE for terms.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger("refresh_wmn_data")

_UPSTREAM_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
_DEST_PATH = Path(__file__).resolve().parent.parent / "data" / "wmn-data.json"


def _fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "unifiedanalyzer-wmn-refresh/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - constant HTTPS URL
        return resp.read()


def _validate(payload: bytes) -> dict:
    doc = json.loads(payload.decode("utf-8"))
    sites = doc.get("sites")
    if not isinstance(sites, list) or not sites:
        raise ValueError("wmn-data.json has no 'sites' array")
    # A site row must at minimum have name + uri_check.
    for i, s in enumerate(sites):
        if not isinstance(s, dict):
            raise ValueError(f"sites[{i}] is not a dict")
        if not s.get("name") or not s.get("uri_check"):
            raise ValueError(f"sites[{i}] missing name or uri_check")
    return doc


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("Fetching %s ...", _UPSTREAM_URL)
    try:
        payload = _fetch(_UPSTREAM_URL)
    except urllib.error.URLError as exc:
        logger.error("fetch failed: %s", exc)
        return 2

    try:
        doc = _validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("upstream payload rejected: %s", exc)
        return 3

    _DEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _DEST_PATH.with_suffix(".json.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, _DEST_PATH)

    logger.info(
        "Wrote %s (%d sites, %d bytes)",
        _DEST_PATH, len(doc["sites"]), _DEST_PATH.stat().st_size,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
