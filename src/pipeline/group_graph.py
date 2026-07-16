import json
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)


def _group_weight(member_count: int) -> float:
    """Small groups should dominate the score; giant rooms should contribute
    little. 2 members = 1.0, 10 members = 0.1, capped away from zero.

    GAP-3 (T5.4): `member_count` here is the group's BEST-KNOWN true size —
    `_effective_group_size()` prefers the collector's real participant/members
    count and only falls back to the count of members we actually collected.
    Weighting by the collected count alone made a 12k-person broadcast channel
    where we happened to sample 7 senders look like a tight 7-person group
    (weight 1/6) and hugely over-reward that co-membership; the true-size join
    correctly collapses it to ~1/12500."""
    if member_count <= 1:
        return 0.0
    return 1.0 / (member_count - 1)


def _effective_group_size(true_size: int | None, tracked_count: int) -> int:
    """Best-available group size for weighting. Prefer the collector's real
    participant/members count (`telegram_chats.members_count` /
    `whatsapp_chats.participant_count`) when it is present and non-zero AND at
    least as large as what we collected; otherwise fall back to the count of
    members we actually observed. Guarding on `>= tracked_count` protects
    against a stale/under-reported true count that would paradoxically inflate
    a group's weight below its known membership."""
    if true_size and true_size >= tracked_count:
        return true_size
    return tracked_count


async def build_whatsapp_group_graph() -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    async with collector.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.id AS chat_id, c.name AS chat_name,
                   c.participant_count AS true_size,
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
    group_true_size: dict[str, int | None] = {}

    for r in rows:
        chat_id = str(r["chat_id"])
        puid = r["platform_user_id"]
        if "@lid" in (puid or ""):
            puid = lid_map.get(puid, puid)
        groups[chat_id].add(puid)
        group_names[chat_id] = r["chat_name"] or chat_id
        group_true_size[chat_id] = r["true_size"]

    entity_lookup: dict[str, str] = {}
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, platform_id FROM entity_platform_links WHERE source = 'whatsapp'"
        )
        for l in links:
            entity_lookup[l["platform_id"]] = l["entity_id"]

    pair_weights: dict[tuple[str, str], list[dict]] = {}
    for chat_id, members in groups.items():
        entity_members = sorted({entity_lookup[p] for p in members if p in entity_lookup})
        # GAP-3: weight by the group's BEST-KNOWN true size, not the count of
        # members we happened to collect. `len(members)` under-counts large
        # broadcast rooms (we only sample active senders), which used to
        # over-reward co-membership there; prefer participant_count when present.
        group_size = _effective_group_size(group_true_size.get(chat_id), len(members))
        for i, a in enumerate(entity_members):
            for b in entity_members[i + 1:]:
                pair = (a, b)
                if pair not in pair_weights:
                    pair_weights[pair] = []
                pair_weights[pair].append({
                    "name": group_names[chat_id],
                    "size": group_size,
                    "weight": round(_group_weight(group_size), 6),
                })

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
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'whatsapp_group_co_member'"
            )
            for (a, b), shared_groups in pair_weights.items():
                weighted = sum(group["weight"] for group in shared_groups)
                weight = max(1, round(weighted * 100))
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'whatsapp_group_co_member', $3, false, $4::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, a, b, weight, json.dumps({
                    "groups": [group["name"] for group in shared_groups],
                    "group_sizes": {group["name"]: group["size"] for group in shared_groups},
                    "weighted_total": round(weighted, 4),
                    "shared_group_count": len(shared_groups),
                    "why": "Shared smaller groups contribute more weight than large groups (weighted by true group size).",
                }))
                stats["relationships"] += 1

            await conn.execute(
                "DELETE FROM identity_signals WHERE signal_type = 'group_cooccurrence' AND source_platform = 'whatsapp'"
            )
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


