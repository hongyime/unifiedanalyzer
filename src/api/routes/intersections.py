import bisect
import json
import logging
import math
import os
import time
from datetime import datetime
from itertools import combinations
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.api.face_lookup import face_crop_url, representative_faces
from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.location_evidence import (
    attach_location_evidence_key,
    fetch_location_evidence_statuses,
    is_location_suppressed,
    upsert_location_evidence_batch,
)

router = APIRouter(tags=["intersections"])
logger = logging.getLogger(__name__)

_MAX_PHYSICAL_POINTS_PER_ENTITY = 2500
_MAX_PHYSICAL_RESULTS = 100
_MAX_DIGITAL_RESULTS = 200
_LOCATION_INTERSECTION_MATERIALIZE_LIMIT = int(os.getenv("LOCATION_INTERSECTION_MATERIALIZE_LIMIT", "5000"))


class IntersectRequest(BaseModel):
    ids: list[str] = Field(..., min_length=2, max_length=12)
    radius_m: float = Field(200.0, ge=10.0, le=5000.0)
    window_minutes: int = Field(60, ge=1, le=1440)
    from_date: datetime | None = Field(None, alias="from")
    to_date: datetime | None = Field(None, alias="to")

    class Config:
        populate_by_name = True


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _parse_latlng(raw: Any) -> tuple[float, float] | None:
    if not raw:
        return None
    try:
        text = str(raw).strip("[]() ")
        lat_s, lng_s = text.split(",", 1)
        return float(lat_s), float(lng_s)
    except (TypeError, ValueError):
        return None


def _haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(a_lat)
    phi2 = math.radians(b_lat)
    d_phi = math.radians(b_lat - a_lat)
    d_lambda = math.radians(b_lng - a_lng)
    h = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(h))


def _point(
    entity_id: str,
    source: str,
    record_id: str,
    occurred_at: datetime | None,
    lat: float,
    lng: float,
    label: str | None,
    *,
    evidence_type: str = "gps",
    source_table: str | None = None,
    confidence: float | None = None,
    evidence_key: str | None = None,
    status: str | None = None,
) -> dict:
    point = {
        "entity_id": entity_id,
        "source": source,
        "record_id": record_id,
        "occurred_at": occurred_at,
        "lat": lat,
        "lng": lng,
        "label": label,
        "evidence_type": evidence_type,
        "source_table": source_table,
        "source_record_id": record_id,
        "confidence": confidence,
        "status": status,
    }
    if evidence_key:
        point["evidence_key"] = evidence_key
    return point


def _serialise_point(point: dict, entity_names: dict[str, str | None]) -> dict:
    return {
        "entity_id": point["entity_id"],
        "entity_name": entity_names.get(point["entity_id"]),
        "source": point["source"],
        "record_id": point["record_id"],
        "occurred_at": _iso(point["occurred_at"]),
        "lat": point["lat"],
        "lng": point["lng"],
        "label": point["label"],
        "evidence_type": point.get("evidence_type"),
        "evidence_key": point.get("evidence_key"),
        "confidence": point.get("confidence"),
        "status": point.get("status"),
    }


def _physical_hit(points: list[dict], entity_names: dict[str, str | None], radius_m: float) -> dict:
    lat = sum(p["lat"] for p in points) / len(points)
    lng = sum(p["lng"] for p in points) / len(points)
    distances = [
        _haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
        for a, b in combinations(points, 2)
    ]
    times = [p["occurred_at"] for p in points if p["occurred_at"]]
    time_gap = 0.0
    if len(times) >= 2:
        time_gap = (max(times) - min(times)).total_seconds() / 60.0
    return {
        "type": "same_place_same_time",
        "locus": {"lat": round(lat, 6), "lng": round(lng, 6)},
        "radius_m": radius_m,
        "max_distance_m": round(max(distances or [0.0]), 1),
        "time_gap_minutes": round(time_gap, 1),
        "sources": sorted({p["source"] for p in points}),
        "evidence": [_serialise_point(p, entity_names) for p in points],
    }


def _dedupe_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in ids:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


