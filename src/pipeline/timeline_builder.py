import logging
from datetime import datetime, timezone

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000

PLATFORM_QUERIES = [
    {
        "source": "github",
        "event_type": "CODE_COMMIT",
        "query": """
            SELECT c.sha AS record_id, c.date AS occurred_at,
                   LEFT(c.message, 200) AS title, c.author_login AS entity_ref
            FROM github_commits c
            WHERE c.date IS NOT NULL {where_clause}
            ORDER BY c.date DESC
        """,
        "time_col": "c.date",
    },
    {
        "source": "strava",
        "event_type": "PHYSICAL_ACTIVITY",
        "query": """
            SELECT a.platform_activity_id::text AS record_id, a.start_date AS occurred_at,
                   a.name AS title, ath.username AS entity_ref
            FROM strava_activities a
            LEFT JOIN strava_athletes ath ON a.athlete_id = ath.id
            WHERE a.start_date IS NOT NULL {where_clause}
            ORDER BY a.start_date DESC
        """,
        "time_col": "a.start_date",
    },
    {
        "source": "instagram",
        "event_type": "CONTENT_PUBLISHED",
        "query": """
            SELECT p.platform_post_id AS record_id, p.platform_created_at AS occurred_at,
                   LEFT(p.caption, 200) AS title, pr.username AS entity_ref
            FROM instagram_posts p
            LEFT JOIN instagram_profiles pr ON p.profile_id = pr.id
            WHERE p.platform_created_at IS NOT NULL {where_clause}
            ORDER BY p.platform_created_at DESC
        """,
        "time_col": "p.platform_created_at",
    },
    {
        "source": "telegram",
        "event_type": "MESSAGE_SENT",
        "query": """
            SELECT m.platform_message_id AS record_id, m.platform_created_at AS occurred_at,
                   LEFT(m.text, 200) AS title, u.username AS entity_ref
            FROM telegram_messages m
            LEFT JOIN telegram_users u ON m.sender_id = u.id
            WHERE m.platform_created_at IS NOT NULL {where_clause}
            ORDER BY m.platform_created_at DESC
        """,
        "time_col": "m.platform_created_at",
    },
    {
        "source": "whatsapp",
        "event_type": "MESSAGE_SENT",
        "query": """
            SELECT m.platform_message_id AS record_id, m.timestamp AS occurred_at,
                   LEFT(m.text, 200) AS title, u.name AS entity_ref
            FROM whatsapp_messages m
            LEFT JOIN whatsapp_users u ON m.sender_id = u.id
            WHERE m.timestamp IS NOT NULL {where_clause}
            ORDER BY m.timestamp DESC
        """,
        "time_col": "m.timestamp",
    },
    {
        "source": "tiktok",
        "event_type": "CONTENT_PUBLISHED",
        "query": """
            SELECT p.platform_post_id AS record_id, p.create_time AS occurred_at,
                   LEFT(p.title, 200) AS title, pr.username AS entity_ref
            FROM tiktok_posts p
            LEFT JOIN tiktok_profiles pr ON p.profile_id = pr.id
            WHERE p.create_time IS NOT NULL {where_clause}
            ORDER BY p.create_time DESC
        """,
        "time_col": "p.create_time",
    },
    {
        "source": "youtube",
        "event_type": "VIDEO_PUBLISHED",
        "query": """
            SELECT v.platform_video_id AS record_id, v.platform_published_at AS occurred_at,
                   v.title AS title, ch.title AS entity_ref
            FROM youtube_videos v
            LEFT JOIN youtube_channels ch ON v.channel_id = ch.id
            WHERE v.platform_published_at IS NOT NULL {where_clause}
            ORDER BY v.platform_published_at DESC
        """,
        "time_col": "v.platform_published_at",
    },
    {
        "source": "lemon8",
        "event_type": "CONTENT_PUBLISHED",
        "query": """
            SELECT p.platform_post_id AS record_id,
                   COALESCE(p.platform_created_at, p.collected_at) AS occurred_at,
                   LEFT(p.title, 200) AS title, pr.username AS entity_ref
            FROM lemon8_posts p
            LEFT JOIN lemon8_profiles pr ON p.profile_id = pr.id
            WHERE COALESCE(p.platform_created_at, p.collected_at) IS NOT NULL {where_clause}
            ORDER BY COALESCE(p.platform_created_at, p.collected_at) DESC
        """,
        "time_col": "COALESCE(p.platform_created_at, p.collected_at)",
    },
    {
        "source": "github",
        "event_type": "ISSUE_OPENED",
        "query": """
            SELECT i.platform_issue_id::text AS record_id, i.created_at AS occurred_at,
                   i.title AS title, NULL AS entity_ref
            FROM github_issues i
            WHERE i.created_at IS NOT NULL {where_clause}
            ORDER BY i.created_at DESC
        """,
        "time_col": "i.created_at",
    },
    {
        "source": "instagram",
        "event_type": "COMMENT_POSTED",
        "query": """
            SELECT c.platform_comment_id AS record_id, c.platform_created_at AS occurred_at,
                   LEFT(c.text, 200) AS title, c.author_username AS entity_ref
            FROM instagram_comments c
            WHERE c.platform_created_at IS NOT NULL {where_clause}
            ORDER BY c.platform_created_at DESC
        """,
        "time_col": "c.platform_created_at",
    },
]


