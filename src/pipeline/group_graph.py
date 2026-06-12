import json
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)


async def build_whatsapp_group_graph() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    async with collector.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.id AS chat_id, c.name AS chat_name,
                   m.sender_id, u.platform_user_id, u.name AS user_name
            FROM whatsapp_messages m
            JOIN whatsapp_chats c ON m.chat_id = c.id
            JOIN whatsapp_users u ON m.sender_id = u.id
            WHERE c.is_group = true AND m.sender_id IS NOT NULL
        """)

        lid_map = {}
        lid_rows = await conn.fetch(
            "SELECT lid, phone_jid FROM whatsapp_lid_map"
        )
        for lr in lid_rows:
            lid_map[lr["lid"]] = lr["phone_jid"]

    groups: dict[str, set[str]] = defaultdict(set)
    group_names: dict[str, str] = {}

    for r in rows:
        chat_id = str(r["chat_id"])
        puid = r["platform_user_id"]
        if "@lid" in (puid or ""):
            puid = lid_map.get(puid, puid)
        groups[chat_id].add(puid)
        group_names[chat_id] = r["chat_name"] or chat_id

    entity_lookup: dict[str, str] = {}
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, platform_id FROM entity_platform_links WHERE source = 'whatsapp'"
        )
        for l in links:
            entity_lookup[l["platform_id"]] = l["entity_id"]

    pair_weights: dict[tuple[str, str], list[str]] = {}
    for chat_id, members in groups.items():
        entity_members = sorted({entity_lookup[p] for p in members if p in entity_lookup})
        for i, a in enumerate(entity_members):
            for b in entity_members[i + 1:]:
                pair = (a, b)
                if pair not in pair_weights:
                    pair_weights[pair] = []
                pair_weights[pair].append(group_names[chat_id])

    stats = {"relationships": 0, "groups_processed": len(groups), "group_cooccurrence_signals": 0}

    # Phase 4F: persistent identity_signals for pairs sharing >= 2 groups
    new_signals: list[tuple] = []
    for (a, b), shared_groups in pair_weights.items():
        count = len(shared_groups)
        if count >= 2:
            confidence = round(min(0.15 + count * 0.10, 0.70), 3)
            new_signals.append((
                a,
                "group_cooccurrence",
                "whatsapp",
                None, None, None,
                "whatsapp",
                b,
                f"shared_groups:{count}",
                confidence,
            ))

    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = 'whatsapp_group_co_member'"
        )
        for (a, b), shared_groups in pair_weights.items():
            await conn.execute("""
                INSERT INTO entity_relationships
                    (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                VALUES ($1::uuid, $2::uuid, 'whatsapp_group_co_member', $3, false, $4::jsonb)
            """, a, b, len(shared_groups), json.dumps({"groups": shared_groups}))
            stats["relationships"] += 1

        await conn.execute("DELETE FROM identity_signals WHERE signal_type = 'group_cooccurrence'")
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)
            stats["group_cooccurrence_signals"] = len(new_signals)

    logger.info("WhatsApp group graph: %s", stats)
    return stats
