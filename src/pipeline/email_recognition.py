"""Track-C: Holehe silent email-recognition fan-out.

For each email on a tracked entity, run Holehe against 100+ services.
Holehe checks whether "forgot password" / "email exists" endpoints reveal
that an account with the given email exists on each service. It's silent
(no email is sent to the user).

Results (services where the email is recognised) are staged in
handle_discoveries (tool='holehe') for operator review. Not auto-promoted
to entity_platform_links because Holehe's per-service accuracy varies.

Holehe is a Python library that runs its checks over asyncio internally.
We invoke it as a subprocess for isolation - a hang in one Holehe check
won't wedge our own event loop.

Env: HOLEHE_ENABLED=1 to activate. Rate-controlled via HOLEHE_MAX_PER_RUN.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_ENABLED = "HOLEHE_ENABLED"
_MAX_PER_RUN = int(os.getenv("HOLEHE_MAX_PER_RUN", "3"))
_HOLEHE_TIMEOUT = int(os.getenv("HOLEHE_TIMEOUT_S", "180"))


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "1") == "1"


def _holehe_available() -> bool:
    return shutil.which("holehe") is not None


def _run_holehe(email: str) -> list[dict]:
    """Blocking subprocess call to holehe. Returns list of dicts:
    [{"service": "adobe", "url": "https://adobe.com"}, ...].

    Holehe CLI output pattern:
        [+] adobe.com                    (used)
        [-] amazon.com                   (not used)
    We only keep the [+] lines.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["holehe", "--only-used", "--no-color", "--no-clear", email],
            capture_output=True, text=True, timeout=_HOLEHE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("holehe subprocess failed for %r: %s", email, e)
        return []

    out = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("[+]"):
            continue
        # Line: "[+] adobe.com"
        service = line[3:].strip()
        # Strip trailing whitespace + ANSI leftovers.
        service = service.split()[0] if service else ""
        if service:
            out.append({
                "service": service,
                # Best-effort URL from domain; holehe doesn't emit URLs directly.
                "url": f"https://{service}",
            })
    return out


async def run_email_recognition() -> dict:
    if not _is_enabled():
        return {"skipped": "disabled",
                "hint": f"set {_ENABLED}=1 in .env to enable Holehe"}
    if not _holehe_available():
        return {"skipped": "holehe_not_on_path",
                "hint": "pip install holehe (already in requirements.txt)"}

    pool = get_analyzer_pool()
    stats = {"entities_scanned": 0, "emails_queried": 0, "discoveries_added": 0}

    async with pool.acquire() as conn:
        # Emails-per-entity from either identity_signals (email_match /
        # commit_email) or plausibly entity metadata. Newest-first, capped.
        # Excludes emails Holehe-scanned in the last 30 days.
        rows = await conn.fetch("""
            WITH tracked AS (
                SELECT DISTINCT
                    entity_id,
                    LOWER(SUBSTRING(value FROM 'email:(.+)')) AS email
                FROM identity_signals
                WHERE signal_type IN ('email_match', 'commit_email')
                  AND value LIKE 'email:%'
                UNION
                SELECT DISTINCT entity_id, LOWER(value) AS email
                FROM identity_signals
                WHERE signal_type = 'commit_email'
                  AND value NOT LIKE '%:%'
                  AND value LIKE '%@%'
            ), last_scanned AS (
                SELECT source_query AS email, MAX(created_at) AS last_at
                FROM handle_discoveries
                WHERE tool = 'holehe'
                GROUP BY 1
            )
            SELECT t.entity_id, t.email
            FROM tracked t
            LEFT JOIN last_scanned ls ON ls.email = t.email
            WHERE t.email IS NOT NULL AND t.email <> ''
              AND (ls.last_at IS NULL OR ls.last_at < NOW() - INTERVAL '30 days')
            LIMIT $1
        """, _MAX_PER_RUN)

        for r in rows:
            entity_id = r["entity_id"]
            email = r["email"]
            stats["entities_scanned"] += 1
            stats["emails_queried"] += 1

            hits = await asyncio.to_thread(_run_holehe, email)
            logger.info("holehe: entity=%s email=%r found %d services",
                        entity_id, email, len(hits))

            for h in hits:
                service = h["service"]
                await conn.execute("""
                    INSERT INTO handle_discoveries
                        (entity_id, source_query, tool, platform, url, confidence, detail)
                    VALUES ($1::uuid, $2, 'holehe', $3, $4, 0.5, $5::jsonb)
                    ON CONFLICT (entity_id, tool, platform, source_query) DO NOTHING
                """, entity_id, email, service, h["url"],
                     json.dumps({"raw_service": service}))
                stats["discoveries_added"] += 1

    logger.info("email_recognition (holehe): %s", stats)
    return stats
