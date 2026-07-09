import logging
from datetime import datetime, timedelta, timezone

from src.db.connection import get_analyzer_pool, get_collector_pool

logger = logging.getLogger(__name__)

BATCH_SIZE = 5000

# Bogus-timestamp floor. Source records occasionally carry epoch-0 (1970) or
# clock-error dates (e.g. GitHub commits with a misconfigured author clock dated
# 1985). Git itself dates to 2005, and this dataset has no legitimate pre-2005
# activity, so events before this are dropped rather than persisted — otherwise
# they land as permanent 1970/198x noise in the timeline (and its partition
# default). Deterministic, so it never removes a legit event.
TIMELINE_MIN_DATE = datetime(2005, 1, 1, tzinfo=timezone.utc)
# Future ceiling: an event can't legitimately be more than a small clock-skew
# margin into the future. Records dated years ahead (seen up to 2042) are clock
# errors — drop them too rather than seed far-future partition noise.
TIMELINE_MAX_FUTURE = timedelta(days=366)

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
        # entity_ref = platform_user_id (matches entity_platform_links.platform_id,
        # the stable numeric Telegram id) with username as fallback. Previously
        # this matched on username ALONE, which is NULL for most senders — only
        # ~1.2% of 1.28M telegram events were attributed. (2026-07-10 attribution fix)
        "query": """
            SELECT m.platform_message_id AS record_id, m.platform_created_at AS occurred_at,
                   LEFT(m.text, 200) AS title,
                   u.platform_user_id AS entity_ref, u.username AS entity_ref2
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
        # entity_ref = reconstructed JID (phone_number || '@s.whatsapp.net'), which
        # is what entity_platform_links.platform_id stores for WhatsApp; bare
        # phone_number is the fallback. Previously matched on u.name (display name),
        # which never matches a JID key — 0% of 50.7k whatsapp events were
        # attributed despite 637 whatsapp entities. (2026-07-10 attribution fix)
        "query": """
            SELECT m.platform_message_id AS record_id, m.timestamp AS occurred_at,
                   LEFT(m.text, 200) AS title,
                   (u.phone_number || '@s.whatsapp.net') AS entity_ref,
                   u.phone_number AS entity_ref2
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
        # entity_ref = platform_channel_id (the UC... id in
        # entity_platform_links.platform_id) with custom_url (@handle) fallback.
        # Previously matched on ch.title (display name) — 0% of 22.4k youtube
        # events were attributed despite 491 youtube entities. (2026-07-10 fix)
        "query": """
            SELECT v.platform_video_id AS record_id, v.platform_published_at AS occurred_at,
                   v.title AS title,
                   ch.platform_channel_id AS entity_ref, ch.custom_url AS entity_ref2
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


async def ensure_timeline_partitions(months_ahead: int = 6) -> None:
    """P2-5 maintenance: guarantee a monthly partition exists for the current
    month and the next `months_ahead`, so newly-built events never silently fall
    into the DEFAULT partition. No-op when timeline_events isn't partitioned
    (fresh/pre-migration installs). Idempotent and cheap (catalog lookups)."""
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        is_part = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_partitioned_table pt
                JOIN pg_class c ON c.oid = pt.partrelid
                WHERE c.relname = 'timeline_events'
            )
        """)
        if not is_part:
            return
        base = datetime.now(timezone.utc)
        for i in range(months_ahead + 1):
            y = base.year + (base.month - 1 + i) // 12
            mo = (base.month - 1 + i) % 12 + 1
            ny, nmo = (y + 1, 1) if mo == 12 else (y, mo + 1)
            name = f"timeline_events_{y:04d}_{mo:02d}"
            if await conn.fetchval("SELECT to_regclass($1)", f"public.{name}") is not None:
                continue
            try:
                await conn.execute(
                    f"CREATE TABLE {name} PARTITION OF timeline_events "
                    f"FOR VALUES FROM ('{y:04d}-{mo:02d}-01') TO ('{ny:04d}-{nmo:02d}-01')"
                )
                logger.info("Created timeline partition %s", name)
            except Exception:
                # e.g. a stray out-of-range row already sits in DEFAULT for this
                # month — non-fatal; the row stays in default, new ones still work.
                logger.debug("Could not create partition %s (non-fatal)", name, exc_info=True)


async def build_timeline(since: datetime | None = None) -> dict:
    collector = get_collector_pool()
    analyzer = get_analyzer_pool()
    # Keep upcoming monthly partitions provisioned before inserting.
    try:
        await ensure_timeline_partitions()
    except Exception:
        logger.debug("ensure_timeline_partitions failed (non-fatal)", exc_info=True)
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
                        # Try the primary entity_ref then an optional fallback
                        # (entity_ref2) — some sources match on an id column but
                        # keep a handle/username as a secondary key (see the
                        # per-source query comments). First ref that resolves wins.
                        entity_id = None
                        for _ref_col in ("entity_ref", "entity_ref2"):
                            entity_ref = row.get(_ref_col)
                            if not entity_ref:
                                continue
                            entity_ref = str(entity_ref)
                            entity_id = entity_lookup.get((pq["source"], entity_ref.lower())) \
                                or entity_lookup.get((pq["source"], entity_ref))
                            if entity_id:
                                break

                        occurred_at = row["occurred_at"]
                        if occurred_at and not occurred_at.tzinfo:
                            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
                        # Skip null/bogus timestamps so they don't regenerate as
                        # 1970/198x (or far-future) timeline noise.
                        if not occurred_at or occurred_at < TIMELINE_MIN_DATE:
                            continue
                        if occurred_at > datetime.now(timezone.utc) + TIMELINE_MAX_FUTURE:
                            continue

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
            -- Only write when the attribution actually changes. Without this
            -- guard every conflicting row is rewritten on each full rebuild,
            -- churning millions of dead tuples and growing the C:-backed
            -- Postgres volume for no reason. IS DISTINCT FROM handles NULLs on
            -- both sides. (2026-07-10 disk-safety + re-attribution fix)
            WHERE timeline_events.entity_id IS DISTINCT FROM EXCLUDED.entity_id
        """, batch)