async def _get_entity_lookup() -> dict[tuple[str, str], str]:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT entity_id::text, source, platform_id, platform_username
            FROM entity_platform_links
        """)
    lookup: dict[tuple[str, str], str] = {}
    for r in rows:
        if r["platform_username"]:
            lookup[(r["source"], r["platform_username"].lower())] = r["entity_id"]
        lookup[(r["source"], r["platform_id"])] = r["entity_id"]
    return lookup


async def build_timeline(since: datetime | None = None) -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()
    entity_lookup = await _get_entity_lookup()

    stats = {"total": 0, "inserted": 0, "skipped_tables": []}

    for pq in PLATFORM_QUERIES:
        where_clause = ""
        params: list = []
        if since:
            where_clause = f"AND {pq['time_col']} > $1"
            params.append(since)

        query = pq["query"].format(where_clause=where_clause)
        source_name = f"{pq['source']}/{pq['event_type']}"

        try:
            async with collector.acquire() as conn:
                async with conn.transaction():
                    stmt = await conn.prepare(query)
                    cursor = stmt.cursor(*params)

                    batch: list[tuple] = []
                    row_count = 0

                    async for row in cursor:
                        entity_ref = row.get("entity_ref")
                        entity_id = None
                        if entity_ref:
                            entity_id = entity_lookup.get((pq["source"], entity_ref.lower()))
                            if not entity_id:
                                entity_id = entity_lookup.get((pq["source"], entity_ref))

                        occurred_at = row["occurred_at"]
                        if occurred_at and not occurred_at.tzinfo:
                            occurred_at = occurred_at.replace(tzinfo=timezone.utc)

                        batch.append((
                            entity_id,
                            pq["source"],
                            pq["event_type"],
                            str(row["record_id"]),
                            occurred_at,
                            row.get("title"),
                        ))
                        row_count += 1

                        if len(batch) >= BATCH_SIZE:
                            await _insert_batch(analyzer, batch)
                            stats["inserted"] += len(batch)
                            batch.clear()

                    if batch:
                        await _insert_batch(analyzer, batch)
                        stats["inserted"] += len(batch)

                    stats["total"] += row_count
                    logger.info("Timeline %s: %d rows processed", source_name, row_count)

        except Exception as e:
            logger.warning("Skipping %s: %s", source_name, e)
            stats["skipped_tables"].append(source_name)

    logger.info("Timeline build complete: %s", stats)
    return stats


async def _insert_batch(pool, batch: list[tuple]) -> None:
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO timeline_events
                (entity_id, source, event_type, source_record_id, occurred_at, title)
            VALUES ($1::uuid, $2, $3, $4, $5, $6)
            -- P2-5: 4-col conflict target (includes occurred_at) so the upsert
            -- works on the month-partitioned table, where every unique key must
            -- include the partition column. occurred_at is deterministic per
            -- (source,event_type,source_record_id), so this preserves the old
            -- 3-col semantics. Backed by idx_timeline_uniq4.
            ON CONFLICT (source, event_type, source_record_id, occurred_at)
            DO UPDATE SET entity_id = EXCLUDED.entity_id
        """, batch)
