"""SYNC #31: Beeper cross-network identity bridge.

beeper_shadow_messages.sender_id encodes each sender's NATIVE platform id:
    @telegram_<digits>:beeper.local            -> telegram platform_user_id
    @telegram_channel-<id>:beeper.local        -> SKIPPED (channels, not people)
    @instagramgo_<digits>:beeper.local         -> instagram-side id (see note)
    @whatsapp_<digits>:beeper.local            -> the digits ARE the phone
    @whatsapp_lid-<digits>:beeper.local        -> WhatsApp LID; phone comes from
                                                  sender_name (usually "+65...")
                                                  or whatsapp_lid_map

This module turns those senders into entity_platform_links on the NATIVE
source (telegram/instagram/whatsapp), NOT source='beeper', so that:
  * a sender already known to the resolver (link exists for (source, native_id))
    is left alone — beeper messages attribute to the EXISTING entity;
  * an unknown sender WITH a usable display name or phone gets a NEW entity +
    link (link_method='beeper_bridge', confidence=0.7).

Verified live (2026-07-14): 1,509 beeper telegram senders -> 1,292 match
collector telegram_users.platform_user_id. NOTE: instagram beeper ids come from
the "instagramgo" bridge and live in a DIFFERENT id-space than
instagram_profiles.platform_user_id (0/53 matched at build time), so IG senders
mostly become new entities; if the id-spaces ever converge the dedup-by-link
check keeps this idempotent.

Deferred networks (no collector counterpart to bridge yet): Discord, Slack,
Signal, Beeper (Matrix), Facebook/Messenger, Google Chat, LinkedIn. They could
later get source='beeper' links but are intentionally out of scope here.

Safety notes:
  * entity_resolver's stale-link cleanup only deletes link_method='auto' rows,
    so 'beeper_bridge' links survive resolution runs.
  * entity_resolver's ON CONFLICT (source, platform_id) DO UPDATE may later
    re-home a bridged link onto a resolver-clustered entity; the then-orphaned
    bridge entity is removed by the resolver's orphan cleanup. That is the
    intended precedence (resolver > bridge).
  * Idempotent: re-runs skip every (source, platform_id) that already has a link.
"""

import logging
import re
import uuid as uuid_module

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

BRIDGE_CONFIDENCE = 0.7
BRIDGE_LINK_METHOD = "beeper_bridge"
# beeper_shadow_messages is ~473k rows; the aggregate below scans the three
# bridged networks (~117k rows) server-side. Give it well beyond the pool's
# default command_timeout.
SCAN_TIMEOUT_SECONDS = 600

_RE_TELEGRAM = re.compile(r"^@telegram_(\d+):")
# Accept both @instagram_<id> and the live "@instagramgo_<id>" bridge prefix.
_RE_INSTAGRAM = re.compile(r"^@instagram(?:go)?_(\d+):")
_RE_WHATSAPP_LID = re.compile(r"^@whatsapp_lid-(\d+):")
_RE_WHATSAPP_DIRECT = re.compile(r"^@whatsapp_(\d+):")
# A sender_name that is really a phone number: "+", digits, common separators.
_RE_PHONE_NAME = re.compile(r"^\+[\d\s\-().]{6,}$")

