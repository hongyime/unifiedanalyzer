"""
Phase 5C: Intelligence report endpoint.

Synthesizes everything known about an entity (identity, location,
behavior, content fingerprint, identity signals, same-person
candidates, relationships, community membership, and timeline summary)
into a single response.

Follows the jsonb-decoding pattern from behavior.py's _decode_meta —
asyncpg may return jsonb columns as str or dict depending on codec
config, so both must be handled.
"""
import json
from fastapi import APIRouter, HTTPException

from src.db.connection import get_analyzer_pool
from src.merge_candidates import merge_candidate_min_weight
from src.api.face_lookup import representative_faces, face_crop_url
from src.api.routes.uuid_validation import require_uuid

router = APIRouter(tags=["intelligence"])


def _decode_meta(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


@router.get("/entities/{entity_id}/intelligence")
async def get_intelligence(entity_id: str):
    entity_id = require_uuid(entity_id)
    pool = get_analyzer_pool()
    min_weight = merge_candidate_min_weight()
    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT id, canonical_name, tier, first_event_at, last_event_at "
            "FROM entities WHERE id = $1::uuid", entity_id
        )
        if not entity:
            raise HTTPException(404, "Entity not found")

        links = await conn.fetch("""
            SELECT source, platform_id, platform_username
            FROM entity_platform_links
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        bp = await conn.fetchrow("""
            SELECT total_events, posting_hour_dist, metadata
            FROM behavioral_profiles WHERE entity_id = $1::uuid
        """, entity_id)

        signals = await conn.fetch("""
            SELECT signal_type, target_platform, target_record_id, value, confidence
            FROM identity_signals
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        same_person_rows = await conn.fetch("""
            SELECT r.entity_a_id, r.entity_b_id, r.cross_platform, r.sources,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            WHERE r.relationship_type = 'same_person_probability'
              AND (r.entity_a_id = $1::uuid OR r.entity_b_id = $1::uuid)
              AND COALESCE(
                    CASE WHEN jsonb_typeof(r.sources->'score') = 'number'
                         THEN (r.sources->>'score')::float8 * 100
                    END,
                    r.weight
                  ) >= $2
            ORDER BY r.weight DESC
        """, entity_id, min_weight)

        relationship_rows = await conn.fetch("""
            SELECT r.relationship_type, r.entity_a_id, r.entity_b_id, r.weight, r.cross_platform,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            WHERE (r.entity_a_id = $1::uuid OR r.entity_b_id = $1::uuid)
              AND r.relationship_type != 'same_person_probability'
            ORDER BY r.weight DESC
        """, entity_id)

        # Bound to the entity's own date-range so Postgres partition-prunes the 373
        # timeline_events partitions (else this GROUP BY scans them all, ~3.4s). The
        # range IS min/max of the entity's events, so results are identical.
        if entity["first_event_at"] is not None:
            timeline_rows = await conn.fetch("""
                SELECT source, COUNT(*) AS count, MIN(occurred_at) AS first_seen, MAX(occurred_at) AS last_seen
                FROM timeline_events
                WHERE entity_id = $1::uuid
                  AND occurred_at >= $2 AND occurred_at <= $3
                GROUP BY source
            """, entity_id, entity["first_event_at"], entity["last_event_at"])
        else:
            timeline_rows = await conn.fetch("""
                SELECT source, COUNT(*) AS count, MIN(occurred_at) AS first_seen, MAX(occurred_at) AS last_seen
                FROM timeline_events
                WHERE entity_id = $1::uuid
                GROUP BY source
            """, entity_id)

    # --- behavioral profile / metadata-derived fields ---
    meta = _decode_meta(bp["metadata"]) if bp else {}
    location_inference = meta.get("location_inference")
    content_fingerprint_raw = meta.get("content_fingerprint")
    community_id = meta.get("community_id")

    location = None
    if location_inference:
        location = {
            "primary_country": location_inference.get("primary_country"),
            "primary_timezone": location_inference.get("primary_timezone"),
            "region": location_inference.get("region"),
            "source_countries": location_inference.get("source_countries"),
        }

    content_fingerprint = None
    if content_fingerprint_raw:
        content_fingerprint = {
            "vocab_size": content_fingerprint_raw.get("vocab_size"),
            "vocab_richness": content_fingerprint_raw.get("vocab_richness"),
            "top_words": content_fingerprint_raw.get("top_words"),
            "post_count": content_fingerprint_raw.get("post_count"),
        }

    behavioral_summary = None
    if bp:
        raw_hour_dist = bp["posting_hour_dist"]
        if isinstance(raw_hour_dist, str):
            try:
                raw_hour_dist = json.loads(raw_hour_dist)
            except (json.JSONDecodeError, TypeError):
                raw_hour_dist = {}
        behavioral_summary = {
            "total_events": bp["total_events"],
            "posting_hour_dist": raw_hour_dist if isinstance(raw_hour_dist, dict) else {},
        }

    # --- same-person candidates ---
    same_person_candidates = []
    for r in same_person_rows:
        if str(r["entity_a_id"]) == entity_id:
            other_id = r["entity_b_id"]
            other_name = r["name_b"]
        else:
            other_id = r["entity_a_id"]
            other_name = r["name_a"]

        sources = _decode_meta(r["sources"])
        same_person_candidates.append({
            "entity_id": str(other_id),
            "canonical_name": other_name,
            "score": sources.get("score"),
            "cross_platform": r["cross_platform"],
            "contributing_signals": sources.get("contributing_signals", []),
        })

    # Representative face thumbnails for the subject + every candidate (so the
    # Intelligence tab can show faces side by side).
    async with pool.acquire() as conn:
        _rep = await representative_faces(
            conn, [entity_id] + [c["entity_id"] for c in same_person_candidates]
        )
    subject_face_url = face_crop_url(_rep.get(entity_id))
    for c in same_person_candidates:
        c["face_crop_url"] = face_crop_url(_rep.get(c["entity_id"]))

    # --- other relationships ---
    relationships = []
    for r in relationship_rows:
        if str(r["entity_a_id"]) == entity_id:
            other_id = r["entity_b_id"]
            other_name = r["name_b"]
        else:
            other_id = r["entity_a_id"]
            other_name = r["name_a"]

        relationships.append({
            "relationship_type": r["relationship_type"],
            "other_entity_id": str(other_id),
            "other_canonical_name": other_name,
            "weight": r["weight"],
            "cross_platform": r["cross_platform"],
        })

    # --- timeline summary ---
    timeline_summary = None
    if timeline_rows:
        event_count_by_source = {r["source"]: r["count"] for r in timeline_rows}
        first_seen = min(r["first_seen"] for r in timeline_rows)
        last_seen = max(r["last_seen"] for r in timeline_rows)
        timeline_summary = {
            "first_seen": first_seen.isoformat() if first_seen else None,
            "last_seen": last_seen.isoformat() if last_seen else None,
            "event_count_by_source": event_count_by_source,
        }

    return {
        "entity": {
            "id": str(entity["id"]),
            "canonical_name": entity["canonical_name"],
            "tier": entity["tier"],
            "face_crop_url": subject_face_url,
        },
        "platforms": [
            {
                "source": l["source"],
                "platform_id": l["platform_id"],
                "platform_username": l["platform_username"],
            }
            for l in links
        ],
        "location": location,
        "behavioral_summary": behavioral_summary,
        "content_fingerprint": content_fingerprint,
        "identity_signals": [
            {
                "signal_type": s["signal_type"],
                "target_platform": s["target_platform"],
                "target_record_id": s["target_record_id"],
                "value": s["value"],
                "confidence": s["confidence"],
            }
            for s in signals
        ],
        "same_person_candidates": same_person_candidates,
        "relationships": relationships,
        "community_id": community_id,
        "timeline_summary": timeline_summary,
    }
