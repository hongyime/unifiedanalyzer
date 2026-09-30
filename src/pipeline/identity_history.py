"""Identity-history signals: consume the <platform>_user_changes tables the
collector already writes into, and emit identity_scorer signals when a
target's CURRENT attribute on one platform matches a HISTORICAL attribute
of a user on another platform.

Source: generalized from Griffin (@hatless1der) "A Snapchat OSINT Tip:
Viewing Bitmoji Changes" (2022-10-24) — prior versions of identity data
are pivotable.

Existing collector tables (verified via grep - all have identical shape:
id / user_id / field / old_value / new_value / detected_at):
- telegram_user_changes  (user_id BIGINT)
- instagram_user_changes (user_id VARCHAR)
- github_user_changes    (user_id VARCHAR)
- youtube_user_changes   (user_id VARCHAR)
- whatsapp_user_changes  (user_id VARCHAR)
- tiktok_user_changes    (user_id VARCHAR)
- lemon8_user_changes    (user_id VARCHAR)
- beeper_user_changes    (user_id VARCHAR)

Current-value tables (verified via grep of information_schema):
- telegram_users.username / bio / phone
- instagram_profiles.username / bio
- github_users.username / bio
- youtube_channels.username / description
- whatsapp_users.about / status
- tiktok_profiles.username / bio
- lemon8_profiles.username / bio
- beeper_users.display_name / avatar_url

Ref: Z:\\...\\research\\spec-do-now-3-identity-history.md
"""
from __future__ import annotations

import json
import logging
import os
from typing import Iterable

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

_ENABLED = "IDENTITY_HISTORY_ENABLED"
_LOOKBACK_DAYS = int(os.getenv("IDENTITY_HISTORY_LOOKBACK_DAYS", "365"))
_MIN_USERNAME_LEN = int(os.getenv("IDENTITY_HISTORY_MIN_USERNAME_LEN", "6"))

# (platform_of_changes, changes_table, current_platform_bindings)
# current_platform_bindings: list of (target_platform, current_table, id_col, field_col, field_name)
# We match old_value from <changes> against current field_col in each binding.
_CHANGES_TABLES = [
    "telegram_user_changes",
    "instagram_user_changes",
    "github_user_changes",
    "youtube_user_changes",
    "whatsapp_user_changes",
    "tiktok_user_changes",
    "lemon8_user_changes",
    "beeper_user_changes",
]

_CHANGES_TABLE_PLATFORM = {
    "telegram_user_changes":  "telegram",
    "instagram_user_changes": "instagram",
    "github_user_changes":    "github",
    "youtube_user_changes":   "youtube",
    "whatsapp_user_changes":  "whatsapp",
    "tiktok_user_changes":    "tiktok",
    "lemon8_user_changes":    "lemon8",
    "beeper_user_changes":    "beeper",
}

# For each field name in a *_user_changes row, which (target_platform,
# current_users_table, user_id_col, target_field) rows we match against.
# Only these fields fan out; other fields (e.g. display_name, avatar_url)
# ride along but don't emit signals in v1.
_USERNAME_FIELDS = ("username",)
_PHONE_FIELDS = ("phone",)
_BIO_FIELDS = ("bio", "about", "description", "status")

# (target_platform, table, id_col, username_col)
_USERNAME_SOURCES = [
    ("telegram",  "telegram_users",       "platform_user_id", "username"),
    ("instagram", "instagram_profiles",   "platform_user_id", "username"),
    ("github",    "github_users",         "username",         "username"),
    ("tiktok",    "tiktok_profiles",      "platform_user_id", "username"),
    ("lemon8",    "lemon8_profiles",      "platform_user_id", "username"),
]

# (target_platform, table, id_col, phone_col)
_PHONE_SOURCES = [
    ("telegram", "telegram_users", "platform_user_id", "phone"),
    ("whatsapp", "whatsapp_users", "platform_user_id", "phone"),
]


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "1") == "1"


async def _fetch_recent_changes(collector) -> list[dict]:
    """Return all changes rows within the lookback window across the 8
    per-platform tables. UNION ALL on the collector side to save round-trips.
    """
    changes: list[dict] = []
    for tbl in _CHANGES_TABLES:
        try:
            rows = await collector.fetch(
                f"""
                SELECT user_id::text AS user_id, field, old_value, new_value, detected_at
                FROM {tbl}
                WHERE detected_at > now() - ($1::int || ' days')::interval
                  AND old_value IS NOT NULL AND length(old_value) > 0
                ORDER BY detected_at DESC
                LIMIT 5000
                """,
                _LOOKBACK_DAYS,
            )
        except Exception as exc:
            # Table missing / permission / whatever: skip; other tables continue.
            logger.debug("identity_history: skip %s (%s)", tbl, exc)
            continue
        for r in rows:
            changes.append({
                "source_table": tbl,
                "source_platform": _CHANGES_TABLE_PLATFORM[tbl],
                "user_id": r["user_id"],
                "field": r["field"],
                "old_value": r["old_value"],
                "new_value": r["new_value"],
                "detected_at": r["detected_at"],
            })
    return changes


