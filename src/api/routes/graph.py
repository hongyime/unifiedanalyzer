import logging
import re
from datetime import datetime

from fastapi import APIRouter, Query

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.api.face_lookup import representative_faces, face_crop_url
from src.api.routes.uuid_validation import require_uuid
from src.merge_candidates import merge_candidate_min_weight
from src.pipeline.location_evidence import (
    attach_location_evidence_key,
    fetch_location_evidence_statuses,
    is_location_suppressed,
    upsert_location_evidence_batch,
)

router = APIRouter(tags=["graph"])
logger = logging.getLogger(__name__)
_WORD_RE = re.compile(r"[A-Za-z]")


def _relationship_why(relationship_type: str, sources) -> str | None:
    if isinstance(sources, str):
        try:
            import json as _json
            sources = _json.loads(sources)
        except Exception:
            return None
    if not isinstance(sources, dict):
        return None

    explicit = sources.get("why")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    if relationship_type == "interaction":
        by_type = sources.get("by_type")
        if isinstance(by_type, dict) and by_type:
            pairs = sorted(by_type.items(), key=lambda item: (-int(item[1]), item[0]))
            return "Directed interactions: " + ", ".join(f"{k} {v}" for k, v in pairs[:4])
    if relationship_type == "social_graph_overlap":
        shared = sources.get("shared")
        jaccard = sources.get("jaccard")
        if shared is not None and jaccard is not None:
            return f"Shared-neighbour overlap: {shared} shared neighbours, jaccard {jaccard}."
    if relationship_type in {"telegram_group_co_member", "whatsapp_group_co_member"}:
        groups = sources.get("groups")
        if isinstance(groups, list) and groups:
            preview = ", ".join(str(g) for g in groups[:3])
            more = "" if len(groups) <= 3 else f" (+{len(groups) - 3} more)"
            return f"Shared group membership: {preview}{more}."
    if relationship_type == "temporal_hour_similarity":
        similarity = sources.get("similarity")
        if similarity is not None:
            return f"Hourly activity pattern similarity {similarity}."
    if relationship_type == "temporal_copost":
        events = sources.get("coincident_events")
        days = sources.get("copost_days")
        if events is not None and days is not None:
            return f"Repeated posting-time overlap: {events} close events across {days} days. Context only; not same-person evidence."
        return "Repeated posting-time overlap. Context only; not same-person evidence."
    if relationship_type == "same_person_probability":
        score = sources.get("score")
        signals = sources.get("contributing_signals")
        if score is not None and isinstance(signals, list):
            labels = [str(s.get("type")) for s in signals[:3] if isinstance(s, dict) and s.get("type")]
            return f"Same-person probability {score} from {', '.join(labels) or 'multiple signals'}."
    return None


