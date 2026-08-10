import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

TARGET_PLATFORMS = {
    "github",
    "instagram",
    "strava",
    "tiktok",
    "threads",
    "x",
    "telegram",
    "whatsapp",
    "youtube",
    "beeper",
}

DEFAULT_OWNER_HANDLES = {"hongyime"}
DEFAULT_OWNER_ACCOUNTS = {
    ("github", "hongyime"),
    ("github", "66017805"),
    ("instagram", "4495993191"),
    ("instagram", "1484289220"),
    ("strava", "72101656"),
    ("tiktok", "6592362267399716866"),
    ("telegram", "8367748717"),
    ("telegram", "154320684"),
    ("whatsapp", "00000000@s.whatsapp.net"),
}

BEEPER_ID_RE = re.compile(r"^@(?P<prefix>[a-z]+)(?:go)?_(?P<value>[^:]+):", re.I)


def _telegram_t1_max_group_size() -> int:
    try:
        return max(2, int(os.getenv("PROXIMITY_TELEGRAM_T1_MAX_GROUP_SIZE", "50")))
    except ValueError:
        return 50


def _split_env_set(name: str) -> set[str]:
    raw = os.getenv(name, "")
    return {p.strip().lower() for p in re.split(r"[,;\s]+", raw) if p.strip()}


def _owner_handles() -> set[str]:
    return DEFAULT_OWNER_HANDLES | _split_env_set("PROXIMITY_OWNER_HANDLES")


def _platform(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "twitter": "x",
        "twitter/x": "x",
        "ig": "instagram",
        "instagramgo": "instagram",
        "telegramgo": "telegram",
        "whatsappgo": "whatsapp",
        "beeper (matrix)": "beeper",
        "facebook/messenger": "facebook",
    }
    return aliases.get(text, text.replace(" ", "_"))


def _account(value: Any, platform: str | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if platform in {"github", "instagram", "tiktok", "threads", "x"}:
        text = text.lstrip("@").lower()
    return text


def _reason(reason_type: str, **detail: Any) -> dict[str, Any]:
    clean = {k: v for k, v in detail.items() if v not in (None, "", [], {})}
    return {"type": reason_type, **clean}


def _parse_owner_account_env() -> dict[str, set[str]]:
    accounts: dict[str, set[str]] = defaultdict(set)
    for platform, account in DEFAULT_OWNER_ACCOUNTS:
        accounts[_platform(platform)].add(_account(account, _platform(platform)))

    raw = os.getenv("PROXIMITY_OWNER_ACCOUNTS", "")
    for part in re.split(r"[,;\n]+", raw):
        if ":" not in part:
            continue
        platform, account = part.split(":", 1)
        p = _platform(platform)
        a = _account(account, p)
        if p and a:
            accounts[p].add(a)
    return accounts


def _parse_beeper_native_id(network: Any, participant_id: Any, full_name: Any = None) -> tuple[str, str]:
    network_name = _platform(network)
    participant = str(participant_id or "").strip()
    match = BEEPER_ID_RE.match(participant)
    if match:
        prefix = match.group("prefix").lower()
        value = match.group("value")
        platform = {
            "instagram": "instagram",
            "telegram": "telegram",
            "whatsapp": "whatsapp",
            "discord": "discord",
        }.get(prefix, network_name)
        if platform == "whatsapp":
            phone = re.sub(r"\D+", "", str(full_name or ""))
            if phone:
                return "whatsapp", f"{phone}@s.whatsapp.net"
        return platform, _account(value, platform)
    return network_name or "beeper", _account(participant, network_name)


@dataclass
class ProximityRow:
    tier: int
    reasons: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, tier: int, reason: dict[str, Any]) -> None:
        self.tier = min(self.tier, tier)
        key = json.dumps(reason, sort_keys=True, default=str)
        self.reasons.setdefault(key, reason)


