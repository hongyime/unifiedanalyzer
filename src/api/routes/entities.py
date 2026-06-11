import json
from fastapi import APIRouter, Query, HTTPException

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["entities"])


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
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


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

    return {
        "id": str(entity["id"]),
        "tier": entity["tier"],
        "canonical_name": entity["canonical_name"],
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