def _decode_polyline(s: str) -> list[list[float]]:
    """Decode a Google encoded polyline (strava summary_polyline) -> [[lat,lng]]."""
    points: list[list[float]] = []
    index = lat = lng = 0
    n = len(s)
    while index < n:
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(s[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lat:
                lat += d
            else:
                lng += d
        points.append([lat / 1e5, lng / 1e5])
    return points


def _parse_latlng(s) -> list[float] | None:
    if not s:
        return None
    try:
        parts = str(s).strip("[]() ").split(",")
        return [float(parts[0]), float(parts[1])]
    except (ValueError, IndexError):
        return None


def _caption_mentions_place(caption: str | None, place_name: str | None) -> bool:
    if not caption or not place_name:
        return False
    place = str(place_name).strip()
    if len(place) < 4 or not _WORD_RE.search(place):
        return False
    caption_text = str(caption)
    if place.lower() not in caption_text.lower():
        return False
    pattern = re.compile(rf"(?<!\w){re.escape(place)}(?!\w)", re.IGNORECASE)
    return bool(pattern.search(caption_text))


def _as_latlng_list(raw) -> list[list[float]]:
    """strava_gps_streams.latlng -> [[lat,lng],...]. asyncpg may hand back jsonb
    as a str; normalize."""
    import json as _json
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for p in raw:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append([float(p[0]), float(p[1])])
    return out


def _downsample(pts: list, target: int = 120) -> list:
    """Thin a track to ~target points to keep the payload small (full-res Strava
    streams can be thousands of points)."""
    if len(pts) <= target:
        return pts
    step = len(pts) / target
    return [pts[int(i * step)] for i in range(target)] + [pts[-1]]


def _geo_event(item: dict, kind: str) -> dict:
    occurred_at = item.get("occurred_at") or item.get("date")
    points = item.get("points") if isinstance(item.get("points"), list) else []
    start = points[0] if points else None
    end = points[-1] if points else None
    return {
        "kind": kind,
        "evidence_key": item.get("evidence_key"),
        "source": item.get("source"),
        "evidence_type": item.get("evidence_type"),
        "label": item.get("label") or item.get("name"),
        "occurred_at": occurred_at,
        "lat": item.get("lat") if kind == "point" else (start[0] if start else None),
        "lng": item.get("lng") if kind == "point" else (start[1] if start else None),
        "end_lat": end[0] if end else None,
        "end_lng": end[1] if end else None,
        "confidence": item.get("confidence"),
        "status": item.get("status"),
        "source_table": item.get("source_table"),
        "source_record_id": item.get("source_record_id"),
    }


def _geo_events(routes: list[dict], points: list[dict], limit: int = 250) -> list[dict]:
    events = [_geo_event(item, "route") for item in routes]
    events.extend(_geo_event(item, "point") for item in points)
    events.sort(key=lambda event: event.get("occurred_at") or "", reverse=True)
    return events[:limit]


@router.get("/entities/{entity_id}/geo")
async def entity_geo(
    entity_id: str,
    from_date: datetime | None = Query(None, alias="from"),
    to_date: datetime | None = Query(None, alias="to"),
):
    """Geo footprint for the map: Strava route polylines + start points, and
    Instagram tagged-place pins. Reads the collector DB."""
    entity_id = require_uuid(entity_id)
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT source, platform_id FROM entity_platform_links WHERE entity_id = $1::uuid", entity_id
        )
    try:
        collector = get_collector_pool()
    except Exception as e:  # noqa: BLE001 - geo source data is collector-owned
        logger.warning("entity_geo collector read skipped: %s", e)
        return {"routes": [], "points": [], "counts": {"routes": 0, "points": 0}, "collector_skipped": True}
    strava_ids = []
    for link in links:
        if link["source"] != "strava" or not link["platform_id"]:
            continue
        try:
            strava_ids.append(int(link["platform_id"]))
        except (TypeError, ValueError):
            continue
    ig_ids = [link["platform_id"] for link in links if link["source"] == "instagram" and link["platform_id"]]
    telegram_ids = [link["platform_id"] for link in links if link["source"] == "telegram" and link["platform_id"]]
    whatsapp_ids = [link["platform_id"] for link in links if link["source"] == "whatsapp" and link["platform_id"]]

    routes: list[dict] = []
    points: list[dict] = []
    ig_place_names: list[str] = []
    ig_caption_rows: list[dict] = []
    async with collector.acquire() as cc:
        if strava_ids:
            strava_filters = ["a.athlete_id = ANY(SELECT id FROM target_athletes)"]
            strava_params: list = [strava_ids]
            if from_date:
                strava_filters.append(f"a.start_date >= ${len(strava_params) + 1}")
                strava_params.append(from_date)
            if to_date:
                strava_filters.append(f"a.start_date <= ${len(strava_params) + 1}")
                strava_params.append(to_date)
            # Prefer full-res gps_streams.latlng; fall back to the (now
            # backfilled) summary_polyline so activities with only the overview
            # line still render.
            acts = await cc.fetch(f"""
                WITH target_athletes AS (
                    SELECT id
                    FROM strava_athletes
                    WHERE platform_athlete_id = ANY($1::bigint[])
                )
                SELECT a.platform_activity_id, a.name, a.start_date, a.type, a.start_latlng, a.summary_polyline,
                       s.latlng::text AS latlng
                FROM strava_activities a
                LEFT JOIN strava_gps_streams s ON s.activity_id = a.id
                WHERE {' AND '.join(strava_filters)}
                  AND (s.latlng IS NOT NULL
                       OR (a.summary_polyline IS NOT NULL AND a.summary_polyline <> ''))
                ORDER BY a.start_date DESC NULLS LAST LIMIT 100
            """, *strava_params)
            for r in acts:
                track = _as_latlng_list(r["latlng"]) if r["latlng"] else _decode_polyline(r["summary_polyline"] or "")
                pts = _downsample(track)
                if len(pts) >= 2:
                    routes.append({"name": r["name"], "type": r["type"],
                                   "date": r["start_date"].isoformat() if r["start_date"] else None,
                                   "source": "strava",
                                   "evidence_type": "route_polyline",
                                   "confidence": 0.9 if r["latlng"] else 0.75,
                                   "source_table": "strava_activities",
                                   "source_record_id": str(r["platform_activity_id"]),
                                   "points": pts})
                sp = _parse_latlng(r["start_latlng"])
                if sp:
                    points.append({
                        "lat": sp[0],
                        "lng": sp[1],
                        "label": r["name"],
                        "source": "strava",
                        "evidence_type": "gps_start",
                        "confidence": 0.7,
                        "source_table": "strava_activities",
                        "source_record_id": str(r["platform_activity_id"]),
                        "occurred_at": r["start_date"].isoformat() if r["start_date"] else None,
                    })
        if ig_ids:
            ig_filters = ["pr.platform_user_id::text = ANY($1::text[])", "p.location_lat IS NOT NULL"]
            ig_params: list = [ig_ids]
            if from_date:
                ig_filters.append(f"p.platform_created_at >= ${len(ig_params) + 1}")
                ig_params.append(from_date)
            if to_date:
                ig_filters.append(f"p.platform_created_at <= ${len(ig_params) + 1}")
                ig_params.append(to_date)
            posts = await cc.fetch(f"""
                SELECT p.platform_post_id, p.location_name, p.location_lat, p.location_lng, p.platform_created_at
                FROM instagram_posts p
                JOIN instagram_profiles pr ON pr.id = p.profile_id
                WHERE {' AND '.join(ig_filters)}
                ORDER BY p.platform_created_at DESC NULLS LAST LIMIT 300
            """, *ig_params)
            for p in posts:
                points.append({
                    "lat": float(p["location_lat"]),
                    "lng": float(p["location_lng"]),
                    "label": p["location_name"],
                    "source": "instagram",
                    "evidence_type": "venue_tag",
                    "confidence": 0.75,
                    "source_table": "instagram_posts",
                    "source_record_id": str(p["platform_post_id"]),
                    "occurred_at": p["platform_created_at"].isoformat() if p["platform_created_at"] else None,
                })
            # IG stores only place NAMES (no coords); collect them to resolve via
            # the geocode cache below.
            named_filters = ["pr.platform_user_id::text = ANY($1::text[])", "p.location_name IS NOT NULL", "p.location_name <> ''"]
            named_params: list = [ig_ids]
            if from_date:
                named_filters.append(f"p.platform_created_at >= ${len(named_params) + 1}")
                named_params.append(from_date)
            if to_date:
                named_filters.append(f"p.platform_created_at <= ${len(named_params) + 1}")
                named_params.append(to_date)
            named = await cc.fetch(f"""
                SELECT DISTINCT p.location_name
                FROM instagram_posts p
                JOIN instagram_profiles pr ON pr.id = p.profile_id
                WHERE {' AND '.join(named_filters)}
                LIMIT 500
            """, *named_params)
            ig_place_names = [r["location_name"] for r in named]

            caption_filters = ["pr.platform_user_id::text = ANY($1::text[])", "p.caption IS NOT NULL", "p.caption <> ''"]
            caption_params: list = [ig_ids]
            if from_date:
                caption_filters.append(f"p.platform_created_at >= ${len(caption_params) + 1}")
                caption_params.append(from_date)
            if to_date:
                caption_filters.append(f"p.platform_created_at <= ${len(caption_params) + 1}")
                caption_params.append(to_date)
            captions = await cc.fetch(f"""
                SELECT p.platform_post_id, p.caption, p.platform_created_at
                FROM instagram_posts p
                JOIN instagram_profiles pr ON pr.id = p.profile_id
                WHERE {' AND '.join(caption_filters)}
                ORDER BY p.platform_created_at DESC NULLS LAST
                LIMIT 300
            """, *caption_params)
            ig_caption_rows = [dict(row) for row in captions]
        if telegram_ids:
            tg_filters = ["u.platform_user_id = ANY($1::text[])"]
            tg_params: list = [telegram_ids]
            if from_date:
                tg_filters.append(f"m.platform_created_at >= ${len(tg_params) + 1}")
                tg_params.append(from_date)
            if to_date:
                tg_filters.append(f"m.platform_created_at <= ${len(tg_params) + 1}")
                tg_params.append(to_date)
            loc_rows = await cc.fetch(f"""
                SELECT m.platform_message_id, l.latitude, l.longitude,
                       COALESCE(NULLIF(l.venue_title, ''), NULLIF(l.venue_address, ''), LEFT(COALESCE(m.text, m.caption, ''), 120)) AS label,
                       m.platform_created_at
                FROM telegram_message_locations l
                JOIN telegram_messages m ON m.platform_message_id = l.platform_message_id
                JOIN telegram_users u ON u.id = m.sender_id
                WHERE {' AND '.join(tg_filters)}
                ORDER BY m.platform_created_at DESC NULLS LAST
                LIMIT 200
            """, *tg_params)
            for row in loc_rows:
                points.append({
                    "lat": float(row["latitude"]),
                    "lng": float(row["longitude"]),
                    "label": row["label"],
                    "source": "telegram",
                    "evidence_type": "message_location",
                    "confidence": 0.9,
                    "source_table": "telegram_message_locations",
                    "source_record_id": str(row["platform_message_id"]),
                    "occurred_at": row["platform_created_at"].isoformat() if row["platform_created_at"] else None,
                })
        if whatsapp_ids:
            wa_filters = ["u.platform_user_id = ANY($1::text[])"]
            wa_params: list = [whatsapp_ids]
            if from_date:
                wa_filters.append(f"m.timestamp >= ${len(wa_params) + 1}")
                wa_params.append(from_date)
            if to_date:
                wa_filters.append(f"m.timestamp <= ${len(wa_params) + 1}")
                wa_params.append(to_date)
            wa_rows = await cc.fetch(f"""
                SELECT m.platform_message_id, l.latitude, l.longitude,
                       COALESCE(NULLIF(l.name, ''), NULLIF(l.address, ''), LEFT(COALESCE(m.text, ''), 120)) AS label,
                       m.timestamp
                FROM whatsapp_message_locations l
                JOIN whatsapp_messages m ON m.platform_message_id = l.platform_message_id
                JOIN whatsapp_users u ON u.id = m.sender_id
                WHERE {' AND '.join(wa_filters)}
                ORDER BY m.timestamp DESC NULLS LAST
                LIMIT 200
            """, *wa_params)
            for row in wa_rows:
                points.append({
                    "lat": float(row["latitude"]),
                    "lng": float(row["longitude"]),
                    "label": row["label"],
                    "source": "whatsapp",
                    "evidence_type": "message_location",
                    "confidence": 0.9,
                    "source_table": "whatsapp_message_locations",
                    "source_record_id": str(row["platform_message_id"]),
                    "occurred_at": row["timestamp"].isoformat() if row["timestamp"] else None,
                })

    # Geocoded IG place-name pins (cache lives in the analyzer DB).
    if ig_place_names:
        async with analyzer.acquire() as conn:
            geo = await conn.fetch(
                "SELECT place_name, lat, lng FROM geocode_cache "
                "WHERE status = 'ok' AND place_name = ANY($1::text[])", ig_place_names)
        for g in geo:
            points.append({
                "lat": g["lat"],
                "lng": g["lng"],
                "label": g["place_name"],
                "source": "instagram",
                "evidence_type": "venue_geocode",
                "confidence": 0.55,
                "source_table": "geocode_cache",
                "source_record_id": g["place_name"],
                "occurred_at": None,
            })

    # Caption-derived pins are deliberately conservative: only exact mentions
    # of places already resolved in geocode_cache become low-confidence points.
    if ig_caption_rows:
        async with analyzer.acquire() as conn:
            caption_places = await conn.fetch(
                """
                SELECT place_name, lat, lng
                FROM geocode_cache
                WHERE status = 'ok'
                  AND char_length(place_name) >= 4
                LIMIT 2000
                """
            )
        emitted = 0
        for row in ig_caption_rows:
            caption = row.get("caption")
            for place in caption_places:
                if not _caption_mentions_place(caption, place["place_name"]):
                    continue
                points.append({
                    "lat": place["lat"],
                    "lng": place["lng"],
                    "label": place["place_name"],
                    "source": "instagram",
                    "evidence_type": "caption_derived",
                    "confidence": 0.35,
                    "source_table": "instagram_posts",
                    "source_record_id": str(row["platform_post_id"]),
                    "occurred_at": row["platform_created_at"].isoformat() if row["platform_created_at"] else None,
                    "kind": "caption_derived",
                    "payload": {
                        "derivation": "caption_exact_geocache_match",
                        "matched_place": place["place_name"],
                        "caption_preview": str(caption or "")[:240],
                    },
                })
                emitted += 1
                if emitted >= 100:
                    break
            if emitted >= 100:
                break

    # Drive-original EXIF GPS is analyzer-owned and attributed through the face
    # bridge. Keep it in the same map payload as collector-sourced evidence.
    async with analyzer.acquire() as conn:
        try:
            drive_rows = await conn.fetch("""
                SELECT DISTINCT ma.media_item_id, ma.gps_lat, ma.gps_lon, ma.taken_at
                FROM media_analysis ma
                JOIN facetracker.images i ON i.file_hash = ma.media_item_id
                JOIN facetracker.faces f ON f.image_id = i.id
                JOIN public.entity_faces ef ON ef.face_id = f.id
                WHERE ef.entity_id = $1::uuid
                  AND ma.analysis_type = 'exif_gps'
                  AND ma.gps_lat IS NOT NULL
                  AND ma.gps_lon IS NOT NULL
                ORDER BY ma.taken_at DESC NULLS LAST
                LIMIT 300
            """, entity_id)
        except Exception as exc:  # noqa: BLE001 - facetracker may be unavailable in tests/scratch DBs
            logger.debug("entity_geo drive EXIF read skipped: %s", exc)
            drive_rows = []
    for row in drive_rows:
        points.append({
            "lat": float(row["gps_lat"]),
            "lng": float(row["gps_lon"]),
            "label": row["media_item_id"],
            "source": "drive",
            "evidence_type": "exif_gps",
            "confidence": 0.85,
            "source_table": "media_analysis",
            "source_record_id": str(row["media_item_id"]),
            "occurred_at": row["taken_at"].isoformat() if row["taken_at"] else None,
        })

    routes = [attach_location_evidence_key(entity_id, item) for item in routes]
    points = [attach_location_evidence_key(entity_id, item) for item in points]
    evidence_keys = [item["evidence_key"] for item in [*routes, *points] if item.get("evidence_key")]
    async with analyzer.acquire() as conn:
        await upsert_location_evidence_batch(conn, entity_id, [*routes, *points])
        statuses = await fetch_location_evidence_statuses(conn, evidence_keys)

    suppressed = 0
    visible_routes: list[dict] = []
    for item in routes:
        status = statuses.get(item.get("evidence_key"), {})
        item.update(status)
        if is_location_suppressed(item.get("status")):
            suppressed += 1
            continue
        visible_routes.append(item)
    visible_points: list[dict] = []
    for item in points:
        status = statuses.get(item.get("evidence_key"), {})
        item.update(status)
        if is_location_suppressed(item.get("status")):
            suppressed += 1
            continue
        visible_points.append(item)
    routes = visible_routes
    points = visible_points

    evidence_counts: dict[str, int] = {}
    for item in [*routes, *points]:
        key = str(item.get("evidence_type") or "unknown")
        evidence_counts[key] = evidence_counts.get(key, 0) + 1
    events = _geo_events(routes, points)
    return {"routes": routes, "points": points,
            "events": events,
            "counts": {"routes": len(routes), "points": len(points),
                       "events": len(events), "evidence_types": evidence_counts, "suppressed": suppressed}}


@router.get("/entities/{entity_id}/geo/quality")
async def entity_geo_quality(entity_id: str):
    entity_id = require_uuid(entity_id)
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT source,
                   COALESCE(evidence_type, 'unknown') AS evidence_type,
                   COALESCE(status, 'active') AS status,
                   count(*)::int AS n,
                   round(avg(confidence)::numeric, 3) AS avg_confidence,
                   min(occurred_at) AS first_seen,
                   max(occurred_at) AS last_seen
            FROM location_evidence
            WHERE entity_id = $1::uuid
            GROUP BY source, COALESCE(evidence_type, 'unknown'), COALESCE(status, 'active')
            ORDER BY n DESC, source, evidence_type
        """, entity_id)
        weak = await conn.fetch("""
            SELECT evidence_key, source, evidence_type, status, label,
                   confidence, occurred_at, source_table, source_record_id
            FROM location_evidence
            WHERE entity_id = $1::uuid
              AND (COALESCE(confidence, 0) < 0.45 OR COALESCE(status, 'active') <> 'active')
            ORDER BY occurred_at DESC NULLS LAST
            LIMIT 20
        """, entity_id)
    return {
        "entity_id": entity_id,
        "groups": [
            {
                "source": r["source"],
                "evidence_type": r["evidence_type"],
                "status": r["status"],
                "count": r["n"],
                "avg_confidence": float(r["avg_confidence"]) if r["avg_confidence"] is not None else None,
                "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
                "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
            }
            for r in rows
        ],
        "weak_samples": [
            {
                "evidence_key": r["evidence_key"],
                "source": r["source"],
                "evidence_type": r["evidence_type"],
                "status": r["status"],
                "label": r["label"],
                "confidence": r["confidence"],
                "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
                "source_table": r["source_table"],
                "source_record_id": r["source_record_id"],
            }
            for r in weak
        ],
    }


@router.get("/entities/{entity_id}/chat/summary")
async def entity_chat_summary(entity_id: str):
    entity_id = require_uuid(entity_id)
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT count(*)::int AS threads,
                   COALESCE(sum(message_count), 0)::int AS messages,
                   COALESCE(sum(reply_count), 0)::int AS replies,
                   COALESCE(sum(reaction_count), 0)::int AS reactions,
                   max(last_message_at) AS last_message_at
            FROM conversation_threads
            WHERE entity_id = $1::uuid OR peer_entity_id = $1::uuid
        """, entity_id)
    return {
        "entity_id": entity_id,
        "threads": row["threads"] if row else 0,
        "messages": row["messages"] if row else 0,
        "replies": row["replies"] if row else 0,
        "reactions": row["reactions"] if row else 0,
        "last_message_at": row["last_message_at"].isoformat() if row and row["last_message_at"] else None,
        "context_only": True,
    }