async def _entity_context(entity_ids: list[str]) -> tuple[dict[str, str | None], list[dict], dict[str, str | None]]:
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        entities = await conn.fetch(
            "SELECT id::text AS id, canonical_name FROM entities WHERE id = ANY($1::uuid[])",
            entity_ids,
        )
        links = await conn.fetch("""
            SELECT entity_id::text AS entity_id, source, platform_id, platform_username, platform_name
            FROM entity_platform_links
            WHERE entity_id = ANY($1::uuid[])
              AND retracted_at IS NULL
        """, entity_ids)
        rep = await representative_faces(conn, entity_ids)
    names = {r["id"]: r["canonical_name"] for r in entities}
    faces = {entity_id: face_crop_url(rep.get(entity_id)) for entity_id in entity_ids}
    return names, [dict(r) for r in links], faces


def _link_values(links: list[dict], entity_ids: list[str], source: str) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    owners: list[str] = []
    wanted = set(entity_ids)
    for link in links:
        if link["entity_id"] not in wanted or link["source"] != source:
            continue
        if link.get("platform_id"):
            ids.append(str(link["platform_id"]))
            owners.append(str(link["platform_id"]))
        if link.get("platform_username"):
            owners.append(str(link["platform_username"]).lower())
    return ids, owners


async def _fetch_physical_points(
    entity_ids: list[str],
    links: list[dict],
    from_date: datetime | None,
    to_date: datetime | None,
) -> tuple[list[dict], bool]:
    try:
        collector = get_collector_pool()
    except Exception as exc:  # noqa: BLE001 - collector is optional
        logger.warning("intersections physical collector read skipped: %s", exc)
        return [], True

    points: list[dict] = []
    per_entity_limit = max(1, len(entity_ids)) * _MAX_PHYSICAL_POINTS_PER_ENTITY
    try:
        async with collector.acquire() as conn:
            strava_pairs: list[tuple[int, str]] = []
            for link in links:
                if link["source"] != "strava" or link["entity_id"] not in entity_ids:
                    continue
                try:
                    strava_pairs.append((int(link["platform_id"]), link["entity_id"]))
                except (TypeError, ValueError):
                    continue
            if strava_pairs:
                pids = [p for p, _ in strava_pairs]
                eids = [e for _, e in strava_pairs]
                params: list[Any] = [pids, eids]
                filters = ["COALESCE(act.start_latlng_full, act.start_latlng) IS NOT NULL"]
                if from_date:
                    filters.append(f"act.start_date >= ${len(params) + 1}")
                    params.append(from_date)
                if to_date:
                    filters.append(f"act.start_date <= ${len(params) + 1}")
                    params.append(to_date)
                params.append(per_entity_limit)
                rows = await conn.fetch(f"""
                WITH wanted(platform_athlete_id, entity_id) AS (
                    SELECT * FROM unnest($1::bigint[], $2::uuid[])
                )
                SELECT w.entity_id::text AS entity_id,
                       act.platform_activity_id::text AS record_id,
                       act.start_date AS occurred_at,
                       act.name AS label,
                       COALESCE(act.start_latlng_full, act.start_latlng) AS latlng
                FROM wanted w
                JOIN strava_athletes a ON a.platform_athlete_id = w.platform_athlete_id
                JOIN strava_activities act ON act.athlete_id = a.id
                WHERE {' AND '.join(filters)}
                ORDER BY act.start_date DESC NULLS LAST
                LIMIT ${len(params)}
            """, *params)
                for row in rows:
                    latlng = _parse_latlng(row["latlng"])
                    if latlng and row["occurred_at"]:
                        points.append(_point(
                            row["entity_id"], "strava", row["record_id"], row["occurred_at"],
                            latlng[0], latlng[1], row["label"],
                            evidence_type="gps_start",
                            source_table="strava_activities",
                            confidence=0.7,
                        ))

            ig_pairs = [
                (str(link["platform_id"]), link["entity_id"])
                for link in links
                if link["source"] == "instagram" and link["platform_id"] and link["entity_id"] in entity_ids
            ]
            if ig_pairs:
                pids = [p for p, _ in ig_pairs]
                eids = [e for _, e in ig_pairs]
                params = [pids, eids]
                filters = ["p.location_lat IS NOT NULL", "p.location_lng IS NOT NULL"]
                if from_date:
                    filters.append(f"p.platform_created_at >= ${len(params) + 1}")
                    params.append(from_date)
                if to_date:
                    filters.append(f"p.platform_created_at <= ${len(params) + 1}")
                    params.append(to_date)
                params.append(per_entity_limit)
                rows = await conn.fetch(f"""
                WITH wanted(platform_user_id, entity_id) AS (
                    SELECT * FROM unnest($1::text[], $2::uuid[])
                )
                SELECT w.entity_id::text AS entity_id,
                       p.platform_post_id::text AS record_id,
                       p.platform_created_at AS occurred_at,
                       p.location_name AS label,
                       p.location_lat AS lat,
                       p.location_lng AS lng
                FROM wanted w
                JOIN instagram_profiles pr ON pr.platform_user_id::text = w.platform_user_id
                JOIN instagram_posts p ON p.profile_id = pr.id
                WHERE {' AND '.join(filters)}
                ORDER BY p.platform_created_at DESC NULLS LAST
                LIMIT ${len(params)}
            """, *params)
                for row in rows:
                    if row["occurred_at"]:
                        points.append(_point(
                            row["entity_id"], "instagram", row["record_id"], row["occurred_at"],
                            float(row["lat"]), float(row["lng"]), row["label"],
                            evidence_type="venue_tag",
                            source_table="instagram_posts",
                            confidence=0.75,
                        ))

            tg_pairs = [
                (str(link["platform_id"]), link["entity_id"])
                for link in links
                if link["source"] == "telegram" and link["platform_id"] and link["entity_id"] in entity_ids
            ]
            if tg_pairs:
                pids = [p for p, _ in tg_pairs]
                eids = [e for _, e in tg_pairs]
                params = [pids, eids]
                filters = []
                if from_date:
                    filters.append(f"m.platform_created_at >= ${len(params) + 1}")
                    params.append(from_date)
                if to_date:
                    filters.append(f"m.platform_created_at <= ${len(params) + 1}")
                    params.append(to_date)
                where = (" AND " + " AND ".join(filters)) if filters else ""
                params.append(per_entity_limit)
                rows = await conn.fetch(f"""
                WITH wanted(platform_user_id, entity_id) AS (
                    SELECT * FROM unnest($1::text[], $2::uuid[])
                )
                SELECT w.entity_id::text AS entity_id,
                       l.platform_message_id::text AS record_id,
                       m.platform_created_at AS occurred_at,
                       COALESCE(NULLIF(l.venue_title, ''), NULLIF(l.venue_address, ''), LEFT(COALESCE(m.text, m.caption, ''), 120)) AS label,
                       l.latitude AS lat,
                       l.longitude AS lng
                FROM wanted w
                JOIN telegram_users u ON u.platform_user_id::text = w.platform_user_id
                JOIN telegram_messages m ON m.sender_id = u.id
                JOIN telegram_message_locations l ON l.platform_message_id = m.platform_message_id
                WHERE m.platform_created_at IS NOT NULL {where}
                ORDER BY m.platform_created_at DESC NULLS LAST
                LIMIT ${len(params)}
            """, *params)
                for row in rows:
                    points.append(_point(
                        row["entity_id"], "telegram", row["record_id"], row["occurred_at"],
                        float(row["lat"]), float(row["lng"]), row["label"],
                        evidence_type="message_location",
                        source_table="telegram_message_locations",
                        confidence=0.9,
                    ))

            wa_pairs = [
                (str(link["platform_id"]), link["entity_id"])
                for link in links
                if link["source"] == "whatsapp" and link["platform_id"] and link["entity_id"] in entity_ids
            ]
            if wa_pairs:
                pids = [p for p, _ in wa_pairs]
                eids = [e for _, e in wa_pairs]
                params = [pids, eids]
                filters = []
                if from_date:
                    filters.append(f"wm.timestamp >= ${len(params) + 1}")
                    params.append(from_date)
                if to_date:
                    filters.append(f"wm.timestamp <= ${len(params) + 1}")
                    params.append(to_date)
                where = (" AND " + " AND ".join(filters)) if filters else ""
                params.append(per_entity_limit)
                rows = await conn.fetch(f"""
                WITH wanted(platform_user_id, entity_id) AS (
                    SELECT * FROM unnest($1::text[], $2::uuid[])
                )
                SELECT w.entity_id::text AS entity_id,
                       loc.platform_message_id::text AS record_id,
                       wm.timestamp AS occurred_at,
                       COALESCE(NULLIF(loc.name, ''), NULLIF(loc.address, ''), LEFT(COALESCE(wm.text, ''), 120)) AS label,
                       loc.latitude AS lat,
                       loc.longitude AS lng
                FROM wanted w
                JOIN whatsapp_users wu ON wu.platform_user_id::text = w.platform_user_id
                JOIN whatsapp_messages wm ON wm.sender_id = wu.id
                JOIN whatsapp_message_locations loc ON loc.platform_message_id = wm.platform_message_id
                WHERE wm.timestamp IS NOT NULL {where}
                ORDER BY wm.timestamp DESC NULLS LAST
                LIMIT ${len(params)}
            """, *params)
                for row in rows:
                    points.append(_point(
                        row["entity_id"], "whatsapp", row["record_id"], row["occurred_at"],
                        float(row["lat"]), float(row["lng"]), row["label"],
                        evidence_type="message_location",
                        source_table="whatsapp_message_locations",
                        confidence=0.9,
                    ))
    except Exception as exc:  # noqa: BLE001 - collector is optional for this API
        logger.warning("intersections physical collector read skipped: %s", exc)
        return [], True

    return points, False


