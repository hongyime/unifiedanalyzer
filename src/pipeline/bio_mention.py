"""
Phase 3B: Cross-platform @handle detector.

Scans all collected bios for @username mentions, normalizes them,
and creates bio_mention identity signals when a mentioned handle
matches a known entity on any platform.
"""
import re
import logging

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.notifications import telegram

logger = logging.getLogger(__name__)

# Capture @handle including dots (e.g. @john.doe)
MENTION_RE = re.compile(r"@([\w.]+)")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

_STRIP_CHARS = "._-"
_DEFAULT_USERNAME_RE = re.compile(r"^user\d*$", re.IGNORECASE)
_MIN_LENGTH = 3

BIO_SOURCES = [
    ("github",    "SELECT platform_user_id::text AS pid, bio FROM github_users WHERE bio IS NOT NULL AND bio != ''"),
    ("instagram", "SELECT platform_user_id AS pid, bio FROM instagram_profiles WHERE bio IS NOT NULL AND bio != ''"),
    ("telegram",  "SELECT platform_user_id AS pid, bio FROM telegram_users WHERE bio IS NOT NULL AND bio != ''"),
    ("tiktok",    "SELECT platform_user_id AS pid, bio FROM tiktok_profiles WHERE bio IS NOT NULL AND bio != ''"),
    ("lemon8",    "SELECT platform_user_id AS pid, bio FROM lemon8_profiles WHERE bio IS NOT NULL AND bio != ''"),
    ("youtube",   "SELECT platform_channel_id AS pid, description AS bio FROM youtube_channels WHERE description IS NOT NULL AND description != ''"),
    ("whatsapp",  "SELECT platform_user_id AS pid, COALESCE(about, status) AS bio FROM whatsapp_users WHERE (about IS NOT NULL AND about != '') OR (status IS NOT NULL AND status != '')"),
]

SOURCE_TABLE = {
    "github": "github_users",
    "instagram": "instagram_profiles",
    "telegram": "telegram_users",
    "tiktok": "tiktok_profiles",
    "lemon8": "lemon8_profiles",
    "youtube": "youtube_channels",
    "whatsapp": "whatsapp_users",
}


def _normalize_mention(raw: str) -> str | None:
    """Normalize a @handle for cross-platform matching.

    Strips leading @ (platform_username fields often include it),
    then applies same rules as entity_resolver: strip ._-, lowercase,
    drop trailing digits, reject spaces/short/generic values.
    """
    if not raw:
        return None
    if _DEFAULT_USERNAME_RE.match(raw.lstrip("@")):
        return None
    if " " in raw.strip():
        return None
    u = raw.lower().strip()
    if u.startswith("@"):
        u = u[1:]
    for ch in _STRIP_CHARS:
        u = u.replace(ch, "")
    u = re.sub(r"\d+$", "", u)
    if not u or len(u) < _MIN_LENGTH:
        return None
    return u


def _extract_mentions(bio_text: str) -> list[str]:
    """Return normalized unique handles mentioned in the bio text."""
    clean = URL_RE.sub("", bio_text)
    raw_mentions = MENTION_RE.findall(clean)
    seen: set[str] = set()
    result: list[str] = []
    for m in raw_mentions:
        norm = _normalize_mention(m)
        if norm and norm not in seen:
            seen.add(norm)
            result.append(norm)
    return result


