import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db.connection import get_analyzer_pool

router = APIRouter(tags=["entity-actions"])


class MergeRequest(BaseModel):
    source_entity_ids: list[str]
    reason: str = ""


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

    return {"ok": True, "target_entity_id": str(target_id), "merged": len(others)}


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

    return {"ok": True}