def _location_item_from_point(point: dict) -> dict:
    occurred_at = point.get("occurred_at")
    return {
        "source": point.get("source"),
        "evidence_type": point.get("evidence_type") or "gps",
        "source_table": point.get("source_table"),
        "source_record_id": point.get("source_record_id") or point.get("record_id"),
        "occurred_at": occurred_at.isoformat() if isinstance(occurred_at, datetime) else occurred_at,
        "lat": point.get("lat"),
        "lng": point.get("lng"),
        "label": point.get("label"),
        "confidence": point.get("confidence"),
        "evidence_key": point.get("evidence_key"),
    }


async def _apply_location_registry(points: list[dict]) -> tuple[list[dict], int, int]:
    """Attach deterministic evidence keys and drop rejected/suppressed points."""
    if not points:
        return [], 0, 0
    keyed: list[dict] = []
    for point in points:
        item = attach_location_evidence_key(point["entity_id"], _location_item_from_point(point))
        keyed.append({**point, "evidence_key": item["evidence_key"]})

    materialized = 0
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        if len(keyed) <= _LOCATION_INTERSECTION_MATERIALIZE_LIMIT:
            by_entity: dict[str, list[dict]] = {}
            for point in keyed:
                by_entity.setdefault(point["entity_id"], []).append(_location_item_from_point(point))
            for entity_id, items in by_entity.items():
                materialized += await upsert_location_evidence_batch(conn, entity_id, items)
        statuses = await fetch_location_evidence_statuses(
            conn,
            [point["evidence_key"] for point in keyed],
        )

    visible: list[dict] = []
    suppressed = 0
    for point in keyed:
        status = statuses.get(point["evidence_key"], {})
        point.update(status)
        if is_location_suppressed(point.get("status")):
            suppressed += 1
            continue
        visible.append(point)
    return visible, suppressed, materialized


