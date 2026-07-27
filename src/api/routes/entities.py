import json
from typing import Any

from fastapi import APIRouter, Query, HTTPException

from src.db.connection import get_analyzer_pool
from src.api.face_lookup import representative_faces, face_crop_url
from src.merge_candidates import merge_candidate_min_weight
from src.api.routes.uuid_validation import require_uuid

router = APIRouter(tags=["entities"])


def _decode_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


_DECISION_ACTION_LABELS = {
    "merge_entities": "Merge confirmed",
    "merge_confirmed": "Merge confirmed",
    "split_person": "Split person",
    "dismiss_match": "Dismissed identity candidate",
    "dismiss_identity_candidate": "Dismissed identity candidate",
    "confirm_relationship": "Relationship confirmed",
    "reject_relationship": "Relationship rejected",
    "confirm_location": "Location confirmed",
    "reject_location": "Location rejected",
    "assign_media_owner": "Media owner assigned",
    "reject_media_owner": "Media owner rejected",
    "assign_person_in_photo": "Person in photo assigned",
    "reject_person_in_photo": "Person in photo rejected",
    "assign_target_tier": "Target tier assigned",
    "add_note": "Note added",
    "adjust_source_confidence": "Source confidence adjusted",
}


def _short(value: Any, *, max_len: int = 96) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _decision_summary(action: str, payload: dict) -> str:
    if action in {"merge_confirmed", "merge_entities"}:
        merged = int(payload.get("merged_count") or len(payload.get("merged_entity_ids") or []) or 0)
        target = payload.get("target_entity_id")
        if target:
            return f"Merged {merged} into {str(target)[:8]}"
        if not payload:
            return "Merge confirmed"
        return f"Merged {merged} source entity" if merged == 1 else f"Merged {merged} source entities"
    if action == "split_person":
        links = payload.get("split_links") or payload.get("split_link_ids") or []
        new_id = payload.get("new_entity_id")
        suffix = f" into {str(new_id)[:8]}" if new_id else ""
        return f"Split {len(links)} platform link{'' if len(links) == 1 else 's'}{suffix}"
    if action in {"dismiss_identity_candidate", "dismiss_match"}:
        other = payload.get("entity_b") or payload.get("entity_a")
        return f"Marked candidate as not same{f' ({str(other)[:8]})' if other else ''}"
    if action in {"confirm_relationship", "reject_relationship"}:
        rel = payload.get("relationship_type") or "relationship"
        state = "confirmed" if action == "confirm_relationship" else "rejected"
        return f"{str(rel).replace('_', ' ')} {state}"
    if action in {"confirm_location", "reject_location"}:
        state = "confirmed" if action == "confirm_location" else "rejected"
        ref = payload.get("location_ref") if isinstance(payload.get("location_ref"), dict) else {}
        source = ref.get("source") or ref.get("type") or "location"
        return f"{str(source).replace('_', ' ')} {state}"
    if action in {"assign_media_owner", "reject_media_owner", "assign_person_in_photo", "reject_person_in_photo"}:
        role = payload.get("role") or (
            "owner" if "media_owner" in action else "person in photo"
        )
        state = "assigned" if action.startswith("assign") else "rejected"
        return f"{str(role).replace('_', ' ')} {state}"
    if action == "assign_target_tier":
        return f"Watch status set to {payload.get('watch_status') or 'default'}"
    if action == "add_note":
        note = _short(payload.get("notes"), max_len=120)
        return note or "Note updated"
    if action == "adjust_source_confidence":
        source = payload.get("source") or "source"
        confidence = payload.get("confidence")
        if confidence is not None:
            return f"{source} confidence set to {confidence}"
        return f"{source} confidence adjusted"
    return _short(action.replace("_", " "))


SORT_COLUMNS = {
    "name": "e.canonical_name",
    "confidence": "e.confidence_score",
    "signals": "e.signal_count",
    "platforms": "platform_count",
    "proximity": "pe.proximity_tier",
    "created": "e.created_at",
}

PROXIMITY_ENTITY_CTE = """
WITH proximity_rows AS (
    SELECT epl.entity_id, ap.platform, ap.account_id, ap.owner_account,
           ap.tier, ap.reasons, ap.updated_at
    FROM entity_platform_links epl
    JOIN account_proximity ap
      ON ap.platform = epl.source
     AND ap.account_id = epl.platform_id
    WHERE epl.retracted_at IS NULL
    UNION ALL
    SELECT epl.entity_id, ap.platform, ap.account_id, ap.owner_account,
           ap.tier, ap.reasons, ap.updated_at
    FROM entity_platform_links epl
    JOIN account_proximity ap
      ON ap.platform = epl.source
     AND ap.account_id = lower(epl.platform_username)
    WHERE epl.retracted_at IS NULL
      AND epl.platform_username IS NOT NULL
),
proximity_entity AS (
    SELECT entity_id, MIN(tier) AS proximity_tier
    FROM proximity_rows
    GROUP BY entity_id
)
"""


