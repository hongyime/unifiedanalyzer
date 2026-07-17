from datetime import datetime, timezone
from fastapi import APIRouter, Query, HTTPException

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["timeline"])


def _partition_names_desc(start: datetime | None = None, floor_year: int = 2010) -> list[str]:
    cursor = start or datetime.now(timezone.utc)
    year = cursor.year
    month = cursor.month
    names: list[str] = []
    while year > floor_year or (year == floor_year and month >= 1):
        names.append(f"timeline_events_{year:04d}_{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return names


async def _fetch_recent_timeline_rows(conn, entity_id: str, limit: int) -> list:
    # Bound to the entity's own date-range so Postgres partition-prunes the 373
    # timeline_events partitions (else it MergeAppends all of them, ~6.6s).
    rng = await conn.fetchrow(
        "SELECT first_event_at, last_event_at FROM entities WHERE id = $1::uuid",
        entity_id,
    )
    if rng and rng["first_event_at"] is not None:
        return await conn.fetch("""
            SELECT id, source, event_type, source_record_id, occurred_at, title, metadata
            FROM timeline_events
            WHERE entity_id = $1::uuid
              AND occurred_at >= $3 AND occurred_at <= $4
            ORDER BY occurred_at DESC
            LIMIT $2
        """, entity_id, limit, rng["first_event_at"], rng["last_event_at"])
    return await conn.fetch("""
        SELECT id, source, event_type, source_record_id, occurred_at, title, metadata
        FROM timeline_events
        WHERE entity_id = $1::uuid AND occurred_at > now() - interval '5 years'
        ORDER BY occurred_at DESC
        LIMIT $2
    """, entity_id, limit)


@router.get("/entities/{entity_id}/timeline-lanes")
async def timeline_lanes(entity_id: str, max_events: int = Query(2500, ge=1, le=8000)):
    """Per-platform event timestamps for the swimlane timeline chart, plus alert
    markers and the overall time range. Epochs (float seconds) keep the payload
    small and let the client lay out the x-axis without date parsing per point.

    timeline_events has 373 monthly partitions but is queried by entity_id (NOT the
    partition key), so an unbounded per-entity query MergeAppends ALL partitions
    (~6.6s even for a sparse entity). We bound occurred_at to the entity's OWN
    active range (entities.first_event_at/last_event_at, maintained by the timeline
    pipeline) so Postgres partition-prunes to just that entity's active months —
    fast for everyone AND complete (all of the entity's history, no global window).
    """
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rng = await conn.fetchrow(
            "SELECT first_event_at, last_event_at FROM entities WHERE id = $1::uuid",
            entity_id,
        )
        if rng and rng["first_event_at"] is not None:
            rows = await conn.fetch("""
                SELECT source, event_type, extract(epoch FROM occurred_at) AS ts
                FROM timeline_events
                WHERE entity_id = $1::uuid
                  AND occurred_at >= $3 AND occurred_at <= $4
                ORDER BY occurred_at DESC
                LIMIT $2
            """, entity_id, max_events, rng["first_event_at"], rng["last_event_at"])
        else:
            # Range not computed yet (new entity, pre-first-pipeline-run): recent
            # fallback so we still prune rather than scan all 373 partitions.
            rows = await conn.fetch("""
                SELECT source, event_type, extract(epoch FROM occurred_at) AS ts
                FROM timeline_events
                WHERE entity_id = $1::uuid AND occurred_at > now() - interval '5 years'
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
    use_fast_path = not source and not event_type and not from_date and not to_date

    async with pool.acquire() as conn:
        # Verify entity exists
        exists = await conn.fetchval(
            "SELECT 1 FROM entities WHERE id = $1::uuid", entity_id
        )
        if not exists:
            raise HTTPException(404, "Entity not found")

        if use_fast_path:
            total = await conn.fetchval(
                "SELECT total_events FROM behavioral_profiles WHERE entity_id = $1::uuid",
                entity_id,
            )
            if total is None:
                # No precomputed profile count — COUNT directly, but bound to the
                # entity's date-range so it partition-prunes (an unbounded COUNT
                # scans all 373 partitions ~16s for a profile-less entity).
                rng = await conn.fetchrow(
                    "SELECT first_event_at, last_event_at FROM entities WHERE id = $1::uuid",
                    entity_id,
                )
                if rng and rng["first_event_at"] is not None:
                    total = await conn.fetchval(
                        "SELECT COUNT(*) FROM timeline_events WHERE entity_id = $1::uuid "
                        "AND occurred_at >= $2 AND occurred_at <= $3",
                        entity_id, rng["first_event_at"], rng["last_event_at"],
                    )
                else:
                    total = await conn.fetchval(
                        "SELECT COUNT(*) FROM timeline_events WHERE entity_id = $1::uuid",
                        entity_id,
                    )
            rows = await _fetch_recent_timeline_rows(conn, entity_id, offset + per_page)
            rows = rows[offset: offset + per_page]
            total = max(int(total or 0), offset + len(rows))
        else:
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