async def detect_bio_mentions() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # --- Load all bios ---
    platform_bios: dict[str, dict[str, str]] = {}
    async with collector.acquire() as conn:
        for source, query in BIO_SOURCES:
            try:
                rows = await conn.fetch(query)
                for r in rows:
                    if r["bio"]:
                        platform_bios.setdefault(source, {})[str(r["pid"])] = r["bio"]
            except Exception:
                logger.debug("Skipping bio source %s for mention detection", source, exc_info=True)

    # --- Load entity lookup tables ---
    # (source, platform_id) → entity_id
    pid_to_entity: dict[tuple[str, str], str] = {}
    # normalized_username → list of (entity_id, source, platform_id)
    username_to_entities: dict[str, list[tuple[str, str, str]]] = {}

    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id, platform_username FROM entity_platform_links"
        )
        for lnk in links:
            eid = lnk["entity_id"]
            src = lnk["source"]
            pid = lnk["platform_id"]
            pid_to_entity[(src, pid)] = eid
            uname = lnk["platform_username"]
            if uname:
                norm = _normalize_mention(uname)
                if norm:
                    username_to_entities.setdefault(norm, []).append((eid, src, pid))

    # --- Compute new bio_mention signals ---
    # Key: (source_entity_id, target_entity_id, norm_mention) — deduplicate per run
    seen_pairs: set[tuple[str, str, str]] = set()
    signal_rows: list[tuple] = []

    for source, bios in platform_bios.items():
        for pid, bio_text in bios.items():
            source_entity_id = pid_to_entity.get((source, pid))
            if not source_entity_id:
                continue

            mentions = _extract_mentions(bio_text)
            for norm in mentions:
                targets = username_to_entities.get(norm, [])
                for target_entity_id, target_source, target_pid in targets:
                    if target_entity_id == source_entity_id:
                        continue
                    pair_key = (source_entity_id, target_entity_id, norm)
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    signal_rows.append((
                        source_entity_id,       # $1 entity_id
                        "bio_mention",          # $2 signal_type
                        source,                 # $3 source_platform
                        SOURCE_TABLE.get(source, source),  # $4 source_table
                        "bio",                  # $5 source_column
                        pid,                    # $6 source_record_id
                        target_source,          # $7 target_platform
                        target_pid,             # $8 target_record_id
                        norm,                   # $9 value
                        0.85,                   # $10 confidence
                    ))

    # --- Detect which pairs are new (for notification) ---
    async with analyzer.acquire() as conn:
        existing_rows = await conn.fetch(
            "SELECT entity_id::text, value, target_record_id FROM identity_signals WHERE signal_type = 'bio_mention'"
        )
    existing_set = {(r["entity_id"], r["value"], r["target_record_id"]) for r in existing_rows}
    new_count = sum(
        1 for row in signal_rows
        if (row[0], row[8], row[7]) not in existing_set
    )

    # --- Replace bio_mention signals ---
    async with analyzer.acquire() as conn:
        await conn.execute("DELETE FROM identity_signals WHERE signal_type = 'bio_mention'")
        if signal_rows:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, signal_rows)

    stats = {
        "total_signals": len(signal_rows),
        "new_signals": new_count,
        "bios_scanned": sum(len(v) for v in platform_bios.values()),
    }
    logger.info("Bio mention detection: %s", stats)

    if new_count > 0:
        await _notify_new_bio_mentions(new_count, signal_rows, existing_set, pid_to_entity, analyzer)

    return stats


async def _notify_new_bio_mentions(
    new_count: int,
    signal_rows: list[tuple],
    existing_set: set[tuple[str, str, str]],
    pid_to_entity: dict[tuple[str, str], str],
    analyzer,
) -> None:
    new_rows = [r for r in signal_rows if (r[0], r[8], r[7]) not in existing_set]

    # Fetch entity canonical names for display
    entity_ids = list({r[0] for r in new_rows} | {r[7] for r in new_rows if r[7]})
    # r[7] is target_record_id (a platform_id, not entity_id) — get entity names by entity_id
    source_eids = list({r[0] for r in new_rows})

    try:
        async with analyzer.acquire() as conn:
            name_rows = await conn.fetch(
                "SELECT id::text, canonical_name FROM entities WHERE id = ANY($1::uuid[])",
                source_eids,
            )
            target_eids = list({
                pid_to_entity.get((r[6], r[7]))
                for r in new_rows
                if pid_to_entity.get((r[6], r[7]))
            })
            if target_eids:
                name_rows2 = await conn.fetch(
                    "SELECT id::text, canonical_name FROM entities WHERE id = ANY($1::uuid[])",
                    target_eids,
                )
            else:
                name_rows2 = []

        name_map: dict[str, str] = {}
        for nr in list(name_rows) + list(name_rows2):
            name_map[nr["id"]] = nr["canonical_name"] or nr["id"][:8]

        url = telegram.get_dashboard_url()
        lines = [f"\U0001f517 <b>{new_count} new bio mention match(es)</b>"]
        for row in new_rows[:8]:
            src_name = name_map.get(row[0], row[0][:8])
            tgt_eid = pid_to_entity.get((row[6], row[7]))
            tgt_name = name_map.get(tgt_eid, row[8]) if tgt_eid else row[8]
            lines.append(f"• <b>{src_name}</b> → @{row[8]} ({row[2]}→{row[6]}) → <b>{tgt_name}</b>")
        if len(new_rows) > 8:
            lines.append(f"  ...and {len(new_rows) - 8} more")
        lines.append(f"\n{url}/entities")
        await telegram.send("\n".join(lines))
    except Exception:
        logger.exception("Bio mention Telegram notification failed (non-fatal)")
