from fastapi import APIRouter, Query

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.api.face_lookup import representative_faces, face_crop_url

router = APIRouter(tags=["graph"])


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


@router.get("/entities/{entity_id}/geo")
async def entity_geo(entity_id: str):
    """Geo footprint for the map: Strava route polylines + start points, and
    Instagram tagged-place pins. Reads the collector DB."""
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT source, platform_id FROM entity_platform_links WHERE entity_id = $1::uuid", entity_id
        )
    strava_ids = [l["platform_id"] for l in links if l["source"] == "strava" and l["platform_id"]]
    ig_ids = [l["platform_id"] for l in links if l["source"] == "instagram" and l["platform_id"]]

    routes: list[dict] = []
    points: list[dict] = []
    ig_place_names: list[str] = []
    async with collector.acquire() as cc:
        if strava_ids:
            # Prefer full-res gps_streams.latlng; fall back to the (now
            # backfilled) summary_polyline so activities with only the overview
            # line still render.
            acts = await cc.fetch("""
                SELECT a.name, a.start_date, a.type, a.start_latlng, a.summary_polyline,
                       s.latlng::text AS latlng
                FROM strava_activities a
                JOIN strava_athletes ath ON ath.id = a.athlete_id
                LEFT JOIN strava_gps_streams s ON s.activity_id = a.id
                WHERE ath.platform_athlete_id::text = ANY($1::text[])
                  AND (s.latlng IS NOT NULL
                       OR (a.summary_polyline IS NOT NULL AND a.summary_polyline <> ''))
                ORDER BY a.start_date DESC NULLS LAST LIMIT 100
            """, strava_ids)
            for r in acts:
                track = _as_latlng_list(r["latlng"]) if r["latlng"] else _decode_polyline(r["summary_polyline"] or "")
                pts = _downsample(track)
                if len(pts) >= 2:
                    routes.append({"name": r["name"], "type": r["type"],
                                   "date": r["start_date"].isoformat() if r["start_date"] else None,
                                   "points": pts})
                sp = _parse_latlng(r["start_latlng"])
                if sp:
                    points.append({"lat": sp[0], "lng": sp[1], "label": r["name"], "source": "strava"})
        if ig_ids:
            posts = await cc.fetch("""
                SELECT p.location_name, p.location_lat, p.location_lng
                FROM instagram_posts p
                JOIN instagram_profiles pr ON pr.id = p.profile_id
                WHERE pr.platform_user_id::text = ANY($1::text[]) AND p.location_lat IS NOT NULL
                ORDER BY p.platform_created_at DESC NULLS LAST LIMIT 300
            """, ig_ids)
            for p in posts:
                points.append({"lat": float(p["location_lat"]), "lng": float(p["location_lng"]),
                               "label": p["location_name"], "source": "instagram"})
            # IG stores only place NAMES (no coords); collect them to resolve via
            # the geocode cache below.
            named = await cc.fetch("""
                SELECT DISTINCT p.location_name
                FROM instagram_posts p
                JOIN instagram_profiles pr ON pr.id = p.profile_id
                WHERE pr.platform_user_id::text = ANY($1::text[])
                  AND p.location_name IS NOT NULL AND p.location_name <> ''
                LIMIT 500
            """, ig_ids)
            ig_place_names = [r["location_name"] for r in named]

    # Geocoded IG place-name pins (cache lives in the analyzer DB).
    if ig_place_names:
        async with analyzer.acquire() as conn:
            geo = await conn.fetch(
                "SELECT place_name, lat, lng FROM geocode_cache "
                "WHERE status = 'ok' AND place_name = ANY($1::text[])", ig_place_names)
        for g in geo:
            points.append({"lat": g["lat"], "lng": g["lng"], "label": g["place_name"], "source": "instagram"})

    return {"routes": routes, "points": points,
            "counts": {"routes": len(routes), "points": len(points)}}


@router.get("/entities/{entity_id}/associates")
async def entity_associates(entity_id: str, limit: int = Query(40, ge=1, le=100)):
    """"Seen with" co-presence from Instagram tagged photos
    (media_items kind='tagged'). content_id = tagged_<mediaPk>_<ownerPk> links a
    tagged person (entity_name) to the poster (ownerPk). For this entity the
    associates are: the posters who tagged them (entity is the tagged person) +
    the people they tagged (entity is the poster). Resolved to analyzer entities
    where possible; otherwise social_users supplies a name/photo."""
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT platform_id, platform_username FROM entity_platform_links "
            "WHERE entity_id = $1::uuid AND source = 'instagram'", entity_id
        )
    ig_ids = [l["platform_id"] for l in links if l["platform_id"]]
    ig_users = [l["platform_username"] for l in links if l["platform_username"]]
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
