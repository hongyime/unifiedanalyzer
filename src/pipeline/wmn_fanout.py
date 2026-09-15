"""Track-C: WhatsMyName account-existence fan-out.

Runs the WhatsMyName ``wmn-data.json`` ruleset (WebBreacher/WhatsMyName, CC0
open-data) against tracked entity usernames. Each entry defines a URL template
plus response signatures (``e_code`` + ``e_string`` for existence,
``m_code`` + ``m_string`` for absence). We GET the templated URL with async
httpx and stage discovered accounts to ``handle_discoveries`` for operator
review — same shape as ``handle_fanout.py`` (Sherlock) and
``email_recognition.py`` (Holehe).

Complements:

* Sherlock (~400 sites) via ``handle_fanout.py`` — subprocess CLI.
* Maigret (top-300 default, 3000+ overall) via ``recon_spiderfoot.py`` — subprocess CLI.
* Holehe (~120 sites, email) via ``email_recognition.py`` — subprocess CLI.

WhatsMyName's edge is the curated per-site signature — lower false-positive
rate than a naive HTTP status probe.  Coverage adds sites the other tools
miss (e.g. many niche/tech/gaming/blogging platforms).

Results are STAGED (not auto-promoted) to ``handle_discoveries`` so an operator
can accept/reject each before it becomes an ``entity_platform_links`` row.

Env vars (all optional):

    WMN_FANOUT_ENABLED          "1" = run this phase, "0" = skip (default).
    WMN_FANOUT_MAX_PER_RUN      Entities scanned per phase tick (default 1).
    WMN_FANOUT_CONCURRENCY      Parallel HTTP GETs per username (default 20).
    WMN_FANOUT_SITE_TIMEOUT_S   Per-site GET timeout (default 5.0).
    WMN_FANOUT_TOTAL_TIMEOUT_S  Total wall-clock budget per username (default 90.0).
    WMN_FANOUT_MIN_HANDLE_LEN   Skip usernames shorter than this (default 3).
    WMN_FANOUT_UA               User-Agent header (default identifies as unifiedanalyzer).

Default OFF because a naive scan bursts 700 outbound GETs per entity and can
trip network egress filters.  Opt-in only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_ENABLED = "WMN_FANOUT_ENABLED"
_DATA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "wmn-data.json"

# Sites already covered by native collectors — skip these to reduce noise in
# handle_discoveries.  Same list as handle_fanout.py.
_ALREADY_COLLECTED_SITES = {
    "instagram", "threads", "tiktok", "lemon8", "twitter", "x",
    "facebook", "telegram", "whatsapp", "beeper", "youtube", "strava",
    "github",
}


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "0") == "1"


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Ruleset loader
# ---------------------------------------------------------------------------

_sites_cache: list[dict] | None = None


def _load_sites() -> list[dict]:
    """Load and cache the WhatsMyName site list.

    Returns ``[]`` and logs a warning if the file is missing (phase will skip
    with ``wmn_data_missing`` rather than fail).  Entries with
    ``"valid": false`` are dropped — the upstream ruleset uses that flag to
    mark sites known-broken.
    """
    global _sites_cache
    if _sites_cache is not None:
        return _sites_cache
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(
            "WhatsMyName data missing at %s; run scripts/refresh_wmn_data.py",
            _DATA_PATH,
        )
        _sites_cache = []
        return _sites_cache
    sites = [
        s for s in raw.get("sites", [])
        if s.get("valid") is not False and s.get("uri_check")
    ]
    _sites_cache = sites
    logger.info("Loaded %d WhatsMyName sites from %s", len(sites), _DATA_PATH.name)
    return sites


# ---------------------------------------------------------------------------
# Per-site probe
# ---------------------------------------------------------------------------


async def _check_site(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    site: dict,
    username: str,
    site_timeout: float,
) -> dict | None:
    """Probe one site.  Returns hit dict or ``None`` on miss/error."""
    uri = site["uri_check"].replace("{account}", username)
    async with sem:
        try:
            resp = await client.get(uri, timeout=site_timeout, follow_redirects=True)
        except (httpx.HTTPError, asyncio.TimeoutError):
            return None
        e_code = site.get("e_code")
        e_string = site.get("e_string", "")
        if e_code is None or e_string is None:
            return None
        if resp.status_code != e_code:
            return None
        if e_string and e_string not in resp.text:
            return None
        # Match — treat as a discovered account.
        return {
            "site": site["name"],
            "url": site.get("uri_pretty", uri).replace("{account}", username),
            "cat": site.get("cat", ""),
        }


async def _run_wmn(username: str) -> list[dict]:
    """Fan out one username across all valid WhatsMyName sites."""
    sites = _load_sites()
    if not sites:
        return []
    concurrency = _cfg_int("WMN_FANOUT_CONCURRENCY", 20)
    site_timeout = _cfg_float("WMN_FANOUT_SITE_TIMEOUT_S", 5.0)
    total_timeout = _cfg_float("WMN_FANOUT_TOTAL_TIMEOUT_S", 90.0)
    sem = asyncio.Semaphore(max(1, concurrency))
    headers = {
        "User-Agent": os.getenv(
            "WMN_FANOUT_UA",
            "unifiedanalyzer-wmn-fanout/1.0 (+https://github.com/hongyime/unifiedanalyzer)",
        )
    }
    async with httpx.AsyncClient(headers=headers) as client:
        tasks = [
            _check_site(client, sem, s, username, site_timeout)
            for s in sites
        ]
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=total_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "WhatsMyName fanout total-timeout for %r after %.0fs",
                username, total_timeout,
            )
            return []
    hits: list[dict] = []
    for r in results:
        if isinstance(r, dict):
            hits.append(r)
    return hits


# ---------------------------------------------------------------------------
# Public entry point (registered as a phase in incremental_runner.py)
# ---------------------------------------------------------------------------


async def run_wmn_fanout() -> dict:
    if not _is_enabled():
        return {
            "skipped": "disabled",
            "hint": f"set {_ENABLED}=1 in .env to enable WhatsMyName fanout",
        }
    if not _DATA_PATH.exists():
        return {
            "skipped": "wmn_data_missing",
            "hint": (
                f"expected {_DATA_PATH}; run "
                "`python scripts/refresh_wmn_data.py` to populate it"
            ),
        }

    max_per_run = _cfg_int("WMN_FANOUT_MAX_PER_RUN", 1)
    min_handle_len = _cfg_int("WMN_FANOUT_MIN_HANDLE_LEN", 3)

    pool = get_analyzer_pool()
    stats = {"entities_scanned": 0, "handles_queried": 0, "discoveries_added": 0}

    async with pool.acquire() as conn:
        # Pick entities with a known username that haven't been WMN-scanned
        # in the last 30 days.  Newest-active first.
        rows = await conn.fetch(
            """
            WITH last_scanned AS (
                SELECT entity_id, MAX(created_at) AS last_at
                FROM handle_discoveries
                WHERE tool = 'whatsmyname'
                GROUP BY entity_id
            )
            SELECT DISTINCT ON (e.id)
                   e.id AS entity_id, e.canonical_name,
                   epl.platform_username AS username
            FROM entities e
            JOIN entity_platform_links epl ON epl.entity_id = e.id
            LEFT JOIN last_scanned ls ON ls.entity_id = e.id
            WHERE epl.platform_username IS NOT NULL
              AND LENGTH(epl.platform_username) BETWEEN $2 AND 40
              AND (ls.last_at IS NULL OR ls.last_at < NOW() - INTERVAL '30 days')
            ORDER BY e.id, e.last_seen_at DESC NULLS LAST
            LIMIT $1
            """,
            max_per_run, min_handle_len,
        )

        for row in rows:
            entity_id = row["entity_id"]
            username = row["username"]
            stats["entities_scanned"] += 1
            stats["handles_queried"] += 1

            hits = await _run_wmn(username)
            logger.info(
                "wmn: entity=%s user=%r found %d sites",
                entity_id, username, len(hits),
            )

            for hit in hits:
                site_key = hit["site"].lower().replace(" ", "").replace("-", "")
                if any(k in site_key for k in _ALREADY_COLLECTED_SITES):
                    continue
                await conn.execute(
                    """
                    INSERT INTO handle_discoveries
                        (entity_id, source_query, tool, platform, url, confidence, detail)
                    VALUES ($1::uuid, $2, 'whatsmyname', $3, $4, 0.75, $5::jsonb)
                    ON CONFLICT (entity_id, tool, platform, source_query) DO NOTHING
                    """,
                    entity_id, username, hit["site"], hit["url"],
                    json.dumps({"cat": hit.get("cat", "")}),
                )
                stats["discoveries_added"] += 1

    logger.info("wmn_fanout (whatsmyname): %s", stats)
    return stats


__all__ = ["run_wmn_fanout"]
