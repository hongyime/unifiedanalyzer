"""Saved investigations ("cases") — a pinboard of entities/media/notes/links
with annotations. The difference between a database and a tool you work in.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.db.connection import get_analyzer_pool
from src.api.face_lookup import representative_faces, face_crop_url

router = APIRouter(tags=["cases"])


class CaseCreate(BaseModel):
    name: str
    notes: str | None = None


class CaseUpdate(BaseModel):
    name: str | None = None
    notes: str | None = None


class ItemAdd(BaseModel):
    item_type: str           # entity | media | note | link
    ref_id: str | None = None
    note: str | None = None


@router.get("/cases")
async def list_cases():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.id, c.name, c.notes, c.updated_at,
                   (SELECT count(*) FROM case_items ci WHERE ci.case_id = c.id) AS items
            FROM cases c ORDER BY c.updated_at DESC
        """)
    return {"cases": [{
        "id": str(r["id"]), "name": r["name"], "notes": r["notes"],
        "items": r["items"], "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    } for r in rows]}


@router.post("/cases")
async def create_case(req: CaseCreate):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        cid = await conn.fetchval(
            "INSERT INTO cases (name, notes) VALUES ($1, $2) RETURNING id", req.name, req.notes
        )
    return {"ok": True, "id": str(cid)}


@router.get("/cases/{case_id}")
async def get_case(case_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        c = await conn.fetchrow("SELECT id, name, notes, updated_at FROM cases WHERE id = $1::uuid", case_id)
        if not c:
            raise HTTPException(404, "Case not found")
        items = await conn.fetch("""
            SELECT id, item_type, ref_id, note, created_at FROM case_items
            WHERE case_id = $1::uuid ORDER BY created_at
        """, case_id)
        ent_ids = [r["ref_id"] for r in items if r["item_type"] == "entity" and r["ref_id"]]
        names = {}
        if ent_ids:
            nrows = await conn.fetch(
                "SELECT id::text AS id, canonical_name FROM entities WHERE id = ANY($1::uuid[])", ent_ids
            )
            names = {r["id"]: r["canonical_name"] for r in nrows}
            rep = await representative_faces(conn, ent_ids)
        else:
            rep = {}
    return {
        "id": str(c["id"]), "name": c["name"], "notes": c["notes"],
        "items": [{
            "id": str(r["id"]), "item_type": r["item_type"], "ref_id": r["ref_id"], "note": r["note"],
            "entity_name": names.get(r["ref_id"]) if r["item_type"] == "entity" else None,
            "face": face_crop_url(rep.get(r["ref_id"])) if r["item_type"] == "entity" else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        } for r in items],
    }


@router.patch("/cases/{case_id}")
async def update_case(case_id: str, req: CaseUpdate):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE cases SET
              name = COALESCE($2, name),
              notes = COALESCE($3, notes),
              updated_at = NOW()
            WHERE id = $1::uuid
        """, case_id, req.name, req.notes)
    return {"ok": True}


@router.delete("/cases/{case_id}")
async def delete_case(case_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM cases WHERE id = $1::uuid", case_id)
    return {"ok": True}


@router.post("/cases/{case_id}/items")
async def add_item(case_id: str, req: ItemAdd):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM cases WHERE id = $1::uuid", case_id)
        if not exists:
            raise HTTPException(404, "Case not found")
        iid = await conn.fetchval("""
            INSERT INTO case_items (case_id, item_type, ref_id, note)
            VALUES ($1::uuid, $2, $3, $4) RETURNING id
        """, case_id, req.item_type, req.ref_id, req.note)
        await conn.execute("UPDATE cases SET updated_at = NOW() WHERE id = $1::uuid", case_id)
    return {"ok": True, "id": str(iid)}


@router.delete("/cases/{case_id}/items/{item_id}")
async def delete_item(case_id: str, item_id: str):
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM case_items WHERE id = $1::uuid AND case_id = $2::uuid", item_id, case_id)
    return {"ok": True}