async def _fetch_registry_physical_points(
    entity_ids: list[str],
    existing_keys: set[str],
    from_date: datetime | None,
    to_date: datetime | None,
) -> list[dict]:
    """Fetch analyzer-owned location rows not already covered by collector reads."""
    pool = get_analyzer_pool()
    params: list[Any] = [entity_ids]
    filters = [
        "entity_id = ANY($1::uuid[])",
        "lat IS NOT NULL",
        "lng IS NOT NULL",
        "occurred_at IS NOT NULL",
        "status NOT IN ('rejected', 'suppressed')",
    ]
    if from_date:
        filters.append(f"occurred_at >= ${len(params) + 1}")
        params.append(from_date)
    if to_date:
        filters.append(f"occurred_at <= ${len(params) + 1}")
        params.append(to_date)
    params.append(sorted(existing_keys) or [""])
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT entity_id::text AS entity_id,
                   evidence_key::text AS evidence_key,
                   source,
                   evidence_type,
                   source_table,
                   source_record_id,
                   occurred_at,
                   lat,
                   lng,
                   label,
                   confidence,
                   status
            FROM location_evidence
            WHERE {' AND '.join(filters)}
              AND evidence_key::text <> ALL(${len(params)}::text[])
            ORDER BY occurred_at DESC NULLS LAST
            LIMIT {_MAX_PHYSICAL_POINTS_PER_ENTITY * max(1, len(entity_ids))}
        """, *params)
    return [
        _point(
            row["entity_id"],
            row["source"],
            row["source_record_id"] or row["evidence_key"],
            row["occurred_at"],
            float(row["lat"]),
            float(row["lng"]),
            row["label"],
            evidence_type=row["evidence_type"],
            source_table=row["source_table"],
            confidence=float(row["confidence"] or 0),
            evidence_key=row["evidence_key"],
            status=row["status"],
        )
        for row in rows
    ]


def _physical_intersections(
    entity_ids: list[str],
    entity_names: dict[str, str | None],
    points: list[dict],
    radius_m: float,
    window_minutes: int,
) -> list[dict]:
    by_entity: dict[str, list[dict]] = {entity_id: [] for entity_id in entity_ids}
    for point in points:
        if point["entity_id"] in by_entity:
            by_entity[point["entity_id"]].append(point)
    for values in by_entity.values():
        values.sort(key=lambda p: p["occurred_at"])

    window_seconds = window_minutes * 60
    hits: list[dict] = []
    if len(entity_ids) == 2:
        a, b = entity_ids
        b_points = by_entity.get(b, [])
        b_times = [p["occurred_at"].timestamp() for p in b_points]
        for pa in by_entity.get(a, []):
            ts = pa["occurred_at"].timestamp()
            lo = bisect.bisect_left(b_times, ts - window_seconds)
            hi = bisect.bisect_right(b_times, ts + window_seconds)
            for pb in b_points[lo:hi]:
                distance = _haversine_m(pa["lat"], pa["lng"], pb["lat"], pb["lng"])
                if distance <= radius_m:
                    hits.append(_physical_hit([pa, pb], entity_names, radius_m))
        hits.sort(key=lambda h: (h["time_gap_minutes"], h["max_distance_m"]))
        return hits[:_MAX_PHYSICAL_RESULTS]

    base_id = entity_ids[0]
    other_ids = entity_ids[1:]
    other_times = {
        entity_id: [p["occurred_at"].timestamp() for p in by_entity.get(entity_id, [])]
        for entity_id in other_ids
    }
    seen: set[tuple[str, ...]] = set()
    for base in by_entity.get(base_id, []):
        ts = base["occurred_at"].timestamp()
        evidence = [base]
        for entity_id in other_ids:
            candidates = by_entity.get(entity_id, [])
            times = other_times.get(entity_id, [])
            lo = bisect.bisect_left(times, ts - window_seconds)
            hi = bisect.bisect_right(times, ts + window_seconds)
            best: tuple[float, dict] | None = None
            for point in candidates[lo:hi]:
                distance = _haversine_m(base["lat"], base["lng"], point["lat"], point["lng"])
                if distance <= radius_m and (best is None or distance < best[0]):
                    best = (distance, point)
            if best is None:
                break
            evidence.append(best[1])
        if len(evidence) != len(entity_ids):
            continue
        key = tuple(sorted(f"{p['entity_id']}:{p['source']}:{p['record_id']}" for p in evidence))
        if key in seen:
            continue
        seen.add(key)
        hits.append(_physical_hit(evidence, entity_names, radius_m))
    hits.sort(key=lambda h: (h["time_gap_minutes"], h["max_distance_m"]))
    return hits[:_MAX_PHYSICAL_RESULTS]


async def _fetch_analyzer_digital(
    entity_ids: list[str],
    entity_names: dict[str, str | None],
    from_date: datetime | None,
    to_date: datetime | None,
) -> list[dict]:
    pool = get_analyzer_pool()
    params: list[Any] = [entity_ids]
    filters = []
    if from_date:
        filters.append(f"occurred_at >= ${len(params) + 1}")
        params.append(from_date)
    if to_date:
        filters.append(f"occurred_at <= ${len(params) + 1}")
        params.append(to_date)
    where_time = (" AND " + " AND ".join(filters)) if filters else ""

    out: list[dict] = []
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT actor_entity_id::text AS actor_id,
                   target_entity_id::text AS target_id,
                   source,
                   interaction_type,
                   count(*)::int AS count,
                   min(occurred_at) AS first_seen_at,
                   max(occurred_at) AS last_seen_at,
                   sum(weight)::int AS weight
            FROM entity_interactions
            WHERE actor_entity_id = ANY($1::uuid[])
              AND target_entity_id = ANY($1::uuid[])
              AND actor_entity_id <> target_entity_id
              {where_time}
            GROUP BY actor_entity_id, target_entity_id, source, interaction_type
            ORDER BY count DESC, last_seen_at DESC NULLS LAST
            LIMIT {_MAX_DIGITAL_RESULTS}
        """, *params)
        for row in rows:
            out.append({
                "type": "direct_interaction",
                "source": row["source"],
                "label": row["interaction_type"],
                "entities": [
                    {"id": row["actor_id"], "name": entity_names.get(row["actor_id"]), "role": "actor"},
                    {"id": row["target_id"], "name": entity_names.get(row["target_id"]), "role": "target"},
                ],
                "count": row["count"],
                "weight": row["weight"],
                "first_seen_at": _iso(row["first_seen_at"]),
                "last_seen_at": _iso(row["last_seen_at"]),
            })

        rels = await conn.fetch("""
            SELECT entity_a_id::text AS a_id,
                   entity_b_id::text AS b_id,
                   relationship_type,
                   weight,
                   sources,
                   last_seen_at
            FROM entity_relationships
            WHERE entity_a_id = ANY($1::uuid[])
              AND entity_b_id = ANY($1::uuid[])
              AND entity_a_id <> entity_b_id
            ORDER BY weight DESC
            LIMIT $2
        """, entity_ids, _MAX_DIGITAL_RESULTS)
        for row in rels:
            out.append({
                "type": "relationship",
                "source": "analyzer",
                "label": row["relationship_type"],
                "entities": [
                    {"id": row["a_id"], "name": entity_names.get(row["a_id"]), "role": "entity"},
                    {"id": row["b_id"], "name": entity_names.get(row["b_id"]), "role": "entity"},
                ],
                "count": 1,
                "weight": row["weight"],
                "last_seen_at": _iso(row["last_seen_at"]),
                "metadata": _parse_json(row["sources"]),
            })

        common_peers = await conn.fetch(f"""
            WITH edges AS (
                SELECT actor_entity_id AS subject_id,
                       target_entity_id AS peer_id,
                       interaction_type,
                       source,
                       occurred_at,
                       weight
                FROM entity_interactions
                WHERE actor_entity_id = ANY($1::uuid[])
                  AND target_entity_id <> ALL($1::uuid[])
                  {where_time}
                UNION ALL
                SELECT target_entity_id AS subject_id,
                       actor_entity_id AS peer_id,
                       interaction_type,
                       source,
                       occurred_at,
                       weight
                FROM entity_interactions
                WHERE target_entity_id = ANY($1::uuid[])
                  AND actor_entity_id <> ALL($1::uuid[])
                  {where_time}
            )
            SELECT peer_id::text,
                   count(DISTINCT subject_id)::int AS entities_present,
                   count(*)::int AS count,
                   max(occurred_at) AS last_seen_at,
                   jsonb_agg(DISTINCT source) AS sources,
                   jsonb_agg(DISTINCT interaction_type) AS interaction_types
            FROM edges
            GROUP BY peer_id
            HAVING count(DISTINCT subject_id) = cardinality($1::uuid[])
            ORDER BY count DESC, last_seen_at DESC NULLS LAST
            LIMIT 80
        """, *params)
        peer_ids = [r["peer_id"] for r in common_peers]
        peer_names = {}
        if peer_ids:
            peer_rows = await conn.fetch(
                "SELECT id::text AS id, canonical_name FROM entities WHERE id = ANY($1::uuid[])",
                peer_ids,
            )
            peer_names = {r["id"]: r["canonical_name"] for r in peer_rows}
        for row in common_peers:
            out.append({
                "type": "shared_interaction_peer",
                "source": "analyzer",
                "label": peer_names.get(row["peer_id"]) or row["peer_id"],
                "entities": [{"id": eid, "name": entity_names.get(eid), "role": "subject"} for eid in entity_ids],
                "peer": {"id": row["peer_id"], "name": peer_names.get(row["peer_id"])},
                "count": row["count"],
                "last_seen_at": _iso(row["last_seen_at"]),
                "metadata": {
                    "sources": _parse_json(row["sources"]) or [],
                    "interaction_types": _parse_json(row["interaction_types"]) or [],
                },
            })

    return out[:_MAX_DIGITAL_RESULTS]