@router.get("/entities")
async def list_entities(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str | None = None,
    tier: str | None = None,
    proximity_tier: int | None = Query(None, ge=1, le=4),
    proximity_platform: str | None = None,
    platform: str | None = None,
    min_platforms: int | None = None,
    sort: str = "confidence",
    order: str = "desc",
):
    pool = get_analyzer_pool()
    offset = (page - 1) * per_page

    conditions = []
    params: list = []
    idx = 1

    if search:
        conditions.append(f"""(
            e.canonical_name ILIKE ${idx}
            OR EXISTS (
                SELECT 1 FROM entity_platform_links epl
                WHERE epl.entity_id = e.id
                AND (epl.platform_username ILIKE ${idx} OR epl.platform_name ILIKE ${idx})
            )
        )""")
        params.append(f"%{search}%")
        idx += 1

    if tier:
        conditions.append(f"e.tier = ${idx}")
        params.append(tier)
        idx += 1

    if proximity_tier is not None or proximity_platform:
        proximity_conditions = []
        if proximity_tier is not None:
            proximity_conditions.append(f"pr.tier = ${idx}")
            params.append(proximity_tier)
            idx += 1
        if proximity_platform:
            proximity_conditions.append(f"pr.platform = ${idx}")
            params.append(proximity_platform)
            idx += 1
        proximity_where = " AND ".join(proximity_conditions)
        if proximity_where:
            proximity_where = " AND " + proximity_where
        conditions.append(f"""EXISTS (
            SELECT 1
            FROM proximity_rows pr
            WHERE pr.entity_id = e.id
              {proximity_where}
        )""")

    if platform:
        conditions.append(f"""EXISTS (
            SELECT 1 FROM entity_platform_links epl
            WHERE epl.entity_id = e.id AND epl.source = ${idx}
        )""")
        params.append(platform)
        idx += 1

    if min_platforms and min_platforms > 1:
        conditions.append(f"""(
            SELECT COUNT(*) FROM entity_platform_links epl WHERE epl.entity_id = e.id
        ) >= ${idx}""")
        params.append(min_platforms)
        idx += 1

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    sort_col = SORT_COLUMNS.get(sort, "e.confidence_score")
    sort_dir = "ASC" if order.lower() == "asc" else "DESC"

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"""
            {PROXIMITY_ENTITY_CTE}
            SELECT COUNT(*)
            FROM entities e
            LEFT JOIN proximity_entity pe ON pe.entity_id = e.id
            {where}
            """,
            *params,
        )

        params.extend([per_page, offset])
        rows = await conn.fetch(f"""
            {PROXIMITY_ENTITY_CTE}
            SELECT e.id, e.tier, e.canonical_name, e.confidence_score,
                   e.signal_count, e.last_seen_at, e.created_at,
                   (SELECT COUNT(*) FROM entity_platform_links epl WHERE epl.entity_id = e.id) AS platform_count,
                   (SELECT array_agg(DISTINCT epl.source) FROM entity_platform_links epl WHERE epl.entity_id = e.id) AS platforms,
                   pe.proximity_tier
            FROM entities e
            LEFT JOIN proximity_entity pe ON pe.entity_id = e.id
            {where}
            ORDER BY {sort_col} {sort_dir} NULLS LAST, e.canonical_name
            LIMIT ${idx} OFFSET ${idx + 1}
        """, *params)

        rep = await representative_faces(conn, [str(r["id"]) for r in rows])

    return {
        "data": [
            {
                "id": str(r["id"]),
                "tier": r["tier"],
                "canonical_name": r["canonical_name"],
                "confidence_score": r["confidence_score"],
                "signal_count": r["signal_count"],
                "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
                "proximity_tier": r["proximity_tier"],
                "platform_count": r["platform_count"],
                "platforms": r["platforms"] or [],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "face_crop_url": face_crop_url(rep.get(str(r["id"]))),
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/review/candidates")
async def review_candidates(limit: int = Query(50, ge=1, le=200)):
    """Global same-person merge candidates (highest score first) with a face
    thumbnail for each side — powers the Review queue. Defined BEFORE the
    /entities/{entity_id} route so 'candidates' isn't swallowed as an id."""
    pool = get_analyzer_pool()
    min_weight = merge_candidate_min_weight()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.entity_a_id, r.entity_b_id, r.weight, r.cross_platform, r.sources,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            JOIN entities ea ON r.entity_a_id = ea.id
            JOIN entities eb ON r.entity_b_id = eb.id
            WHERE r.relationship_type = 'same_person_probability'
              AND COALESCE(
                    CASE WHEN jsonb_typeof(r.sources->'score') = 'number'
                         THEN (r.sources->>'score')::float8 * 100
                    END,
                    r.weight
                  ) >= $2
            ORDER BY r.cross_platform DESC, jsonb_array_length(r.sources->'contributing_signals') DESC, r.weight DESC
            LIMIT $1
        """, limit, min_weight)
        ids: list[str] = []
        for r in rows:
            ids.append(str(r["entity_a_id"]))
            ids.append(str(r["entity_b_id"]))
        rep = await representative_faces(conn, ids)

        # Platform handles per entity — so a name-less entity shows WHO it is
        # ("github:samho", "telegram:sam_ho") instead of a meaningless UUID, and
        # the reviewer has real accounts to compare when deciding same/different.
        handles: dict[str, list[str]] = {}
        if ids:
            link_rows = await conn.fetch("""
                SELECT entity_id::text AS eid, source,
                       -- username/name when present, else the platform_id (for
                       -- WhatsApp that's the phone/JID — the actual identifier for
                       -- an otherwise-nameless account).
                       COALESCE(NULLIF(platform_username, ''), NULLIF(platform_name, ''), platform_id) AS handle
                FROM entity_platform_links
                WHERE entity_id = ANY($1::uuid[])
                ORDER BY source
            """, list(set(ids)))
            for lr in link_rows:
                label = f"{lr['source']}:{lr['handle']}" if lr["handle"] else lr["source"]
                handles.setdefault(lr["eid"], [])
                if label not in handles[lr["eid"]]:
                    handles[lr["eid"]].append(label)

    def _display(name, eid):
        if name:
            return name
        hs = handles.get(eid, [])
        return hs[0] if hs else eid[:8]

    candidates = []
    for r in rows:
        meta = _decode_meta(r["sources"])
        a, b = str(r["entity_a_id"]), str(r["entity_b_id"])
        candidates.append({
            "entity_a": a, "name_a": r["name_a"], "display_a": _display(r["name_a"], a),
            "entity_b": b, "name_b": r["name_b"], "display_b": _display(r["name_b"], b),
            "handles_a": handles.get(a, []),
            "handles_b": handles.get(b, []),
            "score": meta.get("score"),
            "cross_platform": r["cross_platform"],
            "same_platform": (not r["cross_platform"]) or bool(meta.get("same_platform")),
            "signals": meta.get("contributing_signals", []),
            "face_a": face_crop_url(rep.get(a)),
            "face_b": face_crop_url(rep.get(b)),
        })
    return {"candidates": candidates, "total": len(candidates)}


@router.get("/search/entities")
async def search_entities(q: str = Query(..., min_length=1), limit: int = Query(12, ge=1, le=50)):
    """Jump-to-anything search across entity names + platform handles. Powers the
    Cmd-K command palette."""
    pool = get_analyzer_pool()
    like = f"%{q}%"
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT e.id, e.canonical_name, e.tier,
                   (SELECT count(*) FROM entity_platform_links epl WHERE epl.entity_id = e.id) AS platforms
            FROM entities e
            WHERE e.canonical_name ILIKE $1
               OR EXISTS (
                   SELECT 1 FROM entity_platform_links epl
                   WHERE epl.entity_id = e.id
                     AND (epl.platform_username ILIKE $1 OR epl.platform_name ILIKE $1)
               )
            ORDER BY e.confidence_score DESC NULLS LAST
            LIMIT $2
        """, like, limit)
        rep = await representative_faces(conn, [str(r["id"]) for r in rows])
    return {"results": [{
        "id": str(r["id"]),
        "canonical_name": r["canonical_name"],
        "tier": r["tier"],
        "platforms": r["platforms"],
        "face": face_crop_url(rep.get(str(r["id"]))),
    } for r in rows]}


@router.get("/entities/{entity_id}/decisions")
async def entity_decisions(entity_id: str, limit: int = Query(50, ge=1, le=200)):
    entity_id = require_uuid(entity_id)
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM entities WHERE id = $1::uuid",
            entity_id,
        )
        if not exists:
            raise HTTPException(404, "Entity not found")

        rows = await conn.fetch(
            """
            SELECT id, action, actor, entity_ids, payload, created_at,
                   decision_jsonl_path, decision_jsonl_written_at, decision_jsonl_error
            FROM audit_log
            WHERE $1::uuid = ANY(COALESCE(entity_ids, ARRAY[]::uuid[]))
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            entity_id,
            limit,
        )

        referenced_ids: set[str] = set()
        for row in rows:
            for eid in row["entity_ids"] or []:
                referenced_ids.add(str(eid))

        names = {}
        if referenced_ids:
            name_rows = await conn.fetch(
                """
                SELECT id::text AS id, canonical_name
                FROM entities
                WHERE id = ANY($1::uuid[])
                """,
                list(referenced_ids),
            )
            names = {r["id"]: r["canonical_name"] for r in name_rows}

    decisions = []
    for row in rows:
        payload = _decode_meta(row["payload"])
        action = str(row["action"] or "")
        entity_ids = [str(eid) for eid in (row["entity_ids"] or [])]
        decisions.append({
            "id": int(row["id"]),
            "action": action,
            "action_label": _DECISION_ACTION_LABELS.get(action, action.replace("_", " ").title()),
            "actor": row["actor"],
            "entity_ids": entity_ids,
            "entity_names": {eid: names.get(eid) for eid in entity_ids},
            "payload": payload,
            "summary": _decision_summary(action, payload),
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "decision_jsonl_path": row["decision_jsonl_path"],
            "decision_jsonl_written_at": (
                row["decision_jsonl_written_at"].isoformat()
                if row["decision_jsonl_written_at"] else None
            ),
            "decision_jsonl_error": row["decision_jsonl_error"],
            "durable": bool(row["decision_jsonl_written_at"]) and not row["decision_jsonl_error"],
        })

    return {
        "entity_id": entity_id,
        "decisions": decisions,
        "total": len(decisions),
    }


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str):
    entity_id = require_uuid(entity_id)
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT * FROM entities WHERE id = $1::uuid", entity_id
        )
        if not entity:
            raise HTTPException(404, "Entity not found")

        links = await conn.fetch("""
            SELECT id, source, platform_id, platform_username, platform_name,
                   confidence, link_method, is_confirmed, created_at
            FROM entity_platform_links
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        signals = await conn.fetch("""
            SELECT id, signal_type, source_platform, target_platform,
                   value, confidence, created_at
            FROM identity_signals
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
        """, entity_id)

        proximity = await conn.fetch("""
            WITH proximity_rows AS (
                SELECT epl.entity_id, ap.platform, ap.account_id, ap.owner_account,
                       ap.tier, ap.reasons, ap.updated_at
                FROM entity_platform_links epl
                JOIN account_proximity ap
                  ON ap.platform = epl.source
                 AND ap.account_id = epl.platform_id
                WHERE epl.entity_id = $1::uuid
                  AND epl.retracted_at IS NULL
                UNION ALL
                SELECT epl.entity_id, ap.platform, ap.account_id, ap.owner_account,
                       ap.tier, ap.reasons, ap.updated_at
                FROM entity_platform_links epl
                JOIN account_proximity ap
                  ON ap.platform = epl.source
                 AND ap.account_id = lower(epl.platform_username)
                WHERE epl.entity_id = $1::uuid
                  AND epl.retracted_at IS NULL
                  AND epl.platform_username IS NOT NULL
            )
            SELECT DISTINCT ap.platform, ap.account_id, ap.owner_account,
                   ap.tier, ap.reasons, ap.updated_at
            FROM proximity_rows ap
            ORDER BY ap.tier, ap.platform, ap.owner_account
        """, entity_id)

        _rep = await representative_faces(conn, [entity_id])

    return {
        "id": str(entity["id"]),
        "tier": entity["tier"],
        "watch_status": entity["watch_status"],
        "canonical_name": entity["canonical_name"],
        "face_crop_url": face_crop_url(_rep.get(entity_id)),
        "proximity_tier": min((p["tier"] for p in proximity), default=None),
        "proximity": [
            {
                "platform": p["platform"],
                "account_id": p["account_id"],
                "owner_account": p["owner_account"],
                "tier": p["tier"],
                "reasons": p["reasons"] or [],
                "updated_at": p["updated_at"].isoformat() if p["updated_at"] else None,
            }
            for p in proximity
        ],
        "confidence_score": entity["confidence_score"],
        "signal_count": entity["signal_count"],
        "last_seen_at": entity["last_seen_at"].isoformat() if entity["last_seen_at"] else None,
        "primary_timezone": entity["primary_timezone"],
        "metadata": entity["metadata"],
        "created_at": entity["created_at"].isoformat() if entity["created_at"] else None,
        "platform_links": [
            {
                "id": str(link["id"]),
                "source": link["source"],
                "platform_id": link["platform_id"],
                "platform_username": link["platform_username"],
                "platform_name": link["platform_name"],
                "confidence": link["confidence"],
                "link_method": link["link_method"],
                "is_confirmed": link["is_confirmed"],
            }
            for link in links
        ],
        "identity_signals": [
            {
                "id": str(s["id"]),
                "signal_type": s["signal_type"],
                "source_platform": s["source_platform"],
                "target_platform": s["target_platform"],
                "value": s["value"],
                "confidence": s["confidence"],
            }
            for s in signals
        ],
    }
