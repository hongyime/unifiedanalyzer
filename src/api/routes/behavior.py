import json
from fastapi import APIRouter, HTTPException

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["behavior"])


@router.get("/entities/{entity_id}/behavior")
async def get_behavior(entity_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        bp = await conn.fetchrow("""
            SELECT * FROM behavioral_profiles WHERE entity_id = $1::uuid
        """, entity_id)

        if not bp:
            raise HTTPException(404, "No behavioral profile computed yet — run an analysis first")

        source_breakdown = await conn.fetch("""
            SELECT source, COUNT(*) AS count
            FROM timeline_events
            WHERE entity_id = $1::uuid
            GROUP BY source ORDER BY count DESC
        """, entity_id)

        type_breakdown = await conn.fetch("""
            SELECT event_type, COUNT(*) AS count
            FROM timeline_events
            WHERE entity_id = $1::uuid
            GROUP BY event_type ORDER BY count DESC
        """, entity_id)

    raw_meta = bp["metadata"]
    if isinstance(raw_meta, str):
        try:
            raw_meta = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            raw_meta = {}
    meta = raw_meta if isinstance(raw_meta, dict) else {}

    return {
        "entity_id": entity_id,
        "posting_hour_dist": bp["posting_hour_dist"],
        "posting_dow_dist": bp["posting_dow_dist"],
        "avg_post_interval_days": bp["avg_post_interval_days"],
        "total_events": bp["total_events"],
        "inferred_timezone": bp["inferred_timezone"],
        "timezone_confidence": bp["timezone_confidence"],
        "last_computed_at": bp["last_computed_at"].isoformat() if bp["last_computed_at"] else None,
        "strava_patterns": meta.get("strava_patterns"),
        "bio_nlp": meta.get("bio_nlp"),
        "graph_analytics": meta.get("graph_analytics"),
        "source_breakdown": [
            {"source": r["source"], "count": r["count"]} for r in source_breakdown
        ],
        "type_breakdown": [
            {"event_type": r["event_type"], "count": r["count"]} for r in type_breakdown
        ],
    }
