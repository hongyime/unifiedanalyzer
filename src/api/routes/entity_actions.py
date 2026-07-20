from itertools import combinations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db.connection import get_analyzer_pool
from src.pipeline.identity_calibration import record_label
from src.util.audit_log import append_audit

router = APIRouter(tags=["entity-actions"])


async def _entity_snapshots(conn, entity_ids: list[str]) -> list[dict]:
    rows = await conn.fetch("""
        SELECT e.id::text AS entity_id,
               e.canonical_name,
               e.tier,
               e.confidence_score,
               e.watch_status,
               l.id::text AS link_id,
               l.source,
               l.platform_id,
               l.platform_username,
               l.platform_name,
               l.link_method,
               l.confidence AS link_confidence
        FROM entities e
        LEFT JOIN entity_platform_links l ON l.entity_id = e.id
        WHERE e.id = ANY($1::uuid[])
        ORDER BY e.id, l.source, l.platform_username NULLS LAST, l.platform_id
    """, entity_ids)
    by_id: dict[str, dict] = {}
    for row in rows:
        eid = row["entity_id"]
        snap = by_id.setdefault(eid, {
            "entity_id": eid,
            "canonical_name": row["canonical_name"],
            "tier": row["tier"],
            "confidence_score": float(row["confidence_score"] or 0),
            "watch_status": row["watch_status"],
            "platform_links": [],
        })
        if row["link_id"]:
            snap["platform_links"].append({
                "link_id": row["link_id"],
                "source": row["source"],
                "platform_id": row["platform_id"],
                "platform_username": row["platform_username"],
                "platform_name": row["platform_name"],
                "link_method": row["link_method"],
                "confidence": float(row["link_confidence"] or 0),
            })
    return list(by_id.values())


class MergeRequest(BaseModel):
    source_entity_ids: list[str]
    reason: str = ""


class DismissMatchRequest(BaseModel):
    entity_a: str
    entity_b: str


class WatchRequest(BaseModel):
    status: str | None = None  # priority | watching | archive | null


@router.patch("/entities/{entity_id}/watch")
async def set_watch(entity_id: str, req: WatchRequest):
    """Set the user-curated watchlist tier on an entity."""
    s = req.status if req.status in ("priority", "watching", "archive") else None
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            "SELECT watch_status FROM entities WHERE id = $1::uuid", entity_id
        )
        await conn.execute(
            "UPDATE entities SET watch_status = $1, updated_at = NOW() WHERE id = $2::uuid", s, entity_id
        )
        await append_audit(
            conn,
            action="assign_target_tier",
            actor="dashboard",
            entity_ids=[entity_id],
            payload={
                "previous_watch_status": before["watch_status"] if before else None,
                "watch_status": s,
                "entity_snapshot": (await _entity_snapshots(conn, [entity_id]))[0:1],
            },
        )
    return {"ok": True, "watch_status": s}


class SplitRequest(BaseModel):
    link_ids: list[str]
    reason: str = ""


class AlertTuningRequest(BaseModel):
    silence_threshold_days: float | None = None
    notes: str | None = None


@router.post("/entities/merge")
async def merge_entities(req: MergeRequest):
    if len(req.source_entity_ids) < 2:
        raise HTTPException(400, "Need at least 2 entities to merge")

    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entities = await conn.fetch("""
            SELECT id, canonical_name, confidence_score
            FROM entities WHERE id = ANY($1::uuid[])
        """, req.source_entity_ids)

        if len(entities) != len(req.source_entity_ids):
            raise HTTPException(404, "One or more entities not found")

        primary = max(entities, key=lambda e: e["confidence_score"])
        target_id = primary["id"]
        others = [e["id"] for e in entities if e["id"] != target_id]
        snapshots_before = await _entity_snapshots(conn, req.source_entity_ids)

        # Capture "same person" labels (1) for every merged pair BEFORE the
        # mutations below move/delete their identity_signals — this is the
        # ground truth that trains the calibrated scorer (no CSV). Snapshots
        # each pair's current feature vector.
        for a, b in combinations(req.source_entity_ids, 2):
            try:
                await record_label(conn, a, b, 1, "dashboard_merge")
            except Exception:
                pass  # labelling must never block a merge

        for oid in others:
            await conn.execute("""
                UPDATE entity_platform_links SET entity_id = $1, updated_at = NOW()
                WHERE entity_id = $2
            """, target_id, oid)

            await conn.execute("""
                UPDATE timeline_events SET entity_id = $1
                WHERE entity_id = $2
            """, target_id, oid)

            await conn.execute("""
                UPDATE alerts SET entity_id = $1
                WHERE entity_id = $2
            """, target_id, oid)

            await conn.execute("""
                UPDATE identity_signals SET entity_id = $1
                WHERE entity_id = $2
            """, target_id, oid)

            await conn.execute("DELETE FROM entities WHERE id = $1", oid)

        link_count = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_platform_links WHERE entity_id = $1", target_id
        )
        await conn.execute("""
            UPDATE entities SET signal_count = $1, updated_at = NOW()
            WHERE id = $2
        """, link_count, target_id)

        await conn.execute("""
            INSERT INTO entity_merge_log (action, source_entity_ids, target_entity_id, reason)
            VALUES ('merge', $1::uuid[], $2, $3)
        """, req.source_entity_ids, target_id, req.reason)

        # Hash-chained DB audit + append-only JSONL decision event. Non-fatal.
        await append_audit(
            conn, action="merge_confirmed", actor="dashboard",
            entity_ids=req.source_entity_ids,
            payload={
                "target_entity_id": str(target_id),
                "source_entity_ids": [str(x) for x in req.source_entity_ids],
                "merged_entity_ids": [str(x) for x in others],
                "merged_count": len(others),
                "reason": req.reason or "",
                "confidence": 100,
                "entity_snapshots_before": snapshots_before,
            },
        )

    return {"ok": True, "target_entity_id": str(target_id), "merged": len(others)}


