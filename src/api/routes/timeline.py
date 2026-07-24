import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from fastapi import APIRouter, Query, HTTPException

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["timeline"])

_CONFIDENCE_METADATA_PATHS: tuple[tuple[str, ...], ...] = (
    ("confidence",),
    ("confidence_score",),
    ("source_confidence",),
    ("matched_confidence",),
    ("link_confidence",),
    ("detection_confidence",),
    ("attribution_confidence",),
    ("match_confidence",),
    ("evidence", "confidence"),
    ("source_evidence", "confidence"),
    ("source", "confidence"),
    ("attribution", "confidence"),
    ("match", "confidence"),
)
_NUMERIC_CONFIDENCE_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?$")
_SQL_NUMERIC_CONFIDENCE_RE = (
    "'^[[:space:]]*[+-]?([0-9]+([.][0-9]*)?|[.][0-9]+)[[:space:]]*%?[[:space:]]*$'"
)
_SOURCE_LINK_CONFIDENCE_SOURCE = "entity_platform_links.confidence"
_SOURCE_LINK_CONFIDENCE_JOIN = """
LEFT JOIN LATERAL (
    SELECT MAX(epl.confidence) AS confidence
    FROM entity_platform_links epl
    WHERE epl.entity_id = t.entity_id
      AND epl.source = t.source
      AND epl.retracted_at IS NULL
) link ON TRUE
"""