@router.get("/entities/{entity_id}/chat/threads")
async def entity_chat_threads(entity_id: str, limit: int = Query(25, ge=1, le=100)):
    entity_id = require_uuid(entity_id)
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT thread_id, source, entity_id::text, peer_entity_id::text,
                   title, started_at, last_message_at, message_count, reply_count,
                   reaction_count, forwarded_count, avg_response_seconds,
                   sentiment_summary, preview
            FROM conversation_threads
            WHERE entity_id = $1::uuid OR peer_entity_id = $1::uuid
            ORDER BY last_message_at DESC NULLS LAST
            LIMIT $2
        """, entity_id, limit)
    return {"entity_id": entity_id, "threads": [_conversation_thread_payload(r) for r in rows]}


@router.get("/chat/threads/{thread_id:path}")
async def chat_thread_detail(thread_id: str):
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT thread_id, source, entity_id::text, peer_entity_id::text,
                   title, started_at, last_message_at, message_count, reply_count,
                   reaction_count, forwarded_count, avg_response_seconds,
                   sentiment_summary, preview
            FROM conversation_threads
            WHERE thread_id = $1
        """, thread_id)
        metrics = await conn.fetch("""
            SELECT cpm.entity_id::text, e.canonical_name, cpm.source,
                   cpm.message_count, cpm.reply_count, cpm.reaction_count,
                   cpm.avg_response_seconds, cpm.sentiment_summary
            FROM conversation_participant_metrics cpm
            LEFT JOIN entities e ON e.id = cpm.entity_id
            WHERE cpm.thread_id = $1
            ORDER BY cpm.message_count DESC
        """, thread_id)
    if not row:
        return {"thread": None, "participants": []}
    return {
        "thread": _conversation_thread_payload(row),
        "participants": [
            {
                "entity_id": r["entity_id"],
                "entity_name": r["canonical_name"],
                "source": r["source"],
                "message_count": r["message_count"],
                "reply_count": r["reply_count"],
                "reaction_count": r["reaction_count"],
                "avg_response_seconds": r["avg_response_seconds"],
                "sentiment_summary": r["sentiment_summary"] if isinstance(r["sentiment_summary"], dict) else {},
            }
            for r in metrics
        ],
    }


