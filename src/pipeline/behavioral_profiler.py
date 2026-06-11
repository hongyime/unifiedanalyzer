import json
import logging
from collections import defaultdict

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)


async def compute_behavioral_profiles() -> int:
    pool = get_analyzer_pool()
    count = 0

    async with pool.acquire() as conn:
        entities = await conn.fetch("""
            SELECT e.id FROM entities e
            WHERE EXISTS (SELECT 1 FROM timeline_events te WHERE te.entity_id = e.id)
        """)

        for entity in entities:
            eid = entity["id"]

            rows = await conn.fetch("""
                SELECT occurred_at, source, event_type
                FROM timeline_events
                WHERE entity_id = $1 AND occurred_at IS NOT NULL
                ORDER BY occurred_at
            """, eid)

            if len(rows) < 2:
                continue

            hour_dist: dict[str, int] = defaultdict(int)
            dow_dist: dict[str, int] = defaultdict(int)
            source_dist: dict[str, int] = defaultdict(int)

            for r in rows:
                ts = r["occurred_at"]
                hour_dist[str(ts.hour)] += 1
                dow_dist[str(ts.weekday())] += 1
                source_dist[r["source"]] += 1

            first = rows[0]["occurred_at"]
            last = rows[-1]["occurred_at"]
            span_days = max((last - first).total_seconds() / 86400.0, 1.0)
            avg_interval = span_days / (len(rows) - 1) if len(rows) > 1 else span_days

            peak_hour = max(hour_dist, key=hour_dist.get) if hour_dist else None
            if peak_hour is not None:
                quiet_start = (int(peak_hour) + 8) % 24
                quiet_end = (quiet_start + 8) % 24

            await conn.execute("""
                INSERT INTO behavioral_profiles
                    (entity_id, posting_hour_dist, posting_dow_dist, avg_post_interval_days,
                     total_events, last_computed_at)
                VALUES ($1, $2::jsonb, $3::jsonb, $4, $5, NOW())
                ON CONFLICT (entity_id) DO UPDATE SET
                    posting_hour_dist = EXCLUDED.posting_hour_dist,
                    posting_dow_dist = EXCLUDED.posting_dow_dist,
                    avg_post_interval_days = EXCLUDED.avg_post_interval_days,
                    total_events = EXCLUDED.total_events,
                    last_computed_at = NOW(),
                    updated_at = NOW()
            """, eid, json.dumps(hour_dist), json.dumps(dow_dist),
                avg_interval, len(rows))
            count += 1

    logger.info("Computed behavioral profiles for %d entities", count)
    return count
