import json
import logging
from datetime import datetime, timezone

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

INTERACTION_BATCH_SIZE = 5000
SOURCE_QUERY_TIMEOUT_SECONDS = 1800

SOURCE_QUERIES = [
    {
        "source": "telegram",
        "interaction_type": "reacted",
        "query": """
            SELECT r.id::text AS record_id,
                   r.added_at AS occurred_at,
                   actor.platform_user_id AS actor_ref,
                   actor.username AS actor_ref2,
                   target.platform_user_id AS target_ref,
                   target.username AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'emoji', r.emoji,
                       'target_message_id', m.platform_message_id,
                       'target_preview', LEFT(COALESCE(m.text, m.caption, ''), 200)
                   )) AS metadata
            FROM telegram_reactions r
            LEFT JOIN telegram_users actor ON actor.id = r.user_id
            LEFT JOIN telegram_messages m ON m.id = r.message_id
            LEFT JOIN telegram_users target ON target.id = m.sender_id
            WHERE r.added_at IS NOT NULL {where_clause}
            ORDER BY r.added_at DESC
        """,
        "time_col": "r.added_at",
    },
    {
        "source": "telegram",
        "interaction_type": "replied",
        "query": """
            SELECT m.platform_message_id AS record_id,
                   m.platform_created_at AS occurred_at,
                   actor.platform_user_id AS actor_ref,
                   actor.username AS actor_ref2,
                   target.platform_user_id AS target_ref,
                   target.username AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'reply_to_message_id', m.reply_to_message_id,
                       'target_preview', LEFT(COALESCE(parent.text, parent.caption, ''), 200)
                   )) AS metadata
            FROM telegram_messages m
            LEFT JOIN telegram_users actor ON actor.id = m.sender_id
            LEFT JOIN telegram_chats chat ON chat.id = m.chat_id
            LEFT JOIN telegram_messages parent
              ON parent.platform_message_id = chat.platform_chat_id || ':' || m.reply_to_message_id
            LEFT JOIN telegram_users target ON target.id = parent.sender_id
            WHERE m.reply_to_message_id IS NOT NULL
              AND m.platform_created_at IS NOT NULL {where_clause}
            ORDER BY m.platform_created_at DESC
        """,
        "time_col": "m.platform_created_at",
    },
    {
        "source": "telegram",
        "interaction_type": "forwarded",
        "query": """
            SELECT m.platform_message_id AS record_id,
                   m.platform_created_at AS occurred_at,
                   actor.platform_user_id AS actor_ref,
                   actor.username AS actor_ref2,
                   m.forward_from_chat_id AS target_ref,
                   NULL AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'forward_from_chat_id', m.forward_from_chat_id,
                       'forward_from_message_id', m.forward_from_message_id,
                       'message_preview', LEFT(COALESCE(m.text, m.caption, ''), 200)
                   )) AS metadata
            FROM telegram_messages m
            LEFT JOIN telegram_users actor ON actor.id = m.sender_id
            WHERE (m.forward_from_chat_id IS NOT NULL OR m.forward_from_message_id IS NOT NULL)
              AND m.platform_created_at IS NOT NULL {where_clause}
            ORDER BY m.platform_created_at DESC
        """,
        "time_col": "m.platform_created_at",
    },
    {
        "source": "instagram",
        "interaction_type": "commented",
        "query": """
            SELECT c.platform_comment_id AS record_id,
                   c.platform_created_at AS occurred_at,
                   c.author_platform_id AS actor_ref,
                   c.author_username AS actor_ref2,
                   COALESCE(pr.platform_user_id::text, NULLIF(split_part(p.platform_post_id, '_', 2), '')) AS target_ref,
                   pr.username AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'post_id', p.platform_post_id,
                       'text', LEFT(c.text, 200),
                       'is_reply', c.is_reply
                   )) AS metadata
            FROM instagram_comments c
            LEFT JOIN instagram_posts p ON p.id = c.post_id
            LEFT JOIN instagram_profiles pr ON pr.id = p.profile_id
            WHERE c.platform_created_at IS NOT NULL {where_clause}
            ORDER BY c.platform_created_at DESC
        """,
        "time_col": "c.platform_created_at",
    },
    {
        "source": "youtube",
        "interaction_type": "commented",
        "query": """
            SELECT c.platform_comment_id AS record_id,
                   c.platform_published_at AS occurred_at,
                   c.author_channel_id AS actor_ref,
                   c.author_name AS actor_ref2,
                   ch.platform_channel_id AS target_ref,
                   ch.custom_url AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'video_id', v.platform_video_id,
                       'text', LEFT(c.text_original, 200),
                       'is_reply', c.is_reply
                   )) AS metadata
            FROM youtube_comments c
            LEFT JOIN youtube_videos v ON v.id = c.video_id
            LEFT JOIN youtube_channels ch ON ch.id = v.channel_id
            WHERE c.platform_published_at IS NOT NULL {where_clause}
            ORDER BY c.platform_published_at DESC
        """,
        "time_col": "c.platform_published_at",
    },
    {
        "source": "strava",
        "interaction_type": "commented",
        "query": """
            SELECT c.id::text AS record_id,
                   c.platform_created_at AT TIME ZONE 'UTC' AS occurred_at,
                   c.platform_athlete_id::text AS actor_ref,
                   c.athlete_name AS actor_ref2,
                   owner.platform_athlete_id::text AS target_ref,
                   owner.username AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'activity_id', a.platform_activity_id,
                       'text', LEFT(c.comment_text, 200)
                   )) AS metadata
            FROM strava_activity_comments c
            LEFT JOIN strava_activities a ON a.platform_activity_id = c.platform_activity_id
            LEFT JOIN strava_athletes owner ON owner.id = a.athlete_id
            WHERE c.platform_created_at IS NOT NULL {where_clause}
            ORDER BY c.platform_created_at DESC
        """,
        "time_col": "c.platform_created_at",
    },
    {
        "source": "instagram",
        "interaction_type": "mentioned",
        "query": """
            SELECT CONCAT(p.platform_post_id, ':', mention.handle) AS record_id,
                   p.platform_created_at AS occurred_at,
                   COALESCE(pr.platform_user_id::text, NULLIF(split_part(p.platform_post_id, '_', 2), '')) AS actor_ref,
                   pr.username AS actor_ref2,
                   mention.handle AS target_ref,
                   mention.handle AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'post_id', p.platform_post_id,
                       'caption', LEFT(p.caption, 200),
                       'mention', mention.handle
                   )) AS metadata
            FROM instagram_posts p
            LEFT JOIN instagram_profiles pr ON pr.id = p.profile_id
            CROSS JOIN LATERAL (
                SELECT lower(trim(both '@' FROM unnest(COALESCE(p.mentions, ARRAY[]::text[])))) AS handle
            ) AS mention
            WHERE p.platform_created_at IS NOT NULL
              AND mention.handle <> '' {where_clause}
            ORDER BY p.platform_created_at DESC
        """,
        "time_col": "p.platform_created_at",
    },
    {
        "source": "instagram",
        "interaction_type": "followed",
        "query": """
            SELECT CONCAT_WS(':', f.platform, f.owner_account, f.target_uid, f.direction) AS record_id,
                   COALESCE(f.first_seen, f.last_seen) AS occurred_at,
                   f.owner_account AS actor_ref,
                   NULL AS actor_ref2,
                   COALESCE(NULLIF(f.target_uid, ''), f.target_username) AS target_ref,
                   f.target_username AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'target_uid', f.target_uid,
                       'target_username', f.target_username,
                       'direction', f.direction
                   )) AS metadata
            FROM follow_edges f
            WHERE f.platform = 'instagram'
              AND f.direction = 'following'
              AND COALESCE(f.first_seen, f.last_seen) IS NOT NULL {where_clause}
            ORDER BY COALESCE(f.first_seen, f.last_seen) DESC
        """,
        "time_col": "COALESCE(f.first_seen, f.last_seen)",
    },
    {
        "source": "instagram",
        "interaction_type": "tagged",
        "query": """
            SELECT m.id::text AS record_id,
                   COALESCE(m.created_at, m.collected_at) AS occurred_at,
                   split_part(m.content_id, '_', 3) AS actor_ref,
                   NULL AS actor_ref2,
                   m.entity_name AS target_ref,
                   m.entity_name AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'content_id', m.content_id,
                       'source_url', m.source_url,
                       'media_id', split_part(m.content_id, '_', 2)
                   )) AS metadata
            FROM media_items m
            WHERE m.source = 'instagram'
              AND m.kind = 'tagged'
              AND COALESCE(m.created_at, m.collected_at) IS NOT NULL
              AND split_part(m.content_id, '_', 3) <> ''
              AND m.entity_name IS NOT NULL
              AND m.entity_name <> '' {where_clause}
            ORDER BY COALESCE(m.created_at, m.collected_at) DESC
        """,
        "time_col": "COALESCE(m.created_at, m.collected_at)",
    },
    {
        "source": "instagram",
        "interaction_type": "dm",
        "query": """
            SELECT message_id AS record_id,
                   timestamp AS occurred_at,
                   CASE WHEN is_from_me THEN owner_account ELSE sender_id END AS actor_ref,
                   CASE WHEN is_from_me THEN owner_account ELSE sender_username END AS actor_ref2,
                   CASE WHEN is_from_me THEN sender_id ELSE owner_account END AS target_ref,
                   CASE WHEN is_from_me THEN sender_username ELSE owner_account END AS target_ref2,
                   jsonb_strip_nulls(jsonb_build_object(
                       'thread_id', thread_id,
                       'text', LEFT(text, 200),
                       'is_from_me', is_from_me
                   )) AS metadata
            FROM instagram_dm
            WHERE timestamp IS NOT NULL {where_clause}
            ORDER BY timestamp DESC
        """,
        "time_col": "timestamp",
    },
]