def _conversation_thread_payload(row) -> dict:
    return {
        "thread_id": row["thread_id"],
        "source": row["source"],
        "entity_id": row["entity_id"],
        "peer_entity_id": row["peer_entity_id"],
        "title": row["title"],
        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
        "last_message_at": row["last_message_at"].isoformat() if row["last_message_at"] else None,
        "message_count": row["message_count"],
        "reply_count": row["reply_count"],
        "reaction_count": row["reaction_count"],
        "forwarded_count": row["forwarded_count"],
        "avg_response_seconds": row["avg_response_seconds"],
        "sentiment_summary": row["sentiment_summary"] if isinstance(row["sentiment_summary"], dict) else {},
        "preview": row["preview"] if isinstance(row["preview"], list) else [],
    }


@router.get("/entities/{entity_id}/associates")
async def entity_associates(entity_id: str, limit: int = Query(40, ge=1, le=100)):
    """"Seen with" co-presence from Instagram tagged photos
    (media_items kind='tagged'). content_id = tagged_<mediaPk>_<ownerPk> links a
    tagged person (entity_name) to the poster (ownerPk). For this entity the
    associates are: the posters who tagged them (entity is the tagged person) +
    the people they tagged (entity is the poster). Resolved to analyzer entities
    where possible; otherwise social_users supplies a name/photo."""
    entity_id = require_uuid(entity_id)
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT platform_id, platform_username FROM entity_platform_links "
            "WHERE entity_id = $1::uuid AND source = 'instagram'", entity_id
        )
    try:
        collector = get_collector_pool()
    except Exception as e:  # noqa: BLE001 - associates are collector-sourced
        logger.warning("entity_associates collector read skipped: %s", e)
        return {"associates": [], "collector_skipped": True}
    ig_ids = [link["platform_id"] for link in links if link["platform_id"]]
    ig_users = [link["platform_username"] for link in links if link["platform_username"]]
    if not ig_ids and not ig_users:
        return {"associates": []}

    counts: dict = {}  # ("id", ownerPk) | ("user", username) -> shared media count
    async with collector.acquire() as cc:
        if ig_users:
            rowsA = await cc.fetch("""
                SELECT split_part(content_id,'_',3) AS owner,
                       count(DISTINCT split_part(content_id,'_',2)) AS shared
                FROM media_items
                WHERE source='instagram' AND kind='tagged'
                  AND entity_name = ANY($1::text[]) AND split_part(content_id,'_',3) <> ''
                GROUP BY 1
            """, ig_users)
            for r in rowsA:
                counts[("id", r["owner"])] = counts.get(("id", r["owner"]), 0) + r["shared"]
        if ig_ids:
            rowsB = await cc.fetch("""
                SELECT entity_name AS tagged,
                       count(DISTINCT split_part(content_id,'_',2)) AS shared
                FROM media_items
                WHERE source='instagram' AND kind='tagged'
                  AND split_part(content_id,'_',3) = ANY($1::text[]) AND entity_name IS NOT NULL
                GROUP BY 1
            """, ig_ids)
            for r in rowsB:
                counts[("user", r["tagged"])] = counts.get(("user", r["tagged"]), 0) + r["shared"]

        owner_ids = [k[1] for k in counts if k[0] == "id"]
        direct_users = [k[1] for k in counts if k[0] == "user"]
        su_by_id: dict = {}
        su_by_user: dict = {}
        if owner_ids:
            rows = await cc.fetch(
                "SELECT platform_user_id, username, display_name, profile_photo_url "
                "FROM social_users WHERE platform='instagram' AND platform_user_id = ANY($1::text[])", owner_ids)
            su_by_id = {r["platform_user_id"]: r for r in rows}
        if direct_users:
            rows = await cc.fetch(
                "SELECT username, display_name, profile_photo_url "
                "FROM social_users WHERE platform='instagram' AND username = ANY($1::text[])", direct_users)
            su_by_user = {r["username"]: r for r in rows}

    # collapse to per-username associates
    assoc: dict = {}
    for (kind, val), shared in counts.items():
        if kind == "id":
            su = su_by_id.get(val)
            if not su or not su["username"]:
                continue  # unresolvable owner id
            uname, display, photo = su["username"], su["display_name"], su["profile_photo_url"]
        else:
            uname = val
            su = su_by_user.get(val)
            display = su["display_name"] if su else None
            photo = su["profile_photo_url"] if su else None
        a = assoc.setdefault(uname, {"username": uname, "display": display, "photo": photo, "shared": 0})
        a["shared"] += shared
        a["display"] = a["display"] or display
        a["photo"] = a["photo"] or photo

    unames = list(assoc)
    ent_map: dict = {}
    rep: dict = {}
    if unames:
        async with analyzer.acquire() as conn:
            erows = await conn.fetch("""
                SELECT lower(platform_username) AS u, entity_id::text AS eid,
                       (SELECT canonical_name FROM entities e WHERE e.id = epl.entity_id) AS name
                FROM entity_platform_links epl
                WHERE source = 'instagram' AND lower(platform_username) = ANY($1::text[])
            """, [u.lower() for u in unames])
            ent_map = {r["u"]: (r["eid"], r["name"]) for r in erows}
            rep = await representative_faces(conn, [v[0] for v in ent_map.values()])

    out = []
    for a in sorted(assoc.values(), key=lambda x: -x["shared"]):
        eid, ename = ent_map.get(a["username"].lower(), (None, None))
        out.append({
            "username": a["username"], "full_name": ename or a["display"], "shared": a["shared"],
            "entity_id": eid, "entity_name": ename,
            "face": face_crop_url(rep.get(eid)) if eid else a["photo"],
        })
    return {"associates": out[:limit]}


