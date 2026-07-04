import json
from fastapi import APIRouter, Query, HTTPException

from src.db.connection import get_analyzer_pool
from src.api.face_lookup import representative_faces, face_crop_url

router = APIRouter(tags=["entities"])


def _decode_meta(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


SORT_COLUMNS = {
    "name": "e.canonical_name",
    "confidence": "e.confidence_score",
    "signals": "e.signal_count",
    "platforms": "platform_count",
    "created": "e.created_at",
}


@router.get("/entities")
async def list_entities(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    search: str | None = None,
    tier: str | None = None,
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
            f"SELECT COUNT(*) FROM entities e {where}", *params
        )

        params.extend([per_page, offset])
        rows = await conn.fetch(f"""
            SELECT e.id, e.tier, e.canonical_name, e.confidence_score,
                   e.signal_count, e.last_seen_at, e.created_at,
                   (SELECT COUNT(*) FROM entity_platform_links epl WHERE epl.entity_id = e.id) AS platform_count,
                   (SELECT array_agg(DISTINCT epl.source) FROM entity_platform_links epl WHERE epl.entity_id = e.id) AS platforms
            FROM entities e
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
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.entity_a_id, r.entity_b_id, r.weight, r.cross_platform, r.sources,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            JOIN entities ea ON r.entity_a_id = ea.id
            JOIN entities eb ON r.entity_b_id = eb.id
            WHERE r.relationship_type = 'same_person_probability'
            ORDER BY r.weight DESC
            LIMIT $1
        """, limit)
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


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str):
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

        _rep = await representative_faces(conn, [entity_id])

    return {
        "id": str(entity["id"]),
        "tier": entity["tier"],
        "watch_status": entity["watch_status"],
        "canonical_name": entity["canonical_name"],
        "face_crop_url": face_crop_url(_rep.get(entity_id)),
        "confidence_score": entity["confidence_score"],
        "signal_count": entity["signal_count"],
        "last_seen_at": entity["last_seen_at"].isoformat() if entity["last_seen_at"] else None,
        "primary_timezone": entity["primary_timezone"],
        "metadata": entity["metadata"],
        "created_at": entity["created_at"].isoformat() if entity["created_at"] else None,
        "platform_links": [
            {
                "id": str(l["id"]),
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