async def _get_entity_lookup() -> dict[tuple[str, str], str]:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT entity_id::text, source, platform_id, platform_username
            FROM entity_platform_links
            WHERE retracted_at IS NULL
        """)
    lookup: dict[tuple[str, str], str] = {}
    for row in rows:
        platform_id = str(row["platform_id"]) if row["platform_id"] is not None else None
        username = row["platform_username"]
        if platform_id:
            lookup[(row["source"], platform_id)] = row["entity_id"]
            lookup[(row["source"], platform_id.lower())] = row["entity_id"]
        if username:
            lookup[(row["source"], username)] = row["entity_id"]
            lookup[(row["source"], username.lower())] = row["entity_id"]
    return lookup


def _resolve_entity(
    lookup: dict[tuple[str, str], str],
    source: str,
    *refs,
) -> str | None:
    for ref in refs:
        if not ref:
            continue
        key = str(ref).strip()
        if not key:
            continue
        entity_id = lookup.get((source, key))
        if entity_id:
            return entity_id
        entity_id = lookup.get((source, key.lower()))
        if entity_id:
            return entity_id
    return None


def _jsonb_param(raw) -> str:
    if raw is None:
        return "{}"
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, default=str)


async def _insert_batch(batch: list[tuple]) -> None:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO entity_interactions
                (actor_entity_id, target_entity_id, interaction_type, source, source_record_id,
                 occurred_at, weight, metadata)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb)
            ON CONFLICT (interaction_type, source, source_record_id)
            DO UPDATE SET
                actor_entity_id = EXCLUDED.actor_entity_id,
                target_entity_id = EXCLUDED.target_entity_id,
                occurred_at = EXCLUDED.occurred_at,
                weight = EXCLUDED.weight,
                metadata = EXCLUDED.metadata
            WHERE entity_interactions.actor_entity_id IS DISTINCT FROM EXCLUDED.actor_entity_id
               OR entity_interactions.target_entity_id IS DISTINCT FROM EXCLUDED.target_entity_id
               OR entity_interactions.occurred_at IS DISTINCT FROM EXCLUDED.occurred_at
               OR entity_interactions.weight IS DISTINCT FROM EXCLUDED.weight
               OR entity_interactions.metadata IS DISTINCT FROM EXCLUDED.metadata
        """, batch)