async def build_telegram_group_graph() -> dict:
    """
    Telegram analog of build_whatsapp_group_graph(). Uses telegram_chat_members
    (a real membership table, unlike WhatsApp's message-sender proxy) restricted
    to type='group' chats, to find entity pairs that co-occur in >= 2 groups.

    Shares the 'group_cooccurrence' signal_type with WhatsApp — both functions
    scope their DELETE by source_platform so they don't clobber each other.
    Currently expected to produce few/no signals: only 6 entities are linked
    via source='telegram', and only 3 groups have >= 2 tracked members.
    """
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    async with collector.acquire() as conn:
        rows = await conn.fetch("""
            SELECT m.chat_id, c.title AS chat_title,
                   c.members_count AS true_size, u.platform_user_id
            FROM telegram_chat_members m
            JOIN telegram_chats c ON m.chat_id = c.id
            JOIN telegram_users u ON m.user_id = u.id
            WHERE c.type = 'group'
        """)

    groups: dict[str, set[str]] = defaultdict(set)
    group_names: dict[str, str] = {}
    group_true_size: dict[str, int | None] = {}

    for r in rows:
        chat_id = str(r["chat_id"])
        groups[chat_id].add(r["platform_user_id"])
        group_names[chat_id] = r["chat_title"] or chat_id
        group_true_size[chat_id] = r["true_size"]

    entity_lookup: dict[str, str] = {}
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, platform_id FROM entity_platform_links WHERE source = 'telegram'"
        )
        for l in links:
            entity_lookup[l["platform_id"]] = l["entity_id"]

    pair_weights: dict[tuple[str, str], list[dict]] = {}
    for chat_id, members in groups.items():
        entity_members = sorted({entity_lookup[p] for p in members if p in entity_lookup})
        # GAP-3: prefer the group's real members_count over the collected count.
        # telegram_chat_members is a real membership table, but for large groups
        # we still only capture a fraction of members — members_count (present
        # for 731 groups, up to 22k) collapses giant-broadcast co-membership to
        # near-zero weight where the collected count would over-reward it.
        group_size = _effective_group_size(group_true_size.get(chat_id), len(members))
        for i, a in enumerate(entity_members):
            for b in entity_members[i + 1:]:
                pair = (a, b)
                if pair not in pair_weights:
                    pair_weights[pair] = []
                pair_weights[pair].append({
                    "name": group_names[chat_id],
                    "size": group_size,
                    "weight": round(_group_weight(group_size), 6),
                })

    stats = {"relationships": 0, "groups_processed": len(groups), "group_cooccurrence_signals": 0}

    new_signals: list[tuple] = []
    for (a, b), shared_groups in pair_weights.items():
        count = len(shared_groups)
        if count >= 2:
            confidence = round(min(0.15 + count * 0.10, 0.70), 3)
            new_signals.append((
                a,
                "group_cooccurrence",
                "telegram",
                None, None, None,
                "telegram",
                b,
                f"shared_groups:{count}",
                confidence,
            ))

    async with analyzer.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM entity_relationships WHERE relationship_type = 'telegram_group_co_member'"
            )
            for (a, b), shared_groups in pair_weights.items():
                weighted = sum(group["weight"] for group in shared_groups)
                weight = max(1, round(weighted * 100))
                await conn.execute("""
                    INSERT INTO entity_relationships
                        (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources)
                    VALUES ($1::uuid, $2::uuid, 'telegram_group_co_member', $3, false, $4::jsonb)
                    ON CONFLICT (entity_a_id, entity_b_id, relationship_type)
                    DO UPDATE SET
                        weight = EXCLUDED.weight,
                        cross_platform = EXCLUDED.cross_platform,
                        sources = EXCLUDED.sources,
                        last_seen_at = EXCLUDED.last_seen_at,
                        updated_at = NOW()
                """, a, b, weight, json.dumps({
                    "groups": [group["name"] for group in shared_groups],
                    "group_sizes": {group["name"]: group["size"] for group in shared_groups},
                    "weighted_total": round(weighted, 4),
                    "shared_group_count": len(shared_groups),
                    "why": "Shared smaller groups contribute more weight than large groups (weighted by true group size).",
                }))
                stats["relationships"] += 1

            await conn.execute(
                "DELETE FROM identity_signals WHERE signal_type = 'group_cooccurrence' AND source_platform = 'telegram'"
            )
            if new_signals:
                await conn.executemany("""
                    INSERT INTO identity_signals
                        (entity_id, signal_type, source_platform, source_table, source_column,
                         source_record_id, target_platform, target_record_id, value, confidence)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """, new_signals)
                stats["group_cooccurrence_signals"] = len(new_signals)

    logger.info("Telegram group graph: %s", stats)
    return stats
