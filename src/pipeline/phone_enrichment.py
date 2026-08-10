"""Track-C: enrich phone_match identity_signals with phonenumbers-lib metadata.

For each phone_match signal row lacking `metadata.country`, parse the phone
number via the `phonenumbers` library (Google's libphonenumber port, purely
offline) and attach country / carrier / line-type to identity_signals.metadata
as a JSONB payload. Downstream review UIs can show "SG mobile Singtel"
instead of a bare "+65...".

Runs as an incremental phase after contact_extraction. Idempotent: only
touches rows where metadata IS NULL or lacks the `country` key.

phonenumbers is offline — no network calls, no rate limits. If the lib isn't
installed, the phase logs a warning and skips (non-fatal).
"""
from __future__ import annotations

import json
import logging
import os

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    return os.getenv("PHONE_ENRICHMENT_ENABLED", "1") == "1"


def _parse_phone(value_field: str) -> str | None:
    """Extract the E.164 phone from a phone_match value like 'phone:00000000'
    or a bare '00000000'. Returns None if not parseable."""
    if not value_field:
        return None
    if value_field.startswith("phone:"):
        return value_field[6:].strip()
    if value_field.startswith("+"):
        return value_field.strip()
    return None


async def enrich_phone_signals() -> dict:
    if not _is_enabled():
        return {"skipped": "disabled"}

    try:
        import phonenumbers
        from phonenumbers import (
            carrier as pn_carrier,
            geocoder as pn_geocoder,
            phonenumberutil as pn_util,
        )
    except ImportError:
        logger.warning("phone_enrichment: `phonenumbers` lib not installed; skipping")
        return {"skipped": "phonenumbers_unavailable"}

    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, value
            FROM identity_signals
            WHERE signal_type = 'phone_match'
              AND (metadata IS NULL OR NOT (metadata ? 'country'))
        """)

        stats = {"scanned": len(rows), "enriched": 0, "unparseable": 0}
        updates: list[tuple] = []
        for r in rows:
            e164 = _parse_phone(r["value"] or "")
            if not e164:
                stats["unparseable"] += 1
                continue
            try:
                num = phonenumbers.parse(e164, None)
                if not phonenumbers.is_valid_number(num):
                    stats["unparseable"] += 1
                    continue
            except phonenumbers.NumberParseException:
                stats["unparseable"] += 1
                continue

            country_iso = phonenumbers.region_code_for_number(num) or None
            country_name = pn_geocoder.description_for_number(num, "en") or None
            line_type_int = phonenumbers.number_type(num)
            # Map enum int -> readable name.
            line_type = {
                pn_util.PhoneNumberType.MOBILE: "mobile",
                pn_util.PhoneNumberType.FIXED_LINE: "landline",
                pn_util.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
                pn_util.PhoneNumberType.TOLL_FREE: "toll_free",
                pn_util.PhoneNumberType.VOIP: "voip",
            }.get(line_type_int, "unknown")
            carrier_name = pn_carrier.name_for_number(num, "en") or None

            enrichment = {
                "e164": e164,
                "country_iso": country_iso,
                "country_name": country_name,
                "line_type": line_type,
                "carrier": carrier_name,
                "source": "phonenumbers",
            }
            updates.append((json.dumps(enrichment), r["id"]))

        if updates:
            await conn.executemany("""
                UPDATE identity_signals
                SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb
                WHERE id = $2::uuid
            """, updates)
            stats["enriched"] = len(updates)

    logger.info("phone_enrichment: %s", stats)
    return stats
