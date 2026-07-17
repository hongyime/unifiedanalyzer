import logging

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


async def compute_behavioral_profiles() -> int:
    pool = get_analyzer_pool()

    async with pool.acquire() as conn:
        count = await conn.fetchval("""
            WITH base AS (
                SELECT entity_id,
                       count(*)::int AS total_events,
                       min(occurred_at) AS first_seen,
                       max(occurred_at) AS last_seen
                FROM timeline_events
                WHERE entity_id IS NOT NULL
                  AND occurred_at IS NOT NULL
                GROUP BY entity_id
                HAVING count(*) >= 2
            ), hours AS (
                SELECT entity_id,
                       jsonb_object_agg(hour_key, n ORDER BY hour_key) AS posting_hour_dist
                FROM (
                    SELECT entity_id,
                           extract(hour FROM occurred_at)::int::text AS hour_key,
                           count(*)::int AS n
                    FROM timeline_events
                    WHERE entity_id IS NOT NULL
                      AND occurred_at IS NOT NULL
                    GROUP BY entity_id, extract(hour FROM occurred_at)::int
                ) h
                GROUP BY entity_id
            ), dows AS (
                SELECT entity_id,
                       jsonb_object_agg(dow_key, n ORDER BY dow_key) AS posting_dow_dist
                FROM (
                    SELECT entity_id,
                           (extract(isodow FROM occurred_at)::int - 1)::text AS dow_key,
                           count(*)::int AS n
                    FROM timeline_events
                    WHERE entity_id IS NOT NULL
                      AND occurred_at IS NOT NULL
                    GROUP BY entity_id, extract(isodow FROM occurred_at)::int
                ) d
                GROUP BY entity_id
            ), upserted AS (
                INSERT INTO behavioral_profiles
                    (entity_id, posting_hour_dist, posting_dow_dist,
                     avg_post_interval_days, total_events, last_computed_at)
                SELECT b.entity_id,
                       COALESCE(h.posting_hour_dist, '{}'::jsonb),
                       COALESCE(d.posting_dow_dist, '{}'::jsonb),
                       GREATEST(EXTRACT(EPOCH FROM (b.last_seen - b.first_seen)) / 86400.0, 1.0)
                         / GREATEST(b.total_events - 1, 1),
                       b.total_events,
                       NOW()
                FROM base b
                LEFT JOIN hours h ON h.entity_id = b.entity_id
                LEFT JOIN dows d ON d.entity_id = b.entity_id
                ON CONFLICT (entity_id) DO UPDATE SET
                    posting_hour_dist = EXCLUDED.posting_hour_dist,
                    posting_dow_dist = EXCLUDED.posting_dow_dist,
                    avg_post_interval_days = EXCLUDED.avg_post_interval_days,
                    total_events = EXCLUDED.total_events,
                    last_computed_at = NOW(),
                    updated_at = NOW()
                RETURNING 1
            )
            SELECT count(*) FROM upserted
        """)

    logger.info("Computed behavioral profiles for %d entities", count)
    return count or 0