async def refresh_interaction_relationships() -> dict:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_relationships WHERE relationship_type = 'interaction'"
        )
        inserted = await conn.execute("""
            WITH type_counts AS (
                SELECT actor_entity_id, target_entity_id, interaction_type,
                       SUM(weight)::int AS cnt,
                       MAX(occurred_at) AS last_seen
                FROM entity_interactions
                GROUP BY actor_entity_id, target_entity_id, interaction_type
            ),
            source_counts AS (
                SELECT actor_entity_id, target_entity_id, source,
                       SUM(weight)::int AS cnt
                FROM entity_interactions
                GROUP BY actor_entity_id, target_entity_id, source
            ),
            agg AS (
                SELECT actor_entity_id, target_entity_id,
                       SUM(weight)::int AS total_weight,
                       MAX(occurred_at) AS last_seen,
                       COUNT(DISTINCT source) > 1 AS cross_platform
                FROM entity_interactions
                GROUP BY actor_entity_id, target_entity_id
            ),
            reciprocity AS (
                SELECT a.actor_entity_id,
                       a.target_entity_id,
                       COALESCE(b.total_weight, 0)::int AS reverse_total,
                       CASE
                           WHEN GREATEST(a.total_weight, COALESCE(b.total_weight, 0)) = 0 THEN 0
                           ELSE ROUND(
                               LEAST(a.total_weight, COALESCE(b.total_weight, 0))::numeric
                               / GREATEST(a.total_weight, COALESCE(b.total_weight, 0)),
                               4
                           )
                       END AS reciprocity_ratio
                FROM agg a
                LEFT JOIN agg b
                  ON b.actor_entity_id = a.target_entity_id
                 AND b.target_entity_id = a.actor_entity_id
            ),
            type_json AS (
                SELECT actor_entity_id, target_entity_id,
                       jsonb_object_agg(interaction_type, cnt ORDER BY interaction_type) AS by_type,
                       jsonb_object_agg(interaction_type, to_jsonb(last_seen) ORDER BY interaction_type) AS type_last_seen
                FROM type_counts
                GROUP BY actor_entity_id, target_entity_id
            ),
            source_json AS (
                SELECT actor_entity_id, target_entity_id,
                       jsonb_object_agg(source, cnt ORDER BY source) AS by_source
                FROM source_counts
                GROUP BY actor_entity_id, target_entity_id
            )
            INSERT INTO entity_relationships
                (entity_a_id, entity_b_id, relationship_type, weight, cross_platform, sources, last_seen_at)
            SELECT a.actor_entity_id,
                   a.target_entity_id,
                   'interaction',
                   GREATEST(
                       1,
                       ROUND(
                           a.total_weight
                           + (COALESCE(r.reverse_total, 0) * 0.35)
                           + (a.total_weight * COALESCE(r.reciprocity_ratio, 0) * 0.65)
                       )::int
                   ),
                   a.cross_platform,
                   jsonb_build_object(
                       'total', a.total_weight,
                       'reverse_total', COALESCE(r.reverse_total, 0),
                       'reciprocity_ratio', COALESCE(r.reciprocity_ratio, 0),
                       'by_type', COALESCE(t.by_type, '{}'::jsonb),
                       'by_source', COALESCE(s.by_source, '{}'::jsonb),
                       'type_last_seen', COALESCE(t.type_last_seen, '{}'::jsonb),
                       'why', 'Weight blends directed volume with reciprocal depth and balance.'
                   ),
                   a.last_seen
            FROM agg a
            LEFT JOIN reciprocity r
              ON r.actor_entity_id = a.actor_entity_id
             AND r.target_entity_id = a.target_entity_id
            LEFT JOIN type_json t
              ON t.actor_entity_id = a.actor_entity_id
             AND t.target_entity_id = a.target_entity_id
            LEFT JOIN source_json s
              ON s.actor_entity_id = a.actor_entity_id
             AND s.target_entity_id = a.target_entity_id
        """)
    try:
        return {"relationship_rows": int(inserted.split()[-1])}
    except Exception:
        return {"relationship_rows": 0}