def _coerce_confidence(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        raw = float(value)
    elif isinstance(value, str):
        normalized = value.strip().replace(" ", "")
        if not _NUMERIC_CONFIDENCE_RE.match(normalized):
            return None
        raw = float(normalized.rstrip("%"))
    else:
        return None

    if not math.isfinite(raw) or raw < 0:
        return None
    if raw > 1:
        raw = raw / 100.0
    return min(raw, 1.0)


def _metadata_path_value(metadata: object, path: tuple[str, ...]) -> object:
    current = metadata
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _derive_timeline_confidence(metadata: object) -> tuple[float | None, str | None]:
    for path in _CONFIDENCE_METADATA_PATHS:
        confidence = _coerce_confidence(_metadata_path_value(metadata, path))
        if confidence is not None:
            return round(confidence, 4), "metadata." + ".".join(path)
    return None, None


def _timeline_confidence_expr(alias: str = "t") -> str:
    candidates: list[str] = []
    for path in _CONFIDENCE_METADATA_PATHS:
        pg_path = "{" + ",".join(path) + "}"
        raw = f"NULLIF({alias}.metadata #>> '{pg_path}', '')"
        cleaned = f"replace(regexp_replace({raw}, '[[:space:]]', '', 'g'), '%', '')"
        candidates.append(
            f"CASE WHEN {raw} ~ {_SQL_NUMERIC_CONFIDENCE_RE} "
            f"THEN ({cleaned})::float8 END"
        )
    raw_expr = "COALESCE(" + ", ".join(candidates) + ")"
    return (
        "CASE "
        f"WHEN ({raw_expr}) IS NULL THEN NULL "
        f"WHEN ({raw_expr}) < 0 THEN NULL "
        f"WHEN ({raw_expr}) > 1 THEN LEAST(1.0, ({raw_expr}) / 100.0) "
        f"ELSE ({raw_expr}) "
        "END"
    )


def _source_link_confidence_expr(alias: str = "link") -> str:
    return (
        "CASE "
        f"WHEN {alias}.confidence IS NULL THEN NULL "
        f"WHEN {alias}.confidence < 0 THEN NULL "
        f"WHEN {alias}.confidence > 1 THEN LEAST(1.0, {alias}.confidence / 100.0) "
        f"ELSE {alias}.confidence "
        "END"
    )


def _effective_timeline_confidence_expr(alias: str = "t", link_alias: str = "link") -> str:
    return f"COALESCE({_timeline_confidence_expr(alias)}, {_source_link_confidence_expr(link_alias)})"


def _optional_row_value(row, key: str):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _timeline_event_payload(row) -> dict:
    confidence, confidence_source = _derive_timeline_confidence(row["metadata"])
    if confidence is None:
        confidence = _coerce_confidence(_optional_row_value(row, "source_confidence"))
        if confidence is not None:
            confidence = round(confidence, 4)
            confidence_source = _SOURCE_LINK_CONFIDENCE_SOURCE
    return {
        "id": str(row["id"]),
        "source": row["source"],
        "event_type": row["event_type"],
        "source_record_id": row["source_record_id"],
        "occurred_at": row["occurred_at"].isoformat(),
        "title": row["title"],
        "metadata": row["metadata"],
        "confidence": confidence,
        "confidence_source": confidence_source,
    }


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
        return await conn.fetch(f"""
            SELECT t.id, t.source, t.event_type, t.source_record_id,
                   t.occurred_at, t.title, t.metadata,
                   {_source_link_confidence_expr()} AS source_confidence
            FROM timeline_events t
            {_SOURCE_LINK_CONFIDENCE_JOIN}
            WHERE t.entity_id = $1::uuid
              AND t.occurred_at >= $3 AND t.occurred_at <= $4
            ORDER BY occurred_at DESC
            LIMIT $2
        """, entity_id, limit, rng["first_event_at"], rng["last_event_at"])
    return await conn.fetch(f"""
        SELECT t.id, t.source, t.event_type, t.source_record_id,
               t.occurred_at, t.title, t.metadata,
               {_source_link_confidence_expr()} AS source_confidence
        FROM timeline_events t
        {_SOURCE_LINK_CONFIDENCE_JOIN}
        WHERE t.entity_id = $1::uuid AND t.occurred_at > now() - interval '5 years'
        ORDER BY occurred_at DESC
        LIMIT $2
    """, entity_id, limit)


@router.get("/entities/{entity_id}/timeline-lanes")
async def timeline_lanes(
    entity_id: str,
    max_events: int = Query(2500, ge=1, le=8000),
    min_confidence: float | None = Query(None, ge=0, le=1),
):
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
            params = [entity_id, max_events, rng["first_event_at"], rng["last_event_at"]]
            confidence_clause = ""
            confidence_join = ""
            if min_confidence is not None:
                params.append(min_confidence)
                confidence_join = _SOURCE_LINK_CONFIDENCE_JOIN
                confidence_clause = f" AND {_effective_timeline_confidence_expr('t')} >= $5"
            rows = await conn.fetch("""
                SELECT source, event_type, extract(epoch FROM occurred_at) AS ts
                FROM timeline_events t
                {confidence_join}
                WHERE t.entity_id = $1::uuid
                  AND t.occurred_at >= $3 AND t.occurred_at <= $4
                  {confidence_clause}
                ORDER BY occurred_at DESC
                LIMIT $2
            """.format(confidence_join=confidence_join, confidence_clause=confidence_clause), *params)
        else:
            # Range not computed yet (new entity, pre-first-pipeline-run): recent
            # fallback so we still prune rather than scan all 373 partitions.
            params = [entity_id, max_events]
            confidence_clause = ""
            confidence_join = ""
            if min_confidence is not None:
                params.append(min_confidence)
                confidence_join = _SOURCE_LINK_CONFIDENCE_JOIN
                confidence_clause = f" AND {_effective_timeline_confidence_expr('t')} >= $3"
            rows = await conn.fetch("""
                SELECT source, event_type, extract(epoch FROM occurred_at) AS ts
                FROM timeline_events t
                {confidence_join}
                WHERE t.entity_id = $1::uuid AND t.occurred_at > now() - interval '5 years'
                  {confidence_clause}
                ORDER BY occurred_at DESC
                LIMIT $2
            """.format(confidence_join=confidence_join, confidence_clause=confidence_clause), *params)
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
    min_confidence: float | None = Query(None, ge=0, le=1),
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

    if min_confidence is not None:
        conditions.append(f"{_effective_timeline_confidence_expr('t')} >= ${idx}")
        params.append(min_confidence)
        idx += 1

    where = "WHERE " + " AND ".join(conditions)
    use_fast_path = not source and not event_type and not from_date and not to_date and min_confidence is None

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
            confidence_join = _SOURCE_LINK_CONFIDENCE_JOIN if min_confidence is not None else ""
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM timeline_events t {confidence_join} {where}", *params
            )

            params.extend([per_page, offset])
            rows = await conn.fetch(f"""
                SELECT t.id, t.source, t.event_type, t.source_record_id,
                       t.occurred_at, t.title, t.metadata,
                       {_source_link_confidence_expr()} AS source_confidence
                FROM timeline_events t
                {_SOURCE_LINK_CONFIDENCE_JOIN}
                {where}
                ORDER BY t.occurred_at DESC
                LIMIT ${idx} OFFSET ${idx + 1}
            """, *params)

    return {
        "data": [_timeline_event_payload(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