@router.post("/entities/dismiss-match")
async def dismiss_match(req: DismissMatchRequest):
    """User said two same-person CANDIDATES are NOT the same person. Records a
    negative (0) label for the calibrated scorer and removes the current
    same_person_probability suggestion so it stops surfacing."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            snapshots = await _entity_snapshots(conn, [req.entity_a, req.entity_b])
            relationship = await conn.fetchrow("""
                SELECT weight, cross_platform, sources
                FROM entity_relationships
                WHERE relationship_type = 'same_person_probability'
                  AND ((entity_a_id = $1::uuid AND entity_b_id = $2::uuid)
                    OR (entity_a_id = $2::uuid AND entity_b_id = $1::uuid))
                ORDER BY weight DESC NULLS LAST, updated_at DESC NULLS LAST
                LIMIT 1
            """, req.entity_a, req.entity_b)
            try:
                await record_label(conn, req.entity_a, req.entity_b, 0, "dashboard_dismiss")
            except Exception:
                pass
            await conn.execute("""
                DELETE FROM entity_relationships
                WHERE relationship_type = 'same_person_probability'
                  AND ((entity_a_id = $1::uuid AND entity_b_id = $2::uuid)
                    OR (entity_a_id = $2::uuid AND entity_b_id = $1::uuid))
            """, req.entity_a, req.entity_b)

            await append_audit(
                conn, action="dismiss_identity_candidate", actor="dashboard",
                entity_ids=[req.entity_a, req.entity_b],
                payload={
                    "confidence": "X",
                    "entity_a": req.entity_a,
                    "entity_b": req.entity_b,
                    "entity_snapshots": snapshots,
                    "candidate_evidence": {
                        "weight": relationship["weight"] if relationship else None,
                        "cross_platform": relationship["cross_platform"] if relationship else None,
                        "sources": relationship["sources"] if relationship else None,
                    },
                },
            )
    return {"ok": True}


@router.post("/entities/{entity_id}/split")
async def split_entity(entity_id: str, req: SplitRequest):
    if not req.link_ids:
        raise HTTPException(400, "No links specified to split out")

    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT id FROM entities WHERE id = $1::uuid", entity_id
        )
        if not entity:
            raise HTTPException(404, "Entity not found")

        links = await conn.fetch("""
            SELECT id, source, platform_id, platform_username, platform_name
            FROM entity_platform_links
            WHERE id = ANY($1::uuid[]) AND entity_id = $2::uuid
        """, req.link_ids, entity_id)

        if not links:
            raise HTTPException(404, "No matching links found on this entity")

        total_links = await conn.fetchval(
            "SELECT COUNT(*) FROM entity_platform_links WHERE entity_id = $1::uuid", entity_id
        )
        if len(links) >= total_links:
            raise HTTPException(400, "Cannot split all links — entity would be empty")

        name = links[0]["platform_name"] or links[0]["platform_username"] or "Split entity"
        new_id = await conn.fetchval("""
            INSERT INTO entities (tier, canonical_name, confidence_score, signal_count)
            VALUES ('primary', $1, 0.0, 0) RETURNING id
        """, name)

        for link in links:
            await conn.execute("""
                UPDATE entity_platform_links SET entity_id = $1, link_method = 'manual_split', updated_at = NOW()
                WHERE id = $2
            """, new_id, link["id"])

        await conn.execute("""
            INSERT INTO entity_merge_log (action, source_entity_ids, target_entity_id, reason)
            VALUES ('split', $1::uuid[], $2, $3)
        """, [entity["id"]], new_id, req.reason)

        await append_audit(
            conn,
            action="split_person",
            actor="dashboard",
            entity_ids=[entity_id, str(new_id)],
            payload={
                "source_entity_id": entity_id,
                "new_entity_id": str(new_id),
                "split_link_ids": req.link_ids,
                "split_links": [dict(link) for link in links],
                "reason": req.reason or "",
                "source_entity_snapshot_after": await _entity_snapshots(conn, [entity_id]),
                "new_entity_snapshot": await _entity_snapshots(conn, [str(new_id)]),
            },
        )

    return {"ok": True, "new_entity_id": str(new_id)}


@router.patch("/entities/{entity_id}/settings")
async def update_entity_settings(entity_id: str, req: AlertTuningRequest):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity = await conn.fetchrow(
            "SELECT id FROM entities WHERE id = $1::uuid", entity_id
        )
        if not entity:
            raise HTTPException(404, "Entity not found")

        updates = []
        params = []
        idx = 1

        if req.silence_threshold_days is not None:
            updates.append(f"silence_threshold_days = ${idx}")
            params.append(req.silence_threshold_days if req.silence_threshold_days > 0 else None)
            idx += 1

        if req.notes is not None:
            updates.append(f"notes = ${idx}")
            params.append(req.notes)
            idx += 1

        if updates:
            params.append(entity_id)
            await conn.execute(
                f"UPDATE entities SET {', '.join(updates)}, updated_at = NOW() WHERE id = ${idx}::uuid",
                *params
            )
            await append_audit(
                conn,
                action="add_note",
                actor="dashboard",
                entity_ids=[entity_id],
                payload={
                    "silence_threshold_days": req.silence_threshold_days,
                    "notes": req.notes,
                    "entity_snapshot": (await _entity_snapshots(conn, [entity_id]))[0:1],
                },
            )

    return {"ok": True}
