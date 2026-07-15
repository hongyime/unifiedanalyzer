from datetime import datetime
from fastapi import APIRouter, Query, HTTPException

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["timeline"])


@router.get("/entities/{entity_id}/timeline-lanes")
async def timeline_lanes(entity_id: str, max_events: int = Query(2500, ge=1, le=8000)):
    """Per-platform event timestamps for the swimlane timeline chart, plus alert
    markers and the overall time range. Epochs (float seconds) keep the payload
    small and let the client lay out the x-axis without date parsing per point."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT source, event_type, extract(epoch FROM occurred_at) AS ts
            FROM timeline_events
            WHERE entity_id = $1::uuid AND occurred_at > '2010-01-01'
            ORDER BY occurred_at DESC
            LIMIT $2
        """, entity_id, max_events)
        alert_rows = await conn.fetch("""
            SELECT alert_type, extract(epoch FROM detected_at) AS ts
            FROM alerts
            WHERE entity_id = $1::uuid AND detected_at > '2010-01-01'
            ORDER BY detected_at
        """, entity_id)

    lanes: dict[str, list] = {}
    for r in reversed(rows):
        lanes.setdefault(r["source"], []).append({"t": float(r["ts"]), "type": r["event_type"]})
    all_ts = [e["t"] for evs in lanes.values() for e in evs]
    return {
        "lanes": [{"source": s, "events": evs} for s, evs in sorted(lanes.items(), key=lambda kv: -len(kv[1]))],
        "alerts": [{"type": a["alert_type"], "t": float(a["ts"])} for a in alert_rows],
        "min_t": min(all_ts) if all_ts else None,
        "max_t": max(all_ts) if all_ts else None,
        "total": len(all_ts),
    }


@router.get("/entities/{entity_id}/timeline")
async def get_entity_timeline(
    entity_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    source: str | None = None,
    event_type: str | None = None,
    from_date: datetime | None = Query(None, alias="from"),
    to_date: datetime | None = Query(None, alias="to"),
):
    pool = get_analyzer_pool()
    offset = (page - 1) * per_page

    conditions = ["t.entity_id = $1::uuid"]
    params: list = [entity_id]
    idx = 2

    if source:
        conditions.append(f"t.source = ${idx}")
        params.append(source)
        idx += 1

    if event_type:
        conditions.append(f"t.event_type = ${idx}")
        params.append(event_type)
        idx += 1

    if from_date:
        conditions.append(f"t.occurred_at >= ${idx}")
        params.append(from_date)
        idx += 1

    if to_date:
        conditions.append(f"t.occurred_at <= ${idx}")
        params.append(to_date)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)

    async with pool.acquire() as conn:
        # Verify entity exists
        exists = await conn.fetchval(
            "SELECT 1 FROM entities WHERE id = $1::uuid", entity_id
        )
        if not exists:
            raise HTTPException(404, "Entity not found")

        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM timeline_events t {where}", *params
        )

        params.extend([per_page, offset])
        rows = await conn.fetch(f"""
            SELECT t.id, t.source, t.event_type, t.source_record_id,
                   t.occurred_at, t.title, t.metadata
            FROM timeline_events t
            {where}
            ORDER BY t.occurred_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """, *params)

    return {
        "data": [
            {
                "id": str(r["id"]),
                "source": r["source"],
                "event_type": r["event_type"],
                "source_record_id": r["source_record_id"],
                "occurred_at": r["occurred_at"].isoformat(),
                "title": r["title"],
                "metadata": r["metadata"],
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