async def _fetch_collector_digital(
    entity_ids: list[str],
    entity_names: dict[str, str | None],
    links: list[dict],
) -> tuple[list[dict], bool]:
    try:
        collector = get_collector_pool()
    except Exception as exc:  # noqa: BLE001 - collector is optional
        logger.warning("intersections digital collector read skipped: %s", exc)
        return [], True

    out: list[dict] = []
    try:
        async with collector.acquire() as conn:
            tg_pairs = [
                (str(link["platform_id"]), link["entity_id"])
                for link in links
                if link["source"] == "telegram" and link["platform_id"] and link["entity_id"] in entity_ids
            ]
            if tg_pairs:
                rows = await conn.fetch("""
                WITH wanted(platform_user_id, entity_id) AS (
                    SELECT * FROM unnest($1::text[], $2::uuid[])
                )
                SELECT c.platform_chat_id::text AS group_id,
                       c.title,
                       c.members_count,
                       count(DISTINCT w.entity_id)::int AS entities_present,
                       max(m.last_seen_at) AS last_seen_at,
                       jsonb_agg(DISTINCT w.entity_id::text) AS entity_ids
                FROM wanted w
                JOIN telegram_users u ON u.platform_user_id::text = w.platform_user_id
                JOIN telegram_chat_members m ON m.user_id = u.id
                JOIN telegram_chats c ON c.id = m.chat_id
                GROUP BY c.platform_chat_id, c.title, c.members_count
                HAVING count(DISTINCT w.entity_id) = $3
                ORDER BY COALESCE(c.members_count, 999999), last_seen_at DESC NULLS LAST
                LIMIT 80
            """, [p for p, _ in tg_pairs], [e for _, e in tg_pairs], len(entity_ids))
                for row in rows:
                    out.append({
                        "type": "shared_group",
                        "source": "telegram",
                        "label": row["title"] or row["group_id"],
                        "group_id": row["group_id"],
                        "entities": [{"id": eid, "name": entity_names.get(eid), "role": "member"} for eid in entity_ids],
                        "count": row["entities_present"],
                        "last_seen_at": _iso(row["last_seen_at"]),
                        "metadata": {"members_count": row["members_count"], "entity_ids": _parse_json(row["entity_ids"]) or []},
                    })

            wa_pairs = [
                (str(link["platform_id"]), link["entity_id"])
                for link in links
                if link["source"] == "whatsapp" and link["platform_id"] and link["entity_id"] in entity_ids
            ]
            if wa_pairs:
                rows = await conn.fetch("""
                WITH wanted(platform_user_id, entity_id) AS (
                    SELECT * FROM unnest($1::text[], $2::uuid[])
                )
                SELECT c.platform_chat_id::text AS group_id,
                       c.name,
                       c.participant_count,
                       count(DISTINCT w.entity_id)::int AS entities_present,
                       count(*)::int AS messages,
                       max(m.timestamp) AS last_seen_at,
                       jsonb_agg(DISTINCT w.entity_id::text) AS entity_ids
                FROM wanted w
                JOIN whatsapp_users u ON u.platform_user_id::text = w.platform_user_id
                JOIN whatsapp_messages m ON m.sender_id = u.id
                JOIN whatsapp_chats c ON c.id = m.chat_id
                WHERE c.is_group IS TRUE
                GROUP BY c.platform_chat_id, c.name, c.participant_count
                HAVING count(DISTINCT w.entity_id) = $3
                ORDER BY COALESCE(c.participant_count, 999999), messages DESC
                LIMIT 80
            """, [p for p, _ in wa_pairs], [e for _, e in wa_pairs], len(entity_ids))
                for row in rows:
                    out.append({
                        "type": "shared_group",
                        "source": "whatsapp",
                        "label": row["name"] or row["group_id"],
                        "group_id": row["group_id"],
                        "entities": [{"id": eid, "name": entity_names.get(eid), "role": "sender"} for eid in entity_ids],
                        "count": row["messages"],
                        "last_seen_at": _iso(row["last_seen_at"]),
                        "metadata": {"participant_count": row["participant_count"], "entity_ids": _parse_json(row["entity_ids"]) or []},
                    })

            owner_rows: list[tuple[str, str, str]] = []
            for link in links:
                if link["entity_id"] not in entity_ids:
                    continue
                for value in (link.get("platform_id"), link.get("platform_username")):
                    if value:
                        owner_rows.append((link["entity_id"], link["source"], str(value).lower()))
            if owner_rows:
                rows = await conn.fetch("""
                WITH wanted(entity_id, platform, account) AS (
                    SELECT * FROM unnest($1::uuid[], $2::text[], $3::text[])
                )
                SELECT fe.platform,
                       COALESCE(NULLIF(fe.target_username, ''), NULLIF(fe.target_uid, '')) AS target,
                       count(DISTINCT w.entity_id)::int AS entities_present,
                       max(fe.last_seen) AS last_seen_at,
                       jsonb_agg(DISTINCT w.entity_id::text) AS entity_ids
                FROM wanted w
                JOIN follow_edges fe
                  ON fe.platform = w.platform
                 AND lower(fe.owner_account) = w.account
                WHERE fe.direction = 'following'
                  AND COALESCE(NULLIF(fe.target_username, ''), NULLIF(fe.target_uid, '')) IS NOT NULL
                GROUP BY fe.platform, COALESCE(NULLIF(fe.target_username, ''), NULLIF(fe.target_uid, ''))
                HAVING count(DISTINCT w.entity_id) = $4
                ORDER BY last_seen_at DESC NULLS LAST
                LIMIT 80
            """, [r[0] for r in owner_rows], [r[1] for r in owner_rows], [r[2] for r in owner_rows], len(entity_ids))
                for row in rows:
                    out.append({
                        "type": "shared_follow_target",
                        "source": row["platform"],
                        "label": row["target"],
                        "entities": [{"id": eid, "name": entity_names.get(eid), "role": "follower"} for eid in entity_ids],
                        "count": row["entities_present"],
                        "last_seen_at": _iso(row["last_seen_at"]),
                        "metadata": {"target": row["target"], "entity_ids": _parse_json(row["entity_ids"]) or []},
                    })
    except Exception as exc:  # noqa: BLE001 - collector is optional for this API
        logger.warning("intersections digital collector read skipped: %s", exc)
        return [], True

    return out[:_MAX_DIGITAL_RESULTS], False