@router.get("/entities/{entity_id}/social-circle")
async def entity_social_circle(entity_id: str, limit: int = Query(80, ge=1, le=200)):
    """Face associations from this entity's own media.

    `face_associations` stores the other detected faces in photos/videos owned by
    the entity. The frontend expects both unmatched face leads and matched known
    people, so this keeps every association and resolves a best entity match only
    when the associated face is already bridged through public.entity_faces.
    """
    entity_id = require_uuid(entity_id)
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT fa.associated_face_id,
                   fa.media_item_id,
                   fa.quality_score,
                   fa.first_seen_at,
                   match.entity_id::text AS matched_entity_id,
                   match.canonical_name AS matched_entity_name,
                   match.confidence AS matched_confidence
            FROM face_associations fa
            LEFT JOIN LATERAL (
                SELECT ef.entity_id,
                       e.canonical_name,
                       ef.confidence
                FROM public.entity_faces ef
                JOIN entities e ON e.id = ef.entity_id
                WHERE ef.face_id = fa.associated_face_id
                  AND ef.entity_id <> fa.entity_id
                ORDER BY ef.confidence DESC NULLS LAST, e.canonical_name NULLS LAST
                LIMIT 1
            ) AS match ON TRUE
            WHERE fa.entity_id = $1::uuid
            ORDER BY
                match.confidence DESC NULLS LAST,
                fa.quality_score DESC NULLS LAST,
                fa.first_seen_at DESC NULLS LAST
            LIMIT $2
        """, entity_id, limit)

    return {
        "associations": [
            {
                "associated_face_id": r["associated_face_id"],
                "face_crop_url": face_crop_url(r["associated_face_id"]),
                "media_item_id": r["media_item_id"],
                "matched_entity_id": r["matched_entity_id"],
                "matched_entity_name": r["matched_entity_name"],
                "matched_confidence": r["matched_confidence"],
                "first_seen_at": r["first_seen_at"].isoformat() if r["first_seen_at"] else None,
            }
            for r in rows
        ]
    }


@router.get("/entities/{entity_id}/network")
async def entity_network(entity_id: str, limit: int = Query(30, ge=1, le=80)):
    """Ego-graph: the entity + its strongest neighbours (edges from
    entity_relationships, any type), each with a face. The client lays this out
    radially; clicking a neighbour recenters the investigation."""
    entity_id = require_uuid(entity_id)
    pool = get_analyzer_pool()
    min_merge_weight = merge_candidate_min_weight()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.relationship_type, r.entity_a_id, r.entity_b_id, r.weight, r.sources,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            WHERE (r.entity_a_id = $1::uuid OR r.entity_b_id = $1::uuid)
              AND (
                    r.relationship_type != 'same_person_probability'
                    OR COALESCE(
                        CASE WHEN jsonb_typeof(r.sources->'score') = 'number'
                             THEN (r.sources->>'score')::float8 * 100
                        END,
                        r.weight
                    ) >= $3
                  )
            ORDER BY r.weight DESC
            LIMIT $2
        """, entity_id, limit, min_merge_weight)

        neighbors: dict[str, dict] = {}
        for r in rows:
            if str(r["entity_a_id"]) == entity_id:
                oid, oname = str(r["entity_b_id"]), r["name_b"]
            else:
                oid, oname = str(r["entity_a_id"]), r["name_a"]
            n = neighbors.setdefault(oid, {"id": oid, "name": oname, "weight": 0, "types": set(), "why": None})
            n["weight"] = max(n["weight"], r["weight"] or 0)
            n["types"].add(r["relationship_type"])
            why = _relationship_why(r["relationship_type"], r["sources"])
            if why and (n["why"] is None or (r["weight"] or 0) >= n["weight"]):
                n["why"] = why

        rep = await representative_faces(conn, list(neighbors) + [entity_id])
        center_name = await conn.fetchval("SELECT canonical_name FROM entities WHERE id=$1::uuid", entity_id)

    return {
        "center": {"id": entity_id, "name": center_name, "face": face_crop_url(rep.get(entity_id))},
        "nodes": [
            {"id": n["id"], "name": n["name"], "weight": n["weight"],
             "types": sorted(n["types"]), "face": face_crop_url(rep.get(n["id"])), "why": n["why"]}
            for n in sorted(neighbors.values(), key=lambda x: -x["weight"])
        ],
    }