class ProximityAccumulator:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], ProximityRow] = {}

    def add(
        self,
        platform: Any,
        account_id: Any,
        owner_account: Any,
        tier: int,
        reason: dict[str, Any],
    ) -> None:
        p = _platform(platform)
        account = _account(account_id, p)
        owner = _account(owner_account, p)
        if not p or not account or not owner:
            return
        if p == "beeper":
            owner = str(owner_account or "").strip()
        if account.lower() == owner.lower():
            return
        key = (p, account, owner)
        row = self.rows.get(key)
        if row is None:
            row = ProximityRow(tier=tier)
            self.rows[key] = row
        row.add(tier, reason)

    def entity_tier(self, entity_id: str, links_by_entity: dict[str, list[dict[str, str]]]) -> int | None:
        best: int | None = None
        for link in links_by_entity.get(entity_id, []):
            p = _platform(link["source"])
            ids = {_account(link.get("platform_id"), p), _account(link.get("platform_username"), p)}
            ids.discard("")
            for platform, account, _owner in self.rows:
                if platform == p and account in ids:
                    tier = self.rows[(platform, account, _owner)].tier
                    best = tier if best is None else min(best, tier)
        return best

    def records(self) -> list[tuple[str, str, str, int, str]]:
        out: list[tuple[str, str, str, int, str]] = []
        for (platform, account, owner), row in self.rows.items():
            reasons = sorted(row.reasons.values(), key=lambda r: (r.get("type", ""), json.dumps(r, sort_keys=True, default=str)))
            out.append((platform, account, owner, row.tier, json.dumps(reasons[:20], default=str)))
        return out


async def _analyzer_links() -> tuple[dict[tuple[str, str], str], dict[str, list[dict[str, str]]]]:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT entity_id::text, source, platform_id, platform_username, platform_name
            FROM entity_platform_links
            WHERE retracted_at IS NULL
        """)
    lookup: dict[tuple[str, str], str] = {}
    by_entity: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        source = _platform(row["source"])
        link = {
            "source": source,
            "platform_id": str(row["platform_id"] or ""),
            "platform_username": str(row["platform_username"] or ""),
            "platform_name": str(row["platform_name"] or ""),
        }
        by_entity[row["entity_id"]].append(link)
        for value in (row["platform_id"], row["platform_username"], row["platform_name"]):
            account = _account(value, source)
            if account:
                lookup[(source, account)] = row["entity_id"]
                lookup[(source, account.lower())] = row["entity_id"]
    return lookup, by_entity


async def _discover_owner_accounts(
    lookup: dict[tuple[str, str], str],
    links_by_entity: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, set[str]], set[str]]:
    owner_accounts = _parse_owner_account_env()
    handles = _owner_handles()
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()

    async with collector.acquire() as conn:
        follow_rows = await conn.fetch("""
            SELECT DISTINCT platform, owner_account
            FROM follow_edges
            WHERE NULLIF(owner_account, '') IS NOT NULL
        """)
        for row in follow_rows:
            p = _platform(row["platform"])
            owner_accounts[p].add(_account(row["owner_account"], p))

        heartbeat_rows = await conn.fetch("""
            SELECT DISTINCT platform, owner_account
            FROM dm_hook_heartbeat
            WHERE NULLIF(owner_account, '') IS NOT NULL
        """)
        for row in heartbeat_rows:
            p = _platform(row["platform"])
            owner_accounts[p].add(_account(row["owner_account"], p))

        beeper_rows = await conn.fetch("""
            SELECT DISTINCT network, participant_id, username, full_name
            FROM beeper_shadow_participants
            WHERE is_self IS TRUE AND NULLIF(participant_id, '') IS NOT NULL
        """)
        for row in beeper_rows:
            owner_accounts["beeper"].add(str(row["participant_id"]).strip())
            p, native = _parse_beeper_native_id(row["network"], row["participant_id"], row["full_name"])
            if p and native:
                owner_accounts[p].add(native)

        telegram_rows = await conn.fetch("""
            SELECT lower(name) AS name, phone
            FROM telegram_user_accounts
            WHERE status IS DISTINCT FROM 'disabled'
        """)
        for row in telegram_rows:
            if row["name"] not in handles:
                continue
            phone = re.sub(r"\D+", "", row["phone"] or "")
            if phone:
                owner_accounts["whatsapp"].add(f"{phone}@s.whatsapp.net")

    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT source, platform_id, platform_username, platform_name
            FROM entity_platform_links
            WHERE retracted_at IS NULL
              AND (
                    lower(COALESCE(platform_username, '')) = ANY($1::text[])
                 OR lower(COALESCE(platform_name, '')) = ANY($1::text[])
              )
        """, sorted(handles))
    for row in rows:
        p = _platform(row["source"])
        for value in (row["platform_id"], row["platform_username"]):
            account = _account(value, p)
            if account:
                owner_accounts[p].add(account)

    owner_entities: set[str] = set()
    for p, accounts in list(owner_accounts.items()):
        for account in list(accounts):
            entity = lookup.get((p, account)) or lookup.get((p, account.lower()))
            if entity:
                owner_entities.add(entity)
                for link in links_by_entity.get(entity, []):
                    lp = _platform(link["source"])
                    owner_accounts[lp].add(_account(link.get("platform_id"), lp))
                    owner_accounts[lp].add(_account(link.get("platform_username"), lp))

    for accounts in owner_accounts.values():
        accounts.discard("")
    return owner_accounts, owner_entities


