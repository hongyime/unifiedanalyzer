"""Read-only audits for face-to-entity bridge collision risk."""
from __future__ import annotations

from datetime import datetime
from typing import Any


_UNSAFE_CLUSTER_METHODS = (
    "cluster_propagation",
    "drive_cross_ref",
    "drive_cross_ref_knn",
    "face_cluster",
    "knn_propagation",
)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _face_sample(row: Any) -> dict:
    return {
        "face_id": int(row["face_id"]),
        "entity_count": int(row["entity_count"] or 0),
        "entity_ids": _as_list(row["entity_ids"]),
        "entity_names": _as_list(row["entity_names"]),
        "methods": _as_list(row["methods"]),
        "latest_created_at": _iso(row["latest_created_at"]),
    }


def _cluster_sample(row: Any) -> dict:
    return {
        "cluster_id": int(row["cluster_id"]),
        "entity_count": int(row["entity_count"] or 0),
        "face_count": int(row["face_count"] or 0),
        "entity_ids": _as_list(row["entity_ids"]),
        "entity_names": _as_list(row["entity_names"]),
        "methods": _as_list(row["methods"]),
        "latest_created_at": _iso(row["latest_created_at"]),
    }


async def audit_face_bridge_collisions(conn, sample_limit: int = 5) -> dict:
    """Return direct face and cluster collisions in ``entity_faces``.

    A direct face collision is always unsafe: one face crop should not map to
    multiple entities. A cluster collision is only health-blocking when it
    contains propagated/derived rows; direct anchors in the same cluster mark the
    cluster contested, and downstream face-derived builders already fence those
    clusters off. This function is intentionally read-only.
    """
    limit = max(0, min(int(sample_limit or 0), 50))
    try:
        face_collision_count = int(await conn.fetchval("""
            WITH face_collisions AS (
                SELECT ef.face_id
                FROM public.entity_faces ef
                GROUP BY ef.face_id
                HAVING count(DISTINCT ef.entity_id) > 1
            )
            SELECT count(*) FROM face_collisions
        """) or 0)

        contested_cluster_count = int(await conn.fetchval("""
            WITH cluster_collisions AS (
                SELECT f.cluster_id
                FROM facetracker.faces f
                JOIN public.entity_faces ef ON ef.face_id = f.id
                WHERE f.cluster_id IS NOT NULL
                  AND NOT COALESCE(f.is_junk, FALSE)
                GROUP BY f.cluster_id
                HAVING count(DISTINCT ef.entity_id) > 1
            )
            SELECT count(*) FROM cluster_collisions
        """) or 0)

        unsafe_cluster_collision_count = int(await conn.fetchval("""
            WITH unsafe_cluster_collisions AS (
                SELECT f.cluster_id
                FROM facetracker.faces f
                JOIN public.entity_faces ef ON ef.face_id = f.id
                WHERE f.cluster_id IS NOT NULL
                  AND NOT COALESCE(f.is_junk, FALSE)
                GROUP BY f.cluster_id
                HAVING count(DISTINCT ef.entity_id) > 1
                   AND bool_or(ef.method = ANY($1::text[]))
            )
            SELECT count(*) FROM unsafe_cluster_collisions
        """, list(_UNSAFE_CLUSTER_METHODS)) or 0)

        face_rows = await conn.fetch("""
            SELECT
                ef.face_id,
                count(DISTINCT ef.entity_id)::int AS entity_count,
                array_agg(DISTINCT ef.entity_id::text ORDER BY ef.entity_id::text) AS entity_ids,
                array_agg(DISTINCT e.canonical_name ORDER BY e.canonical_name)
                    FILTER (WHERE e.canonical_name IS NOT NULL) AS entity_names,
                array_agg(DISTINCT ef.method ORDER BY ef.method)
                    FILTER (WHERE ef.method IS NOT NULL) AS methods,
                max(ef.created_at) AS latest_created_at
            FROM public.entity_faces ef
            LEFT JOIN public.entities e ON e.id = ef.entity_id
            GROUP BY ef.face_id
            HAVING count(DISTINCT ef.entity_id) > 1
            ORDER BY entity_count DESC, ef.face_id
            LIMIT $1
        """, limit)

        unsafe_cluster_rows = await conn.fetch("""
            SELECT
                f.cluster_id,
                count(DISTINCT ef.entity_id)::int AS entity_count,
                count(DISTINCT f.id)::int AS face_count,
                array_agg(DISTINCT ef.entity_id::text ORDER BY ef.entity_id::text) AS entity_ids,
                array_agg(DISTINCT e.canonical_name ORDER BY e.canonical_name)
                    FILTER (WHERE e.canonical_name IS NOT NULL) AS entity_names,
                array_agg(DISTINCT ef.method ORDER BY ef.method)
                    FILTER (WHERE ef.method IS NOT NULL) AS methods,
                max(ef.created_at) AS latest_created_at
            FROM facetracker.faces f
            JOIN public.entity_faces ef ON ef.face_id = f.id
            LEFT JOIN public.entities e ON e.id = ef.entity_id
            WHERE f.cluster_id IS NOT NULL
              AND NOT COALESCE(f.is_junk, FALSE)
            GROUP BY f.cluster_id
            HAVING count(DISTINCT ef.entity_id) > 1
               AND bool_or(ef.method = ANY($2::text[]))
            ORDER BY entity_count DESC, face_count DESC, f.cluster_id
            LIMIT $1
        """, limit, list(_UNSAFE_CLUSTER_METHODS))

        contested_cluster_rows = await conn.fetch("""
            SELECT
                f.cluster_id,
                count(DISTINCT ef.entity_id)::int AS entity_count,
                count(DISTINCT f.id)::int AS face_count,
                array_agg(DISTINCT ef.entity_id::text ORDER BY ef.entity_id::text) AS entity_ids,
                array_agg(DISTINCT e.canonical_name ORDER BY e.canonical_name)
                    FILTER (WHERE e.canonical_name IS NOT NULL) AS entity_names,
                array_agg(DISTINCT ef.method ORDER BY ef.method)
                    FILTER (WHERE ef.method IS NOT NULL) AS methods,
                max(ef.created_at) AS latest_created_at
            FROM facetracker.faces f
            JOIN public.entity_faces ef ON ef.face_id = f.id
            LEFT JOIN public.entities e ON e.id = ef.entity_id
            WHERE f.cluster_id IS NOT NULL
              AND NOT COALESCE(f.is_junk, FALSE)
            GROUP BY f.cluster_id
            HAVING count(DISTINCT ef.entity_id) > 1
            ORDER BY entity_count DESC, face_count DESC, f.cluster_id
            LIMIT $1
        """, limit)
    except Exception as exc:  # noqa: BLE001 - health/triage must degrade, not crash
        return {
            "available": False,
            "ok": None,
            "error": str(exc),
            "face_entity_collisions": None,
            "cluster_entity_collisions": None,
            "contested_cluster_count": None,
            "samples": {"faces": [], "clusters": [], "contested_clusters": []},
        }

    return {
        "available": True,
        "ok": face_collision_count == 0 and unsafe_cluster_collision_count == 0,
        "face_entity_collisions": face_collision_count,
        "cluster_entity_collisions": unsafe_cluster_collision_count,
        "contested_cluster_count": contested_cluster_count,
        "samples": {
            "faces": [_face_sample(r) for r in face_rows],
            "clusters": [_cluster_sample(r) for r in unsafe_cluster_rows],
            "contested_clusters": [_cluster_sample(r) for r in contested_cluster_rows],
        },
    }