async def _intersect(
    entity_ids: list[str],
    radius_m: float,
    window_minutes: int,
    from_date: datetime | None,
    to_date: datetime | None,
) -> dict:
    start = time.perf_counter()
    ids = _dedupe_ids(entity_ids)
    names, links, faces = await _entity_context(ids)
    raw_points, physical_collector_skipped = await _fetch_physical_points(ids, links, from_date, to_date)
    points, suppressed_points, materialized_points = await _apply_location_registry(raw_points)
    registry_points = await _fetch_registry_physical_points(
        ids,
        {point.get("evidence_key") for point in points if point.get("evidence_key")},
        from_date,
        to_date,
    )
    points.extend(registry_points)
    physical = _physical_intersections(ids, names, points, radius_m, window_minutes)
    digital = await _fetch_analyzer_digital(ids, names, from_date, to_date)
    collector_digital, digital_collector_skipped = await _fetch_collector_digital(ids, names, links)
    digital.extend(collector_digital)
    digital.sort(key=lambda row: (-(row.get("count") or 0), row.get("label") or ""))
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    return {
        "entity_ids": ids,
        "entities": [
            {"id": entity_id, "name": names.get(entity_id), "face": faces.get(entity_id)}
            for entity_id in ids
        ],
        "params": {
            "radius_m": radius_m,
            "window_minutes": window_minutes,
            "from": _iso(from_date),
            "to": _iso(to_date),
        },
        "physical": physical,
        "digital": digital[:_MAX_DIGITAL_RESULTS],
        "counts": {
            "physical": len(physical),
            "digital": min(len(digital), _MAX_DIGITAL_RESULTS),
            "physical_points_considered": len(points),
            "physical_points_raw": len(raw_points),
            "physical_points_suppressed": suppressed_points,
            "physical_points_materialized": materialized_points,
            "physical_points_from_registry": len(registry_points),
        },
        "collector_skipped": physical_collector_skipped or digital_collector_skipped,
        "duration_ms": duration_ms,
    }


@router.get("/entities/{entity_a}/intersect/{entity_b}")
async def intersect_pair(
    entity_a: str,
    entity_b: str,
    radius_m: float = Query(200.0, ge=10.0, le=5000.0),
    window_minutes: int = Query(60, ge=1, le=1440),
    from_date: datetime | None = Query(None, alias="from"),
    to_date: datetime | None = Query(None, alias="to"),
):
    return await _intersect([entity_a, entity_b], radius_m, window_minutes, from_date, to_date)


@router.post("/entities/intersect")
async def intersect_many(req: IntersectRequest):
    return await _intersect(req.ids, req.radius_m, req.window_minutes, req.from_date, req.to_date)