def _default_owner(owner_accounts: dict[str, set[str]], platform: str) -> str:
    owners = sorted(a for a in owner_accounts.get(platform, set()) if a)
    return owners[0] if owners else ""


async def _add_follow_edges(acc: ProximityAccumulator) -> None:
    pool = get_collector_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT platform, owner_account,
                   COALESCE(NULLIF(target_uid, ''), NULLIF(target_username, '')) AS account_id,
                   NULLIF(target_username, '') AS username,
                   array_agg(DISTINCT direction) AS directions,
                   max(last_seen) AS last_seen
            FROM follow_edges
            WHERE NULLIF(owner_account, '') IS NOT NULL
              AND (NULLIF(target_uid, '') IS NOT NULL OR NULLIF(target_username, '') IS NOT NULL)
            GROUP BY platform, owner_account, COALESCE(NULLIF(target_uid, ''), NULLIF(target_username, '')), NULLIF(target_username, '')
        """)
    for row in rows:
        directions = set(row["directions"] or [])
        if {"follower", "following"}.issubset(directions):
            acc.add(row["platform"], row["account_id"], row["owner_account"], 1, _reason("mutual_follow", username=row["username"]))
        else:
            acc.add(
                row["platform"],
                row["account_id"],
                row["owner_account"],
                2,
                _reason("one_way_follow", direction=next(iter(directions), None), username=row["username"]),
            )


async def _add_dm_contacts(acc: ProximityAccumulator, owner_accounts: dict[str, set[str]]) -> None:
    pool = get_collector_pool()
    async with pool.acquire() as conn:
        ig_rows = await conn.fetch("""
            SELECT owner_account, sender_id, sender_username, count(*) AS messages, max(timestamp) AS last_seen
            FROM instagram_dm
            WHERE NULLIF(owner_account, '') IS NOT NULL
              AND (NULLIF(sender_id, '') IS NOT NULL OR NULLIF(sender_username, '') IS NOT NULL)
            GROUP BY owner_account, sender_id, sender_username
        """)
        ig_thread_rows = await conn.fetch("""
            SELECT owner_account, unnest(participants) AS participant, count(*) AS threads
            FROM instagram_dm_thread
            WHERE NULLIF(owner_account, '') IS NOT NULL
              AND participants IS NOT NULL
            GROUP BY owner_account, participant
        """)
        tt_rows = await conn.fetch("""
            SELECT owner_account, sender_uid, sender_secuid, count(*) AS messages, max(timestamp) AS last_seen
            FROM tiktok_dm
            WHERE (NULLIF(sender_uid, '') IS NOT NULL OR NULLIF(sender_secuid, '') IS NOT NULL)
            GROUP BY owner_account, sender_uid, sender_secuid
        """)
        tt_thread_rows = await conn.fetch("""
            SELECT owner_account, unnest(participants) AS participant, count(*) AS threads
            FROM tiktok_dm_thread
            WHERE participants IS NOT NULL
            GROUP BY owner_account, participant
        """)

    for row in ig_rows:
        acc.add("instagram", row["sender_id"] or row["sender_username"], row["owner_account"], 1, _reason("dm_contact", messages=row["messages"]))
    for row in ig_thread_rows:
        acc.add("instagram", row["participant"], row["owner_account"], 1, _reason("dm_thread_participant", threads=row["threads"]))

    tiktok_owner = _default_owner(owner_accounts, "tiktok")
    for row in tt_rows:
        owner = row["owner_account"] or tiktok_owner
        acc.add("tiktok", row["sender_uid"] or row["sender_secuid"], owner, 1, _reason("dm_contact", messages=row["messages"]))
    for row in tt_thread_rows:
        owner = row["owner_account"] or tiktok_owner
        acc.add("tiktok", row["participant"], owner, 1, _reason("dm_thread_participant", threads=row["threads"]))


async def _add_group_comembers(acc: ProximityAccumulator, owner_accounts: dict[str, set[str]]) -> None:
    pool = get_collector_pool()
    telegram_owner_ids = sorted(owner_accounts.get("telegram", set()))
    whatsapp_owner_ids = sorted(owner_accounts.get("whatsapp", set()))

    async with pool.acquire() as conn:
        if telegram_owner_ids:
            rows = await conn.fetch("""
                WITH chat_sizes AS (
                    SELECT chat_id, count(*)::int AS member_count
                    FROM telegram_chat_members
                    GROUP BY chat_id
                ), owner_members AS (
                    SELECT m.chat_id, u.platform_user_id AS owner_account
                    FROM telegram_chat_members m
                    JOIN telegram_users u ON u.id = m.user_id
                    WHERE u.platform_user_id = ANY($1::text[])
                )
                SELECT om.owner_account, u.platform_user_id AS account_id, u.username,
                       count(DISTINCT om.chat_id) AS shared_chats,
                       min(cs.member_count)::int AS min_group_size
                FROM owner_members om
                JOIN telegram_chat_members m ON m.chat_id = om.chat_id
                JOIN telegram_users u ON u.id = m.user_id
                JOIN chat_sizes cs ON cs.chat_id = om.chat_id
                WHERE NULLIF(u.platform_user_id, '') IS NOT NULL
                  AND u.platform_user_id <> om.owner_account
                  AND COALESCE(u.is_bot, FALSE) IS FALSE
                GROUP BY om.owner_account, u.platform_user_id, u.username
            """, telegram_owner_ids)
            max_t1_group_size = _telegram_t1_max_group_size()
            for row in rows:
                min_group_size = row["min_group_size"] or 999999
                tier = 1 if min_group_size <= max_t1_group_size else 2
                reason_type = "telegram_small_group_comember" if tier == 1 else "telegram_large_group_comember"
                acc.add(
                    "telegram",
                    row["account_id"],
                    row["owner_account"],
                    tier,
                    _reason(
                        reason_type,
                        shared_chats=row["shared_chats"],
                        username=row["username"],
                        min_group_size=min_group_size,
                        t1_max_group_size=max_t1_group_size,
                    ),
                )

        if whatsapp_owner_ids:
            rows = await conn.fetch("""
                WITH owner_chats AS (
                    SELECT DISTINCT wm.chat_id, wu.platform_user_id AS owner_account
                    FROM whatsapp_messages wm
                    JOIN whatsapp_chats wc ON wc.id = wm.chat_id
                    JOIN whatsapp_users wu ON wu.id = wm.sender_id
                    WHERE wc.is_group IS TRUE
                      AND wu.platform_user_id = ANY($1::text[])
                )
                SELECT oc.owner_account, wu.platform_user_id AS account_id,
                       COALESCE(NULLIF(wu.pushname, ''), NULLIF(wu.name, '')) AS display_name,
                       count(DISTINCT oc.chat_id) AS shared_chats
                FROM owner_chats oc
                JOIN whatsapp_messages wm ON wm.chat_id = oc.chat_id
                JOIN whatsapp_users wu ON wu.id = wm.sender_id
                WHERE NULLIF(wu.platform_user_id, '') IS NOT NULL
                  AND wu.platform_user_id <> oc.owner_account
                  AND wu.platform_user_id NOT LIKE '%@newsletter'
                GROUP BY oc.owner_account, wu.platform_user_id, COALESCE(NULLIF(wu.pushname, ''), NULLIF(wu.name, ''))
            """, whatsapp_owner_ids)
            for row in rows:
                acc.add("whatsapp", row["account_id"], row["owner_account"], 1, _reason("whatsapp_group_comember", shared_chats=row["shared_chats"], name=row["display_name"]))

        beeper_rows = await conn.fetch("""
            WITH self_participants AS (
                SELECT DISTINCT chat_id, participant_id AS owner_account
                FROM beeper_shadow_participants
                WHERE is_self IS TRUE AND NULLIF(participant_id, '') IS NOT NULL
            )
            SELECT sp.owner_account, p.network, p.participant_id, p.username, p.full_name,
                   count(DISTINCT sp.chat_id) AS shared_chats
            FROM self_participants sp
            JOIN beeper_shadow_participants p ON p.chat_id = sp.chat_id
            WHERE COALESCE(p.is_self, FALSE) IS FALSE
              AND COALESCE(p.is_network_bot, FALSE) IS FALSE
              AND NULLIF(p.participant_id, '') IS NOT NULL
            GROUP BY sp.owner_account, p.network, p.participant_id, p.username, p.full_name
        """)
    for row in beeper_rows:
        p, account = _parse_beeper_native_id(row["network"], row["participant_id"], row["full_name"])
        acc.add(p, account or row["participant_id"], row["owner_account"], 1, _reason("beeper_room_comember", network=row["network"], username=row["username"], name=row["full_name"], shared_chats=row["shared_chats"]))


async def _add_owner_interactions(
    acc: ProximityAccumulator,
    owner_entities: set[str],
    links_by_entity: dict[str, list[dict[str, str]]],
) -> None:
    if not owner_entities:
        return
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT actor_entity_id::text AS actor_id, target_entity_id::text AS target_id,
                   interaction_type, source, count(*) AS interactions, max(occurred_at) AS last_seen
            FROM entity_interactions
            WHERE actor_entity_id IS NOT NULL
              AND target_entity_id IS NOT NULL
              AND (actor_entity_id = ANY($1::uuid[]) OR target_entity_id = ANY($1::uuid[]))
              AND interaction_type IN ('commented', 'reacted', 'replied', 'tagged', 'mentioned')
            GROUP BY actor_entity_id, target_entity_id, interaction_type, source
        """, list(owner_entities))

    for row in rows:
        actor = row["actor_id"]
        target = row["target_id"]
        if actor in owner_entities and target in owner_entities:
            continue
        peer = target if actor in owner_entities else actor
        owner = actor if actor in owner_entities else target
        tier = 1 if row["interaction_type"] == "tagged" else 2
        reason_type = "tagged_with_owner" if row["interaction_type"] == "tagged" else f"owner_{row['interaction_type']}"
        owner_label = _owner_entity_label(owner, links_by_entity, row["source"])
        for link in _peer_links(peer, links_by_entity, row["source"]):
            acc.add(link["source"], link["platform_id"], owner_label, tier, _reason(reason_type, interactions=row["interactions"], source=row["source"]))


