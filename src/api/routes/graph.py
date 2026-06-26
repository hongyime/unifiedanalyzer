from fastapi import APIRouter, Query

from src.db.connection import get_analyzer_pool
from src.api.face_lookup import representative_faces, face_crop_url

router = APIRouter(tags=["graph"])


@router.get("/entities/{entity_id}/network")
async def entity_network(entity_id: str, limit: int = Query(30, ge=1, le=80)):
    """Ego-graph: the entity + its strongest neighbours (edges from
    entity_relationships, any type), each with a face. The client lays this out
    radially; clicking a neighbour recenters the investigation."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.relationship_type, r.entity_a_id, r.entity_b_id, r.weight,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            WHERE r.entity_a_id = $1::uuid OR r.entity_b_id = $1::uuid
            ORDER BY r.weight DESC
            LIMIT $2
        """, entity_id, limit)

        neighbors: dict[str, dict] = {}
        for r in rows:
            if str(r["entity_a_id"]) == entity_id:
                oid, oname = str(r["entity_b_id"]), r["name_b"]
            else:
                oid, oname = str(r["entity_a_id"]), r["name_a"]
            n = neighbors.setdefault(oid, {"id": oid, "name": oname, "weight": 0, "types": set()})
            n["weight"] = max(n["weight"], r["weight"] or 0)
            n["types"].add(r["relationship_type"])

        rep = await representative_faces(conn, list(neighbors) + [entity_id])
        center_name = await conn.fetchval("SELECT canonical_name FROM entities WHERE id=$1::uuid", entity_id)

    return {
        "center": {"id": entity_id, "name": center_name, "face": face_crop_url(rep.get(entity_id))},
        "nodes": [
            {"id": n["id"], "name": n["name"], "weight": n["weight"],
             "types": sorted(n["types"]), "face": face_crop_url(rep.get(n["id"]))}
            for n in sorted(neighbors.values(), key=lambda x: -x["weight"])
        ],
    }


@router.get("/entities/{entity_id}/relationships")
async def get_relationships(entity_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.id, r.entity_a_id, r.entity_b_id, r.relationship_type,
                   r.weight, r.sources, r.last_seen_at,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            WHERE r.entity_a_id = $1::uuid OR r.entity_b_id = $1::uuid
            ORDER BY r.weight DESC
        """, entity_id)

    return {
        "data": [
            {
                "id": str(r["id"]),
                "other_entity_id": str(r["entity_b_id"]) if str(r["entity_a_id"]) == entity_id else str(r["entity_a_id"]),
                "other_name": r["name_b"] if str(r["entity_a_id"]) == entity_id else r["name_a"],
                "relationship_type": r["relationship_type"],
                "weight": r["weight"],
                "sources": r["sources"],
            }
            for r in rows
        ]
    }


@router.get("/graph/overview")
async def graph_overview():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT COUNT(*) AS total_relationships,
                   COUNT(DISTINCT entity_a_id) + COUNT(DISTINCT entity_b_id) AS entities_in_graph,
                   COUNT(*) FILTER (WHERE relationship_type = 'whatsapp_group_co_member') AS whatsapp_co_members
            FROM entity_relationships
        """)

        top = await conn.fetch("""
            SELECT r.entity_a_id, r.entity_b_id, r.weight, r.relationship_type,
                   ea.canonical_name AS name_a, eb.canonical_name AS name_b
            FROM entity_relationships r
            LEFT JOIN entities ea ON r.entity_a_id = ea.id
            LEFT JOIN entities eb ON r.entity_b_id = eb.id
            ORDER BY r.weight DESC LIMIT 20
        """)

    return {
        "total_relationships": stats["total_relationships"],
        "entities_in_graph": stats["entities_in_graph"],
        "whatsapp_co_members": stats["whatsapp_co_members"],
        "top_connections": [
            {
                "entity_a": {"id": str(r["entity_a_id"]), "name": r["name_a"]},
                "entity_b": {"id": str(r["entity_b_id"]), "name": r["name_b"]},
                "weight": r["weight"],
                "type": r["relationship_type"],
            }
            for r in top
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
