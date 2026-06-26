"""Triage home — the investigation workspace landing.

One endpoint feeds the three triage lanes + the coverage strip:
  - merge_candidates: high-confidence same-person pairs awaiting Same/Not-same
    (face pair + deciding signals).
  - alerts: recent change alerts rendered as evidence cards.
  - new_entities: people who just crossed a richness threshold (face + 2+
    platforms) — worth a look.
  - coverage: one-row health (entities, % with faces, % multi-platform, merge
    backlog, unread alerts).

Faces for everyone shown come from the representative-face helper.
"""
import json

from fastapi import APIRouter

from src.db.connection import get_analyzer_pool
from src.api.face_lookup import representative_faces, face_crop_url

router = APIRouter(tags=["triage"])


def _decode(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


@router.get("/triage")
async def triage(merge_limit: int = 25, alert_limit: int = 15, new_limit: int = 12):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM entities") or 0
        with_face = await conn.fetchval("SELECT count(DISTINCT entity_id) FROM entity_faces") or 0
        multi = await conn.fetchval(
            "SELECT count(*) FROM (SELECT entity_id FROM entity_platform_links "
            "GROUP BY entity_id HAVING count(*) > 1) t"
        ) or 0
        backlog = await conn.fetchval(
            "SELECT count(*) FROM entity_relationships WHERE relationship_type = 'same_person_probability'"
        ) or 0
        unread = await conn.fetchval("SELECT count(*) FROM alerts WHERE is_read = false") or 0

        cand_rows = await conn.fetch("""
            SELECT r.entity_a_id, r.entity_b_id, r.weight, r.cross_platform, r.sources,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            JOIN entities ea ON r.entity_a_id = ea.id
            JOIN entities eb ON r.entity_b_id = eb.id
            WHERE r.relationship_type = 'same_person_probability'
            ORDER BY r.weight DESC
            LIMIT $1
        """, merge_limit)

        alert_rows = await conn.fetch("""
            SELECT a.id, a.alert_type, a.severity, a.title, a.detail, a.entity_id,
                   a.detected_at, a.is_read, e.canonical_name
            FROM alerts a
            LEFT JOIN entities e ON a.entity_id = e.id
            ORDER BY a.detected_at DESC
            LIMIT $1
        """, alert_limit)

        # "High-value" = the richest recently-updated people. Threshold relaxes
        # to 2+ platforms (a face is a bonus, shown when present) because face
        # bridging is still early — requiring a face here would empty the lane.
        new_rows = await conn.fetch("""
            SELECT e.id, e.canonical_name, e.tier, e.updated_at,
                   (SELECT count(*) FROM entity_platform_links epl WHERE epl.entity_id = e.id) AS platforms
            FROM entities e
            WHERE (SELECT count(*) FROM entity_platform_links epl WHERE epl.entity_id = e.id) >= 2
            ORDER BY e.updated_at DESC NULLS LAST
            LIMIT $1
        """, new_limit)

        ids: set[str] = set()
        for r in cand_rows:
            ids.add(str(r["entity_a_id"])); ids.add(str(r["entity_b_id"]))
        for r in alert_rows:
            if r["entity_id"]:
                ids.add(str(r["entity_id"]))
        for r in new_rows:
            ids.add(str(r["id"]))
        rep = await representative_faces(conn, list(ids))

    merge_candidates = []
    for r in cand_rows:
        meta = _decode(r["sources"])
        merge_candidates.append({
            "entity_a": str(r["entity_a_id"]), "name_a": r["name_a"],
            "entity_b": str(r["entity_b_id"]), "name_b": r["name_b"],
            "score": meta.get("score"),
            "cross_platform": r["cross_platform"],
            "signals": meta.get("contributing_signals", []),
            "face_a": face_crop_url(rep.get(str(r["entity_a_id"]))),
            "face_b": face_crop_url(rep.get(str(r["entity_b_id"]))),
        })

    alerts = [{
        "id": str(r["id"]),
        "alert_type": r["alert_type"],
        "severity": r["severity"],
        "title": r["title"],
        "detail": r["detail"],
        "entity_id": str(r["entity_id"]) if r["entity_id"] else None,
        "entity_name": r["canonical_name"],
        "detected_at": r["detected_at"].isoformat() if r["detected_at"] else None,
        "is_read": r["is_read"],
        "face": face_crop_url(rep.get(str(r["entity_id"]))) if r["entity_id"] else None,
    } for r in alert_rows]

    new_entities = [{
        "id": str(r["id"]),
        "canonical_name": r["canonical_name"],
        "tier": r["tier"],
        "platforms": r["platforms"],
        "face": face_crop_url(rep.get(str(r["id"]))),
    } for r in new_rows]

    return {
        "coverage": {
            "entities": total,
            "with_faces": with_face,
            "with_faces_pct": round(100 * with_face / total) if total else 0,
            "multi_platform": multi,
            "multi_platform_pct": round(100 * multi / total) if total else 0,
            "merge_backlog": backlog,
            "unread_alerts": unread,
        },
        "merge_candidates": merge_candidates,
        "alerts": alerts,
        "new_entities": new_entities,
    }