def _owner_entity_label(entity_id: str, links_by_entity: dict[str, list[dict[str, str]]], preferred_source: str) -> str:
    preferred = _platform(preferred_source)
    links = links_by_entity.get(entity_id, [])
    for link in links:
        if link["source"] == preferred and link.get("platform_id"):
            return _account(link["platform_id"], preferred)
    for link in links:
        if link.get("platform_id"):
            return _account(link["platform_id"], link["source"])
    return entity_id


def _peer_links(entity_id: str, links_by_entity: dict[str, list[dict[str, str]]], preferred_source: str | None = None) -> list[dict[str, str]]:
    links = links_by_entity.get(entity_id, [])
    preferred = _platform(preferred_source)
    if preferred:
        matching = [l for l in links if l["source"] == preferred and l.get("platform_id")]
        if matching:
            return matching
    return [l for l in links if l["source"] in TARGET_PLATFORMS and l.get("platform_id")]


async def _add_friend_of_friend(
    acc: ProximityAccumulator,
    owner_entities: set[str],
    lookup: dict[tuple[str, str], str],
    links_by_entity: dict[str, list[dict[str, str]]],
) -> None:
    t1_entities: set[str] = set()
    for (platform, account, _owner), row in acc.rows.items():
        if row.tier != 1:
            continue
        entity = lookup.get((platform, account)) or lookup.get((platform, account.lower()))
        if entity and entity not in owner_entities:
            t1_entities.add(entity)
    if not t1_entities:
        return

    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT entity_a_id::text AS a, entity_b_id::text AS b, relationship_type, weight
            FROM entity_relationships
            WHERE entity_a_id = ANY($1::uuid[]) OR entity_b_id = ANY($1::uuid[])
            LIMIT 50000
        """, list(t1_entities))

    t1_and_owner = t1_entities | owner_entities
    owner_by_platform = _best_owner_by_platform(acc)
    for row in rows:
        a, b = row["a"], row["b"]
        peer = b if a in t1_entities else a
        if peer in t1_and_owner:
            continue
        for link in _peer_links(peer, links_by_entity):
            owner = _owner_for_peer_link(owner_by_platform, link)
            if owner:
                acc.add(link["source"], link["platform_id"], owner, 3, _reason("friend_of_friend", relationship=row["relationship_type"], weight=row["weight"]))


def _best_owner_by_platform(acc: ProximityAccumulator) -> dict[str, str]:
    best: dict[str, tuple[int, str]] = {}
    for (platform, _account_id, owner), row in acc.rows.items():
        current = best.get(platform)
        if current is None or row.tier < current[0]:
            best[platform] = (row.tier, owner)
    return {platform: owner for platform, (_tier, owner) in best.items()}


def _owner_for_peer_link(owner_by_platform: dict[str, str], link: dict[str, str]) -> str:
    source = _platform(link["source"])
    return owner_by_platform.get(source, "")


async def _add_discovered_only(
    acc: ProximityAccumulator,
    owner_accounts: dict[str, set[str]],
    links_by_entity: dict[str, list[dict[str, str]]],
) -> None:
    for links in links_by_entity.values():
        for link in links:
            source = _platform(link["source"])
            if source not in TARGET_PLATFORMS:
                continue
            account = _account(link.get("platform_id"), source)
            if not account:
                continue
            owners = owner_accounts.get(source) or set()
            for owner in owners:
                acc.add(source, account, owner, 4, _reason("discovered_only"))


async def _persist(acc: ProximityAccumulator) -> dict[str, Any]:
    records = acc.records()
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM account_proximity")
            if records:
                await conn.executemany("""
                    INSERT INTO account_proximity (platform, account_id, owner_account, tier, reasons, updated_at)
                    VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
                    ON CONFLICT (platform, account_id, owner_account) DO UPDATE SET
                        tier = EXCLUDED.tier,
                        reasons = EXCLUDED.reasons,
                        updated_at = NOW()
                """, records)
        distribution = await conn.fetch("""
            SELECT platform, tier, count(*) AS count
            FROM account_proximity
            GROUP BY platform, tier
            ORDER BY platform, tier
        """)
    return {
        "rows": len(records),
        "distribution": [dict(r) for r in distribution],
    }


async def compute_account_proximity() -> dict[str, Any]:
    """Rebuild account_proximity from current collector/analyzer evidence.

    The table is analyzer-owned and intentionally recomputed as a whole. Source
    inputs are small social graph and interaction summaries; a full refresh keeps
    stale proximity demotions correct without tracking per-source deletes.
    """
    lookup, links_by_entity = await _analyzer_links()
    owner_accounts, owner_entities = await _discover_owner_accounts(lookup, links_by_entity)
    acc = ProximityAccumulator()

    await _add_follow_edges(acc)
    await _add_dm_contacts(acc, owner_accounts)
    await _add_group_comembers(acc, owner_accounts)
    await _add_owner_interactions(acc, owner_entities, links_by_entity)
    await _add_friend_of_friend(acc, owner_entities, lookup, links_by_entity)
    await _add_discovered_only(acc, owner_accounts, links_by_entity)

    stats = await _persist(acc)
    stats["owner_accounts"] = {k: sorted(v) for k, v in sorted(owner_accounts.items()) if v}
    stats["owner_entities"] = len(owner_entities)
    logger.info("account_proximity refreshed: %s rows, %s owner entities", stats["rows"], stats["owner_entities"])
    return stats