async def build_interaction_graph(
    since: datetime | None = None,
    only_sources: set[str] | None = None,
    only_types: set[str] | None = None,
) -> dict:
    collector = get_collector_pool()
    lookup = await _get_entity_lookup()
    only_sources = only_sources or set()
    only_types = only_types or set()
    stats = {
        "processed": 0,
        "inserted": 0,
        "resolved": 0,
        "skipped_unresolved": 0,
        "skipped_self": 0,
        "by_type": {},
    }

    for spec in SOURCE_QUERIES:
        if only_sources and spec["source"] not in only_sources:
            continue
        if only_types and spec["interaction_type"] not in only_types:
            continue

        where_clause = ""
        params: list = []
        if since:
            where_clause = f"AND {spec['time_col']} > $1"
            params.append(since)
        query = spec["query"].format(where_clause=where_clause)

        processed = inserted = resolved = skipped_unresolved = skipped_self = 0
        batch: list[tuple] = []
        async with collector.acquire() as conn:
            async with conn.transaction():
                cursor = conn.cursor(query, *params, timeout=SOURCE_QUERY_TIMEOUT_SECONDS)
                async for row in cursor:
                    processed += 1
                    occurred_at = row["occurred_at"]
                    if occurred_at and not occurred_at.tzinfo:
                        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                    if occurred_at is None:
                        continue
                    actor_id = _resolve_entity(
                        lookup,
                        spec["source"],
                        row.get("actor_ref"),
                        row.get("actor_ref2"),
                    )
                    target_id = _resolve_entity(
                        lookup,
                        spec["source"],
                        row.get("target_ref"),
                        row.get("target_ref2"),
                    )
                    if not actor_id or not target_id:
                        skipped_unresolved += 1
                        continue
                    if actor_id == target_id:
                        skipped_self += 1
                        continue
                    resolved += 1
                    batch.append((
                        actor_id,
                        target_id,
                        spec["interaction_type"],
                        spec["source"],
                        str(row["record_id"]),
                        occurred_at,
                        1,
                        _jsonb_param(row.get("metadata")),
                    ))
                    if len(batch) >= INTERACTION_BATCH_SIZE:
                        await _insert_batch(batch)
                        inserted += len(batch)
                        batch.clear()
                if batch:
                    await _insert_batch(batch)
                    inserted += len(batch)
                    batch.clear()

        key = f"{spec['source']}/{spec['interaction_type']}"
        stats["by_type"][key] = {
            "processed": processed,
            "inserted": inserted,
            "resolved": resolved,
            "skipped_unresolved": skipped_unresolved,
            "skipped_self": skipped_self,
        }
        stats["processed"] += processed
        stats["inserted"] += inserted
        stats["resolved"] += resolved
        stats["skipped_unresolved"] += skipped_unresolved
        stats["skipped_self"] += skipped_self
        logger.info("Interaction %s: %s", key, stats["by_type"][key])

    rel_stats = await refresh_interaction_relationships()
    stats.update(rel_stats)
    logger.info("Interaction graph build complete: %s", stats)
    return stats
