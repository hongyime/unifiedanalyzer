"""SYNC #35: cross-source identity signals the resolver's username clustering
can't see on its own.

Emits into public.identity_signals (a preserved signal_type set, so these
survive resolution runs) for two high-precision cases:

  1. telegram <-> whatsapp SHARED PHONE. A telegram_users.phone that equals a
     whatsapp JID's phone is the same human, but the two cluster separately
     (telegram keys on a numeric id, whatsapp on the phone JID) so the resolver
     never joins them. We emit a two-sided 'phone_match' between the telegram
     and whatsapp entities (STRONG signal — see entity_resolver STRONG_SIGNALS).

  2. instagram external_url. The link in an IG bio ties that account to an
     off-platform presence (linktree / personal site / other handle). Emitted as
     'shared_website' keyed on the URL's domain.

Deferred: wa_discovered_links (~3k URLs shared inside chats) is content/interest
signal, not identity — attributing it to a person needs the chat participant,
and most domains are news/media. Left out on purpose; revisit if a
domain<->entity corroboration use-case appears.

Idempotent: every row this module writes is tagged metadata.emitter=EMITTER_TAG
and the whole set is deleted+reinserted each run, so it never double-counts and
never touches another emitter's signals.
"""

import logging
import re

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

EMITTER_TAG = "cross_source_signals_v1"
PHONE_MATCH_CONFIDENCE = 45.0
SHARED_WEBSITE_CONFIDENCE = 15.0

_DOMAIN_RE = re.compile(r"^(?:https?://)?(?:www\.)?([^/\s?#]+)", re.IGNORECASE)


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    m = _DOMAIN_RE.match(url.strip())
    return m.group(1).lower() if m else None


async def emit_cross_source_signals() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    # (source, platform_id) -> entity_id for the two link scopes we bridge.
    async with analyzer.acquire() as conn:
        links = {
            (r["source"], r["platform_id"]): r["entity_id"]
            for r in await conn.fetch(
                "SELECT source, platform_id, entity_id::text AS entity_id "
                "FROM entity_platform_links "
                "WHERE source IN ('telegram', 'whatsapp', 'instagram')"
            )
        }

    # rows: (entity_id, signal_type, source_platform, source_table,
    #        source_column, source_record_id, target_platform, target_record_id,
    #        value, confidence)
    rows: list[tuple] = []
    stats = {"phone_match": 0, "shared_website": 0}

    # ---- 1. telegram <-> whatsapp shared phone --------------------------------
    async with collector.acquire() as conn:
        pairs = await conn.fetch(
            r"""
            SELECT t.platform_user_id AS tg_id,
                   regexp_replace(t.phone, '\D', '', 'g') AS phone,
                   w.platform_user_id AS wa_jid
            FROM telegram_users t
            JOIN whatsapp_users w
              ON regexp_replace(t.phone, '\D', '', 'g')
               = regexp_replace(w.platform_user_id, '@.*', '')
            WHERE t.phone IS NOT NULL AND t.phone <> ''
            """
        )
    for r in pairs:
        tg_e = links.get(("telegram", r["tg_id"]))
        wa_e = links.get(("whatsapp", r["wa_jid"]))
        if not tg_e or not wa_e or tg_e == wa_e:
            continue
        rows.append((tg_e, "phone_match", "telegram", "telegram_users", "phone",
                     r["tg_id"], "whatsapp", r["wa_jid"], r["phone"],
                     PHONE_MATCH_CONFIDENCE))
        rows.append((wa_e, "phone_match", "whatsapp", "whatsapp_users",
                     "platform_user_id", r["wa_jid"], "telegram", r["tg_id"],
                     r["phone"], PHONE_MATCH_CONFIDENCE))
        stats["phone_match"] += 1

    # ---- 2. instagram external_url -> shared_website --------------------------
    async with collector.acquire() as conn:
        igs = await conn.fetch(
            "SELECT platform_user_id, external_url FROM instagram_profiles "
            "WHERE external_url IS NOT NULL AND external_url <> ''"
        )
    for r in igs:
        ig_e = links.get(("instagram", r["platform_user_id"]))
        if not ig_e:
            continue
        dom = _domain(r["external_url"])
        rows.append((ig_e, "shared_website", "instagram", "instagram_profiles",
                     "external_url", r["platform_user_id"], None, None,
                     dom or r["external_url"], SHARED_WEBSITE_CONFIDENCE))
        stats["shared_website"] += 1

    # ---- persist (idempotent via EMITTER_TAG) --------------------------------
    async with analyzer.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM identity_signals WHERE metadata->>'emitter' = $1",
                EMITTER_TAG,
            )
            if rows:
                await conn.execute(
                    """
                    INSERT INTO identity_signals
                        (entity_id, signal_type, source_platform, source_table,
                         source_column, source_record_id, target_platform,
                         target_record_id, value, confidence, metadata)
                    SELECT entity_id, signal_type, source_platform, source_table,
                           source_column, source_record_id, target_platform,
                           target_record_id, value, confidence,
                           jsonb_build_object('emitter', $11::text)
                    FROM UNNEST(
                        $1::uuid[], $2::text[], $3::text[], $4::text[], $5::text[],
                        $6::text[], $7::text[], $8::text[], $9::text[], $10::float8[]
                    ) AS t(entity_id, signal_type, source_platform, source_table,
                           source_column, source_record_id, target_platform,
                           target_record_id, value, confidence)
                    """,
                    [x[0] for x in rows], [x[1] for x in rows], [x[2] for x in rows],
                    [x[3] for x in rows], [x[4] for x in rows], [x[5] for x in rows],
                    [x[6] for x in rows], [x[7] for x in rows], [x[8] for x in rows],
                    [x[9] for x in rows], EMITTER_TAG,
                )

    result = {**stats, "rows": len(rows)}
    logger.info("Cross-source signals: %s", result)
    return result