async def _match_username(collector, old_value: str, exclude_platform: str) -> list[tuple]:
    """Return (target_platform, target_record_id) pairs where any OTHER
    platform's current username exactly matches old_value (normalized: lower)."""
    normalized = (old_value or "").strip().lower()
    if len(normalized) < _MIN_USERNAME_LEN:
        return []
    hits: list[tuple] = []
    for target_platform, table, id_col, username_col in _USERNAME_SOURCES:
        if target_platform == exclude_platform:
            continue
        try:
            rows = await collector.fetch(
                f"""
                SELECT {id_col}::text AS target_id
                FROM {table}
                WHERE lower({username_col}) = $1
                LIMIT 5
                """,
                normalized,
            )
        except Exception:
            continue
        for r in rows:
            hits.append((target_platform, r["target_id"]))
    return hits


async def _match_phone(collector, old_value: str, exclude_platform: str) -> list[tuple]:
    """Match historical phone against current phones on OTHER platforms.
    E.164-normalize both sides (strip everything except leading + and digits)."""
    def _norm(v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        cleaned = "+" if v.startswith("+") else ""
        cleaned += "".join(ch for ch in v if ch.isdigit())
        return cleaned if len(cleaned) >= 8 else ""

    normalized = _norm(old_value)
    if not normalized:
        return []
    hits: list[tuple] = []
    for target_platform, table, id_col, phone_col in _PHONE_SOURCES:
        if target_platform == exclude_platform:
            continue
        try:
            rows = await collector.fetch(
                f"""
                SELECT {id_col}::text AS target_id
                FROM {table}
                WHERE {phone_col} IS NOT NULL
                  AND regexp_replace({phone_col}, '[^0-9+]', '', 'g') = $1
                LIMIT 5
                """,
                normalized,
            )
        except Exception:
            continue
        for r in rows:
            hits.append((target_platform, r["target_id"]))
    return hits


def _username_confidence(normalized: str) -> float:
    """Short usernames score lower (higher collision rate)."""
    return min(1.0, len(normalized) / 12.0)


async def run_identity_history() -> dict:
    summary = {"skipped": None, "scanned": 0, "username_signals": 0,
               "phone_signals": 0, "bio_signals": 0}
    if not _is_enabled():
        summary["skipped"] = "disabled"
        return summary

    try:
        collector = get_collector_pool()
    except Exception:
        summary["skipped"] = "no_collector_pool"
        return summary
    analyzer = get_analyzer_pool()

    async with collector.acquire() as ccon:
        changes = await _fetch_recent_changes(ccon)
    summary["scanned"] = len(changes)

    signal_rows: list[tuple] = []
    async with collector.acquire() as ccon:
        for ch in changes:
            field = (ch["field"] or "").lower()
            src_platform = ch["source_platform"]
            src_pid = ch["user_id"]
            old_v = ch["old_value"]

            if field in _USERNAME_FIELDS:
                hits = await _match_username(ccon, old_v, exclude_platform=src_platform)
                for target_platform, target_id in hits:
                    conf = _username_confidence((old_v or "").strip().lower())
                    signal_rows.append((
                        None,  # entity_id — analyzer will resolve later
                        "historical_username_match",
                        src_platform,
                        ch["source_table"],
                        "old_value",
                        src_pid,
                        target_platform,
                        target_id,
                        json.dumps({"old_username": old_v, "new_username": ch["new_value"]}),
                        conf,
                    ))
                    summary["username_signals"] += 1

            elif field in _PHONE_FIELDS:
                hits = await _match_phone(ccon, old_v, exclude_platform=src_platform)
                for target_platform, target_id in hits:
                    signal_rows.append((
                        None,
                        "historical_phone_match",
                        src_platform,
                        ch["source_table"],
                        "old_value",
                        src_pid,
                        target_platform,
                        target_id,
                        json.dumps({"old_phone_e164": old_v}),
                        1.0,
                    ))
                    summary["phone_signals"] += 1

            elif field in _BIO_FIELDS:
                # v1: no bio-content matching here (that's Do Next #2 bio_clustering).
                # We do emit a "historical_bio_available" signal linking the OLD bio to
                # the SAME entity, so downstream stages can pick it up cheaply.
                # Do Next #2 will consume old_value/new_value pairs directly.
                pass

    if signal_rows:
        async with analyzer.acquire() as acon:
            # Replace by (signal_type, source_platform, source_record_id) tuple:
            # keeps this idempotent per cycle.
            await acon.execute(
                "DELETE FROM identity_signals WHERE signal_type IN "
                "('historical_username_match', 'historical_phone_match')"
            )
            await acon.executemany(
                """
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                signal_rows,
            )

    logger.info("identity_history: %s", summary)
    return summary


__all__ = ["run_identity_history"]
