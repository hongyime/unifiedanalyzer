"""Track-C: XposedOrNot email breach check + IDENTITY_BREACHED alerts.

For each email attached to a tracked entity (via contact_extraction /
entity_platform_links / bio fields), query XposedOrNot's public API for
known breaches. Store results in email_breach_findings so we don't re-hit
the API on every cycle. Emit an IDENTITY_BREACHED alert when new breach
data appears for an entity's email.

XposedOrNot is a free public API - no key, gentle rate limits. See
https://xposedornot.com/api_doc for the schema. If any 4xx/5xx or network
error occurs, we skip that email and log; the phase never fails the run.

Emits alert type IDENTITY_BREACHED (new). detail JSONB carries the breach
list, email, and entity_id. Dashboard's LABELS.alertType maps this to
"Data breach exposure".
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_ENABLED = "EMAIL_BREACH_CHECK_ENABLED"
_MAX_PER_RUN = int(os.getenv("EMAIL_BREACH_MAX_PER_RUN", "50"))
_SLEEP_S = float(os.getenv("EMAIL_BREACH_SLEEP_SECONDS", "1.2"))  # polite rate limit
_API_TIMEOUT = float(os.getenv("EMAIL_BREACH_API_TIMEOUT", "15"))
_USER_AGENT = "unifiedanalyzer/1.0 (breach-check; personal-osint)"


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "1") == "1"


def _api_url(email: str) -> str:
    return f"https://api.xposedornot.com/v1/check-email/{urllib.parse.quote(email)}"


def _query_xposedornot(email: str) -> list[dict]:
    """Blocking HTTP GET. Returns list of {breach_name, ...} dicts. Empty on
    no-hit OR any error. Called via asyncio.to_thread from the async caller."""
    req = urllib.request.Request(_api_url(email), headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 404 = email not found in any breach (clean); anything else = error
        if e.code == 404:
            return []
        logger.debug("xposedornot HTTP %d for %s", e.code, email)
        return []
    except Exception:
        logger.debug("xposedornot query failed for %s", email, exc_info=True)
        return []

    # XposedOrNot returns {"breaches": [["name1", "name2", ...]]} or similar
    # depending on version. Normalize to a list of dicts.
    breaches = data.get("breaches") or []
    if isinstance(breaches, list) and breaches and isinstance(breaches[0], list):
        breaches = breaches[0]  # sometimes nested one level
    out: list[dict] = []
    for b in breaches:
        if isinstance(b, str):
            out.append({"breach_name": b})
        elif isinstance(b, dict):
            out.append({
                "breach_name": b.get("breach") or b.get("name") or b.get("title"),
                "breach_date": b.get("date"),
                "data_classes": b.get("dataClasses") or b.get("data_classes"),
            })
    return [b for b in out if b.get("breach_name")]


async def check_email_breaches() -> dict:
    if not _is_enabled():
        return {"skipped": "disabled",
                "hint": f"set {_ENABLED}=1 in .env to enable XposedOrNot lookups"}

    pool = get_analyzer_pool()
    stats = {"emails_checked": 0, "new_findings": 0, "new_alerts": 0}

    async with pool.acquire() as conn:
        # Priority: emails not yet checked, or checked > 7 days ago.
        # Emails are extracted from contact_extraction (identity_signals.value
        # 'email:<addr>' pattern) + entity_platform_links.metadata.email if present.
        rows = await conn.fetch("""
            WITH tracked_emails AS (
                SELECT DISTINCT
                    LOWER(SUBSTRING(value FROM 'email:(.+)')) AS email,
                    entity_id
                FROM identity_signals
                WHERE signal_type IN ('email_match', 'commit_email')
                  AND value LIKE 'email:%'
                UNION
                SELECT DISTINCT LOWER(value), entity_id
                FROM identity_signals
                WHERE signal_type = 'commit_email'
                  AND value NOT LIKE '%:%'
                  AND value LIKE '%@%'
            ), last_seen AS (
                SELECT email, MAX(created_at) AS last_checked_at
                FROM email_breach_findings
                GROUP BY 1
            )
            SELECT te.email, te.entity_id
            FROM tracked_emails te
            LEFT JOIN last_seen ls ON ls.email = te.email
            WHERE te.email IS NOT NULL
              AND te.email <> ''
              AND (ls.last_checked_at IS NULL
                   OR ls.last_checked_at < NOW() - INTERVAL '7 days')
            LIMIT $1
        """, _MAX_PER_RUN)

        for r in rows:
            email = r["email"]
            entity_id = r["entity_id"]
            stats["emails_checked"] += 1

            breaches = await asyncio.to_thread(_query_xposedornot, email)

            if not breaches:
                # Write a sentinel so we don't re-check for 7 days.
                await conn.execute("""
                    INSERT INTO email_breach_findings
                        (email, breach_name, breach_date, source, detail)
                    VALUES ($1, '__none__', NULL, 'xposedornot', '{}'::jsonb)
                    ON CONFLICT (email, breach_name, source) DO UPDATE
                    SET created_at = NOW()
                """, email)
            else:
                new_this_email = 0
                for b in breaches:
                    breach_name = b["breach_name"]
                    breach_date_str = b.get("breach_date")
                    breach_date = None
                    if breach_date_str:
                        try:
                            breach_date = date.fromisoformat(breach_date_str[:10])
                        except ValueError:
                            pass
                    result = await conn.execute("""
                        INSERT INTO email_breach_findings
                            (email, breach_name, breach_date, source, detail)
                        VALUES ($1, $2, $3, 'xposedornot', $4::jsonb)
                        ON CONFLICT (email, breach_name, source) DO NOTHING
                    """, email, breach_name, breach_date,
                         json.dumps(b, default=str))
                    if result.endswith(" 1"):
                        new_this_email += 1
                stats["new_findings"] += new_this_email

                # One alert per email per run summarising all new breaches.
                if new_this_email and entity_id:
                    await conn.execute("""
                        INSERT INTO alerts
                            (entity_id, alert_type, severity, title, detail)
                        VALUES ($1::uuid, 'IDENTITY_BREACHED', 'warning', $2, $3::jsonb)
                    """,
                        entity_id,
                        f"Email {email} appears in {len(breaches)} breach(es)",
                        json.dumps({
                            "email": email,
                            "breach_count": len(breaches),
                            "new_this_run": new_this_email,
                            "breach_names": [b["breach_name"] for b in breaches],
                            "source": "xposedornot",
                        }, default=str))
                    stats["new_alerts"] += 1

            # Polite rate limit between requests.
            await asyncio.sleep(_SLEEP_S)

    logger.info("email_breach_check: %s", stats)
    return stats
