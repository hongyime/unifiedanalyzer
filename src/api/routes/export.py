import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["export"])


@router.get("/entities/{entity_id}/export")
async def export_entity(entity_id: str, format: str = "json"):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT * FROM entities WHERE id = $1::uuid", entity_id
        )
        if not entity:
            raise HTTPException(404, "Entity not found")

        links = await conn.fetch("""
            SELECT source, platform_id, platform_username, platform_name,
                   confidence, link_method, is_confirmed
            FROM entity_platform_links WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        signals = await conn.fetch("""
            SELECT signal_type, source_platform, target_platform, value, confidence
            FROM identity_signals WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        events = await conn.fetch("""
            SELECT source, event_type, source_record_id, occurred_at, title
            FROM timeline_events WHERE entity_id = $1::uuid
            ORDER BY occurred_at DESC LIMIT 500
        """, entity_id)

        behavior = await conn.fetchrow(
            "SELECT * FROM behavioral_profiles WHERE entity_id = $1::uuid", entity_id
        )

    data = {
        "entity": {
            "id": str(entity["id"]),
            "canonical_name": entity["canonical_name"],
            "tier": entity["tier"],
            "confidence_score": entity["confidence_score"],
            "signal_count": entity["signal_count"],
        },
        "platform_links": [
            {
                "source": l["source"],
                "platform_id": l["platform_id"],
                "platform_username": l["platform_username"],
                "platform_name": l["platform_name"],
                "confidence": l["confidence"],
                "link_method": l["link_method"],
                "is_confirmed": l["is_confirmed"],
            }
            for l in links
        ],
        "identity_signals": [
            {
                "signal_type": s["signal_type"],
                "source_platform": s["source_platform"],
                "target_platform": s["target_platform"],
                "value": s["value"],
                "confidence": s["confidence"],
            }
            for s in signals
        ],
        "timeline_events": [
            {
                "source": e["source"],
                "event_type": e["event_type"],
                "source_record_id": e["source_record_id"],
                "occurred_at": e["occurred_at"].isoformat() if e["occurred_at"] else None,
                "title": e["title"],
            }
            for e in events
        ],
        "behavioral_profile": {
            "posting_hour_dist": behavior["posting_hour_dist"],
            "posting_dow_dist": behavior["posting_dow_dist"],
            "avg_post_interval_days": behavior["avg_post_interval_days"],
            "total_events": behavior["total_events"],
        } if behavior else None,
    }

    name = entity["canonical_name"] or entity_id
    content = json.dumps(data, indent=2, default=str)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}_export.json"'},
    )