@router.get("/entities/{entity_id}/relationships")
async def get_relationships(entity_id: str):
    entity_id = require_uuid(entity_id)
    pool = get_analyzer_pool()
    min_merge_weight = merge_candidate_min_weight()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.id, r.entity_a_id, r.entity_b_id, r.relationship_type,
                   r.weight, r.sources, r.last_seen_at,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            WHERE (r.entity_a_id = $1::uuid OR r.entity_b_id = $1::uuid)
              AND (
                    r.relationship_type != 'same_person_probability'
                    OR COALESCE(
                        CASE WHEN jsonb_typeof(r.sources->'score') = 'number'
                             THEN (r.sources->>'score')::float8 * 100
                        END,
                        r.weight
                    ) >= $2
                  )
            ORDER BY r.weight DESC
        """, entity_id, min_merge_weight)

    return {
        "data": [
            {
                "id": str(r["id"]),
                "other_entity_id": str(r["entity_b_id"]) if str(r["entity_a_id"]) == entity_id else str(r["entity_a_id"]),
                "other_name": r["name_b"] if str(r["entity_a_id"]) == entity_id else r["name_a"],
                "relationship_type": r["relationship_type"],
                "weight": r["weight"],
                "sources": r["sources"],
                "why": _relationship_why(r["relationship_type"], r["sources"]),
            }
            for r in rows
        ]
    }


@router.get("/entities/{entity_id}/interactions")
async def get_interactions(
    entity_id: str,
    from_date: datetime | None = Query(None, alias="from"),
    to_date: datetime | None = Query(None, alias="to"),
):
    entity_id = require_uuid(entity_id)
    pool = get_analyzer_pool()
    params: list = [entity_id]
    conditions = []
    idx = 2
    if from_date:
        conditions.append(f"occurred_at >= ${idx}")
        params.append(from_date)
        idx += 1
    if to_date:
        conditions.append(f"occurred_at <= ${idx}")
        params.append(to_date)
        idx += 1
    where = ""
    if conditions:
        where = " AND " + " AND ".join(conditions)

    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            WITH relevant AS (
                SELECT actor_entity_id, target_entity_id, interaction_type, occurred_at
                FROM entity_interactions
                WHERE actor_entity_id = $1::uuid {where}
                UNION ALL
                SELECT actor_entity_id, target_entity_id, interaction_type, occurred_at
                FROM entity_interactions
                WHERE target_entity_id = $1::uuid {where}
            )
            SELECT actor_entity_id::text AS actor_id,
                   target_entity_id::text AS target_id,
                   interaction_type,
                   COUNT(*)::int AS n,
                   MAX(occurred_at) AS last_ts
            FROM relevant
            GROUP BY actor_entity_id, target_entity_id, interaction_type
        """, *params)

        peers: dict[str, dict] = {}
        for row in rows:
            is_out = row["actor_id"] == entity_id
            peer_id = row["target_id"] if is_out else row["actor_id"]
            peer = peers.setdefault(peer_id, {
                "entity_id": peer_id,
                "out": {"total": 0, "by_type": {}, "last_ts": None},
                "in": {"total": 0, "by_type": {}, "last_ts": None},
                "last_ts": None,
            })
            bucket = peer["out"] if is_out else peer["in"]
            bucket["total"] += row["n"]
            bucket["by_type"][row["interaction_type"]] = row["n"]
            ts = row["last_ts"].isoformat() if row["last_ts"] else None
            if ts and (bucket["last_ts"] is None or ts > bucket["last_ts"]):
                bucket["last_ts"] = ts
            if ts and (peer["last_ts"] is None or ts > peer["last_ts"]):
                peer["last_ts"] = ts

        ids = list(peers)
        names = {}
        if ids:
            entity_rows = await conn.fetch(
                "SELECT id::text AS id, canonical_name FROM entities WHERE id = ANY($1::uuid[])",
                ids,
            )
            names = {row["id"]: row["canonical_name"] for row in entity_rows}
            rep = await representative_faces(conn, ids)
        else:
            rep = {}

    data = []
    for peer_id, payload in peers.items():
        payload["name"] = names.get(peer_id)
        payload["face"] = face_crop_url(rep.get(peer_id))
        payload["total"] = payload["out"]["total"] + payload["in"]["total"]
        data.append(payload)
    data.sort(key=lambda item: (-item["total"], item["name"] or item["entity_id"]))
    return {"data": data}


