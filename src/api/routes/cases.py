"""Saved investigations ("cases") — a pinboard of entities/media/notes/links
with annotations. The difference between a database and a tool you work in.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel

from src.db.connection import get_analyzer_pool
from src.api.face_lookup import representative_faces, face_crop_url

router = APIRouter(tags=["cases"])


@router.get("/cases/{case_id}/export")
async def export_case(case_id: str, format: str = "json"):
    """Download a case as a shareable dossier (json|csv)."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        c = await conn.fetchrow("SELECT id, name, notes FROM cases WHERE id = $1::uuid", case_id)
        if not c:
            raise HTTPException(404, "Case not found")
        items = await conn.fetch("""
            SELECT ci.item_type, ci.ref_id, ci.note,
                   (SELECT canonical_name FROM entities e WHERE e.id::text = ci.ref_id) AS name
            FROM case_items ci WHERE ci.case_id = $1::uuid ORDER BY ci.created_at
        """, case_id)
    if format == "csv":
        rows = ["item_type,ref_id,name,note"]
        for it in items:
            def q(v):
                return '"' + str(v or "").replace('"', "'") + '"'
            rows.append(f"{it['item_type']},{q(it['ref_id'])},{q(it['name'])},{q(it['note'])}")
        return PlainTextResponse("\n".join(rows), headers={
            "Content-Disposition": f'attachment; filename="case-{c["name"]}.csv"'})
    return JSONResponse({
        "name": c["name"], "notes": c["notes"],
        "items": [{"item_type": it["item_type"], "ref_id": it["ref_id"],
                   "name": it["name"], "note": it["note"]} for it in items],
    }, headers={"Content-Disposition": f'attachment; filename="case-{c["name"]}.json"'})


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


# ----- Explore #1: case-workspace v1 (Do Now-scoped orchestrator) -----
# Operator drops in one of {email, username, domain}; we fan out over the
# pipelines that already exist (identity_signals, entity_platform_links,
# whois_cache, google_profile_cache) and return a case-shaped dossier.
# Sequential fanout for v1 - parallel + shared rate-limit ledger is v2.
# See Z:\...\research\scope-explore-1-case-workspace-ui.md.

class CaseRunRequest(BaseModel):
    email: str | None = None
    username: str | None = None
    domain: str | None = None
    save_as: str | None = None  # optional: case name to persist


@router.post("/cases/run")
async def run_case_pivot(req: CaseRunRequest):
    """Fan out over the pivots that already exist and return their results.

    Read-only. Does NOT trigger any external HTTP calls - reads only from
    what our pipelines have already discovered. To trigger new probes the
    operator uses the per-pipeline endpoints or waits for the incremental
    cycle to run.
"""
    if not (req.email or req.username or req.domain):
        raise HTTPException(400, "Provide one of email/username/domain")

    pool = get_analyzer_pool()
    findings: list[dict] = []

    async with pool.acquire() as conn:
        if req.email:
            e = req.email.strip().lower()
            # Existing email_match / commit_email signals.
            rows = await conn.fetch(
                """
                SELECT DISTINCT entity_id::text AS entity_id, signal_type,
                       source_platform, source_record_id, confidence
                FROM identity_signals
                WHERE signal_type IN ('commit_email', 'email_match')
                  AND lower(value) = $1
                  AND entity_id IS NOT NULL
                LIMIT 50
                """,
                e,
            )
            findings.append({
                "pivot": "email_match",
                "input": e,
                "hits": [dict(r) for r in rows],
            })
            # Cached epieos probe (Do Next #1) - COLLECTOR DB.
            try:
                from src.db.connection import get_collector_pool
                col = get_collector_pool()
                async with col.acquire() as ccon:
                    grow = await ccon.fetchrow(
                        """
                        SELECT email, gaia_id, display_name, has_profile,
                               maps_reviews, fetched_at
                        FROM google_profile_cache
                        WHERE email = $1 LIMIT 1
                        """,
                        e,
                    )
                if grow:
                    findings.append({"pivot": "epieos_gaia", "input": e, "hit": dict(grow)})
            except Exception:
                pass

        if req.username:
            u = req.username.strip().lower()
            rows = await conn.fetch(
                """
                SELECT source, platform_username, platform_id, confidence, link_method
                FROM entity_platform_links
                WHERE lower(platform_username) = $1
                LIMIT 50
                """,
                u,
            )
            findings.append({
                "pivot": "username_platform_links",
                "input": u,
                "hits": [dict(r) for r in rows],
            })
            # Historical usernames from _user_changes-derived identity_history signals.
            hrows = await conn.fetch(
                """
                SELECT source_platform, source_record_id, confidence, value
                FROM identity_signals
                WHERE signal_type = 'historical_username_match'
                  AND value ILIKE $1
                LIMIT 50
                """,
                f"%{u}%",
            )
            findings.append({
                "pivot": "historical_username",
                "input": u,
                "hits": [dict(r) for r in hrows],
            })

        if req.domain:
            d = req.domain.strip().lower()
            try:
                from src.db.connection import get_collector_pool
                col = get_collector_pool()
                async with col.acquire() as ccon:
                    wrow = await ccon.fetchrow(
                        """
                        SELECT domain, owner_name, owner_email, registrar,
                               registered_at, privacy_flag, fetched_at
                        FROM whois_cache WHERE domain = $1 LIMIT 1
                        """,
                        d,
                    )
                if wrow:
                    findings.append({"pivot": "whois", "input": d, "hit": dict(wrow)})
            except Exception:
                pass
            # Domain also appears in identity_signals via whois_owner_email.
            drows = await conn.fetch(
                """
                SELECT entity_id::text AS entity_id, source_record_id, confidence
                FROM identity_signals
                WHERE signal_type = 'whois_owner_email'
                  AND source_record_id = $1
                  AND entity_id IS NOT NULL
                LIMIT 20
                """,
                d,
            )
            findings.append({
                "pivot": "whois_owner_email_hits",
                "input": d,
                "hits": [dict(r) for r in drows],
            })

        # Optional persistence: if save_as is set, create a case + one
        # item per pivot for durable audit trail.
        case_id = None
        if req.save_as:
            row = await conn.fetchrow(
                "INSERT INTO cases (name, notes) VALUES ($1, $2) RETURNING id",
                req.save_as,
                f"Auto-generated from POST /cases/run - {len(findings)} pivots",
            )
            case_id = str(row["id"]) if row else None
            if case_id:
                for f in findings:
                    await conn.execute(
                        """
                        INSERT INTO case_items (case_id, item_type, ref_id, note)
                        VALUES ($1::uuid, $2, $3, $4)
                        """,
                        case_id,
                        f["pivot"],
                        str(f.get("input") or ""),
                        f"hits={len((f.get('hits') or [f.get('hit')]) if f.get('hits') is not None else [])}",
                    )
                await conn.execute(
                    "UPDATE cases SET updated_at = NOW() WHERE id = $1::uuid", case_id
                )

    return {
        "input": {"email": req.email, "username": req.username, "domain": req.domain},
        "findings": findings,
        "pivot_count": len(findings),
        "total_hits": sum(len(f.get("hits") or ([f["hit"]] if f.get("hit") else [])) for f in findings),
        "saved_case_id": case_id,
    }
    return {"ok": True}