# Server-side aggregate: one row per distinct (network, sender_id) with the
# most recent non-trivial display name. Excludes the account owner
# (is_sender), telegram channels, and non-native sender ids.
_SENDER_AGG_SQL = """
    SELECT ch.network, m.sender_id,
           (array_agg(m.sender_name ORDER BY m.timestamp DESC NULLS LAST)
              FILTER (WHERE m.sender_name IS NOT NULL
                        AND m.sender_name <> ''
                        AND m.sender_name <> m.sender_id))[1] AS display,
           count(*) AS msg_count
    FROM beeper_shadow_messages m
    JOIN beeper_shadow_chats ch ON m.chat_id = ch.chat_id
    WHERE ch.network IN ('Telegram', 'Instagram', 'WhatsApp')
      AND NOT m.is_sender
      AND m.sender_id LIKE '@%'
      AND m.sender_id NOT LIKE '@telegram_channel-%'
    GROUP BY 1, 2
"""


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def parse_native_sender(network: str, sender_id: str, display: str | None):
    """Map one beeper (network, sender_id, display) to its native identity.

    Returns (native_source, native_id, username, name) or None when the row
    cannot be resolved locally. WhatsApp LID rows whose sender_name is not a
    phone return ('__lid__', lid_digits, None, display) so the caller can
    batch-resolve them via whatsapp_lid_map.
    """
    if network == "Telegram":
        m = _RE_TELEGRAM.match(sender_id)
        if not m:
            return None
        return ("telegram", m.group(1), display, display)
    if network == "Instagram":
        m = _RE_INSTAGRAM.match(sender_id)
        if not m:
            return None
        return ("instagram", m.group(1), display, display)
    if network == "WhatsApp":
        m = _RE_WHATSAPP_LID.match(sender_id)
        if m:
            lid = m.group(1)
            if display and _RE_PHONE_NAME.match(display):
                phone = _digits(display)
                return ("whatsapp", f"{phone}@s.whatsapp.net", f"+{phone}", None)
            # Non-phone display (a real name) — needs whatsapp_lid_map.
            return ("__lid__", lid, None, display)
        m = _RE_WHATSAPP_DIRECT.match(sender_id)
        if m:
            # Direct format: the digits ARE the phone number.
            phone = m.group(1)
            name = display if (display and not _RE_PHONE_NAME.match(display)) else None
            return ("whatsapp", f"{phone}@s.whatsapp.net", f"+{phone}", name)
        return None
    return None