@router.get("/graph/overview")
async def graph_overview():
    pool = get_analyzer_pool()
    min_merge_weight = merge_candidate_min_weight()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT COUNT(*) AS total_relationships,
                   COUNT(DISTINCT entity_a_id) + COUNT(DISTINCT entity_b_id) AS entities_in_graph,
                   COUNT(*) FILTER (WHERE relationship_type = 'whatsapp_group_co_member') AS whatsapp_co_members
            FROM entity_relationships
        """)
        type_counts = await conn.fetch("""
            SELECT relationship_type, COUNT(*)::int AS n
            FROM entity_relationships
            GROUP BY relationship_type
            ORDER BY n DESC, relationship_type
        """)

        top = await conn.fetch("""
            SELECT r.entity_a_id, r.entity_b_id, r.weight, r.relationship_type, r.sources,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            WHERE (
                    r.relationship_type != 'same_person_probability'
                    OR COALESCE(
                        CASE WHEN jsonb_typeof(r.sources->'score') = 'number'
                             THEN (r.sources->>'score')::float8 * 100
                        END,
                        r.weight
                    ) >= $1
                  )
            ORDER BY r.weight DESC LIMIT 20
        """, min_merge_weight)
        bridges = await conn.fetch("""
            SELECT bp.entity_id::text AS entity_id,
                   e.canonical_name,
                   COALESCE((bp.metadata->'graph_analytics'->>'betweenness')::double precision, 0) AS betweenness,
                   COALESCE((bp.metadata->'graph_analytics'->>'degree')::int, 0) AS degree,
                   COALESCE((bp.metadata->'graph_analytics'->>'strength')::int, 0) AS strength
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE bp.metadata->'graph_analytics' IS NOT NULL
            ORDER BY betweenness DESC, strength DESC, degree DESC
            LIMIT 20
        """)

    return {
        "total_relationships": stats["total_relationships"],
        "entities_in_graph": stats["entities_in_graph"],
        "whatsapp_co_members": stats["whatsapp_co_members"],
        "relationship_type_counts": {r["relationship_type"]: r["n"] for r in type_counts},
        "top_connections": [
            {
                "entity_a": {"id": str(r["entity_a_id"]), "name": r["name_a"]},
                "entity_b": {"id": str(r["entity_b_id"]), "name": r["name_b"]},
                "weight": r["weight"],
                "type": r["relationship_type"],
                "why": _relationship_why(r["relationship_type"], r["sources"]),
            }
            for r in top
        ],
        "top_bridges": [
            {
                "entity": {"id": r["entity_id"], "name": r["canonical_name"]},
                "betweenness": r["betweenness"],
                "degree": r["degree"],
                "strength": r["strength"],
            }
            for r in bridges
        ],
    }


@router.get("/graph/communities")
async def graph_communities():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT bp.entity_id, bp.metadata->>'community_id' AS community_id,
                   e.canonical_name
            FROM behavioral_profiles bp
            JOIN entities e ON e.id = bp.entity_id
            WHERE bp.metadata->>'community_id' IS NOT NULL
        """)

    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(r["community_id"], []).append({
            "entity_id": str(r["entity_id"]),
            "canonical_name": r["canonical_name"],
        })

    communities = [
        {
            "community_id": community_id,
            "member_count": len(members),
            "members": members,
        }
        for community_id, members in grouped.items()
        if len(members) >= 2
    ]
    communities.sort(key=lambda c: c["member_count"], reverse=True)

    return {"data": communities}
