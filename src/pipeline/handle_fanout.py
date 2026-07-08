"""Track-C: Sherlock handle fan-out.

For each entity with a known username, invoke Sherlock (a public tool that
checks a username against 400+ sites) and stage the found profile URLs in
handle_discoveries for operator review before they get promoted to
entity_platform_links.

Sherlock is slow (~2-5 min per username), so this phase is env-gated and
throttled: HANDLE_FANOUT_MAX_PER_RUN caps entities per cycle. Uses subprocess
rather than importing the Sherlock package so version churn doesn't break us.

Requires sherlock-project on PATH inside the scheduler container (installed
via requirements.txt). If not available, phase logs a warning and skips.

Results are STAGED, not auto-promoted: a human reviews handle_discoveries
before those rows become entity_platform_links (auto-promotion would inject
low-confidence handles into the entity graph and skew calibration).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_ENABLED = "HANDLE_FANOUT_ENABLED"
_MAX_PER_RUN = int(os.getenv("HANDLE_FANOUT_MAX_PER_RUN", "3"))
_SHERLOCK_TIMEOUT = int(os.getenv("HANDLE_FANOUT_TIMEOUT_S", "300"))
# Sites to prefer + which ones we already have via the collector, so we don't
# stage duplicates. Sherlock's --site flag would narrow the run; we filter
# post-hoc instead so operators can see everything.
_ALREADY_COLLECTED_SITES = {
    "instagram", "threads", "tiktok", "lemon8", "twitter", "x",
    "facebook", "telegram", "whatsapp", "beeper", "youtube", "strava",
    "github",
}


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "1") == "1"


def _sherlock_available() -> bool:
    return shutil.which("sherlock") is not None


def _run_sherlock(username: str) -> list[dict]:
    """Blocking subprocess call to sherlock. Returns list of dicts:
    [{"site": "GitHub", "url": "https://github.com/foo"}, ...].

    Sherlock CLI: `sherlock --print-found --nsfw --no-color <user>` prints
    lines like `[+] GitHub: https://github.com/foo`. We parse those. --json
    would be cleaner but different sherlock forks disagree on schema so
    line-parsing is more portable.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["sherlock", "--print-found", "--no-color", "--timeout", "30", username],
            capture_output=True, text=True, timeout=_SHERLOCK_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("sherlock subprocess failed for %r: %s", username, e)
        return []

    out = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("[+] "):
            continue
        rest = line[4:]
        if ":" not in rest:
            continue
        site, url = rest.split(":", 1)
        site = site.strip()
        url = url.strip()
        if not url.startswith("http"):
            continue
        out.append({"site": site, "url": url})
    return out


async def run_handle_fanout() -> dict:
    if not _is_enabled():
        return {"skipped": "disabled",
                "hint": f"set {_ENABLED}=1 in .env to enable Sherlock fanout"}
    if not _sherlock_available():
        return {"skipped": "sherlock_not_on_path",
                "hint": "pip install sherlock-project (already in requirements.txt)"}

    pool = get_analyzer_pool()
    stats = {"entities_scanned": 0, "handles_queried": 0, "discoveries_added": 0}

    async with pool.acquire() as conn:
        # Pick entities with a known username who haven't been Sherlock-scanned
        # in the last 30 days. Newest-last-seen first (most active first).
        rows = await conn.fetch("""
            WITH last_scanned AS (
                SELECT entity_id, MAX(created_at) AS last_at
                FROM handle_discoveries
                WHERE tool = 'sherlock'
                GROUP BY entity_id
            )
            SELECT DISTINCT ON (e.id)
                   e.id AS entity_id, e.canonical_name,
                   epl.platform_username AS username
            FROM entities e
            JOIN entity_platform_links epl ON epl.entity_id = e.id
            LEFT JOIN last_scanned ls ON ls.entity_id = e.id
            WHERE epl.platform_username IS NOT NULL
              AND LENGTH(epl.platform_username) BETWEEN 3 AND 40
              AND (ls.last_at IS NULL OR ls.last_at < NOW() - INTERVAL '30 days')
            ORDER BY e.id, e.last_seen_at DESC NULLS LAST
            LIMIT $1
        """, _MAX_PER_RUN)

        for r in rows:
            entity_id = r["entity_id"]
            username = r["username"]
            stats["entities_scanned"] += 1
            stats["handles_queried"] += 1

            hits = await asyncio.to_thread(_run_sherlock, username)
            logger.info("sherlock: entity=%s user=%r found %d sites",
                        entity_id, username, len(hits))

            # Filter out collector-covered sites so we surface only the NEW
            # platforms Sherlock adds coverage for.
            for h in hits:
                site_key = h["site"].lower().replace(" ", "").replace("-", "")
                if any(k in site_key for k in _ALREADY_COLLECTED_SITES):
                    continue
                await conn.execute("""
                    INSERT INTO handle_discoveries
                        (entity_id, source_query, tool, platform, url, confidence, detail)
                    VALUES ($1::uuid, $2, 'sherlock', $3, $4, 0.6, $5::jsonb)
                    ON CONFLICT (entity_id, tool, platform, source_query) DO NOTHING
                """, entity_id, username, h["site"], h["url"],
                     json.dumps({"raw_site": h["site"]}))
                stats["discoveries_added"] += 1

    logger.info("handle_fanout (sherlock): %s", stats)
    return stats