async def bridge_beeper() -> dict:
    """Bridge beeper senders into native-source entity_platform_links.

    Returns per-source counters:
      {"telegram": {...}, "instagram": {...}, "whatsapp": {...},
       "totals": {"scanned", "matched_existing", "entities_created",
                   "links_created", "unresolved"}}
    """
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    per_source = {
        s: {"scanned": 0, "matched_existing": 0, "entities_created": 0,
            "links_created": 0, "unresolved": 0}
        for s in ("telegram", "instagram", "whatsapp")
    }
    _net_to_source = {"Telegram": "telegram", "Instagram": "instagram",
                      "WhatsApp": "whatsapp"}

    async with collector.acquire() as conn:
        rows = await conn.fetch(_SENDER_AGG_SQL, timeout=SCAN_TIMEOUT_SECONDS)

    # ---- Parse every distinct sender -------------------------------------
    # natives: (source, native_id) -> {"username", "name", "msgs"}
    natives: dict[tuple[str, str], dict] = {}
    # lid_pending: lid_digits -> display name (may be None)
    lid_pending: dict[str, str | None] = {}
    lid_msgs: dict[str, int] = {}

    for r in rows:
        src = _net_to_source[r["network"]]
        per_source[src]["scanned"] += 1
        parsed = parse_native_sender(r["network"], r["sender_id"], r["display"])
        if parsed is None:
            per_source[src]["unresolved"] += 1
            continue
        nsrc, nid, username, name = parsed
        if nsrc == "__lid__":
            lid_pending.setdefault(nid, r["display"])
            lid_msgs[nid] = lid_msgs.get(nid, 0) + r["msg_count"]
            continue
        _merge_native(natives, nsrc, nid, username, name, r["msg_count"])

    # ---- Resolve remaining WhatsApp LIDs via whatsapp_lid_map -------------
    if lid_pending:
        lid_keys = [f"{lid}@lid" for lid in lid_pending]
        async with collector.acquire() as conn:
            lid_rows = await conn.fetch(
                "SELECT lid, phone_jid, display_name FROM whatsapp_lid_map "
                "WHERE lid = ANY($1::text[])",
                lid_keys, timeout=SCAN_TIMEOUT_SECONDS)
        lid_map = {r["lid"].split("@", 1)[0]: r for r in lid_rows}
        for lid, display in lid_pending.items():
            hit = lid_map.get(lid)
            if hit and hit["phone_jid"]:
                phone = hit["phone_jid"].split("@", 1)[0]
                name = display or hit["display_name"]
                _merge_native(natives, "whatsapp", hit["phone_jid"],
                              f"+{phone}", name, lid_msgs.get(lid, 0))
            else:
                per_source["whatsapp"]["unresolved"] += 1

    # ---- Split into already-linked vs to-create ---------------------------
    async with analyzer.acquire() as conn:
        existing = {
            (r["source"], r["platform_id"])
            for r in await conn.fetch(
                "SELECT source, platform_id FROM entity_platform_links "
                "WHERE source = ANY($1::text[])",
                ["telegram", "instagram", "whatsapp"])
        }

    to_create: list[tuple[str, str, str, str | None, str | None]] = []
    for (nsrc, nid), info in natives.items():
        if (nsrc, nid) in existing:
            per_source[nsrc]["matched_existing"] += 1
            continue
        # DECISION: only create an entity when we have a real handle — a
        # display name or a phone. A bare telegram/instagram numeric id with
        # no name is unactionable noise ("telegram:12345" entities).
        if not info["username"] and not info["name"]:
            per_source[nsrc]["unresolved"] += 1
            continue
        canonical = info["name"] or info["username"]
        to_create.append((str(uuid_module.uuid4()), nsrc, nid,
                          info["username"], canonical))

    # ---- Persist: new entities + links (idempotent) ------------------------
    if to_create:
        async with analyzer.acquire() as conn:
            async with conn.transaction():
                await conn.executemany("""
                    INSERT INTO entities (id, tier, canonical_name,
                                          confidence_score, signal_count)
                    VALUES ($1::uuid, 'secondary', $2, $3, 0)
                """, [(eid, canonical, BRIDGE_CONFIDENCE)
                      for eid, _, _, _, canonical in to_create])

                inserted = await conn.fetch("""
                    INSERT INTO entity_platform_links
                        (entity_id, source, platform_id, platform_username,
                         platform_name, confidence, link_method, is_confirmed)
                    SELECT entity_id, source, platform_id, platform_username,
                           platform_name, $2, $3, FALSE
                    FROM UNNEST($1::uuid[],
                                $4::text[], $5::text[], $6::text[], $7::text[])
                        AS t(entity_id, source, platform_id,
                             platform_username, platform_name)
                    ON CONFLICT (source, platform_id) DO NOTHING
                    RETURNING entity_id::text, source
                """,
                    [c[0] for c in to_create], BRIDGE_CONFIDENCE,
                    BRIDGE_LINK_METHOD,
                    [c[1] for c in to_create], [c[2] for c in to_create],
                    [c[3] for c in to_create], [c[4] for c in to_create])

                linked_ids = {r["entity_id"] for r in inserted}
                for r in inserted:
                    per_source[r["source"]]["links_created"] += 1
                    per_source[r["source"]]["entities_created"] += 1

                # A concurrent writer may have raced a link in (conflict
                # skipped): drop the entity we pre-created for it so no
                # orphan lingers until the resolver's cleanup.
                orphans = [c[0] for c in to_create if c[0] not in linked_ids]
                if orphans:
                    await conn.execute(
                        "DELETE FROM entities WHERE id = ANY($1::uuid[]) "
                        "AND NOT EXISTS (SELECT 1 FROM entity_platform_links l "
                        "                WHERE l.entity_id = entities.id)",
                        orphans)

    totals = {k: sum(per_source[s][k] for s in per_source)
              for k in ("scanned", "matched_existing", "entities_created",
                        "links_created", "unresolved")}
    result = {**per_source, "totals": totals}
    logger.info("Beeper bridge complete: %s", result)
    return result


def _merge_native(natives: dict, nsrc: str, nid: str,
                  username: str | None, name: str | None, msgs: int) -> None:
    """Dedup by (source, native_id); a WhatsApp phone can appear via both the
    direct and the LID sender formats. Prefer whichever variant carries a
    display name / username."""
    cur = natives.get((nsrc, nid))
    if cur is None:
        natives[(nsrc, nid)] = {"username": username, "name": name, "msgs": msgs}
        return
    cur["msgs"] += msgs
    if not cur["username"] and username:
        cur["username"] = username
    if not cur["name"] and name:
        cur["name"] = name
