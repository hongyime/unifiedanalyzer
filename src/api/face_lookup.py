"""Representative-face lookup shared by the entity/intelligence/review routes.

Picks each entity's highest-quality bridged face (public.entity_faces ->
facetracker.faces) so the UI can show a face thumbnail wherever a person
appears. Crop images are served by src/face/api/routes/gallery.py.
"""


async def representative_faces(conn, entity_ids: list[str]) -> dict[str, int]:
    """{entity_id: best face_id} for the given entities (highest quality first).
    Entities with no bridged face are simply absent from the result."""
    ids = [e for e in {str(x) for x in entity_ids} if e]
    if not ids:
        return {}
    rows = await conn.fetch("""
        SELECT DISTINCT ON (ef.entity_id) ef.entity_id::text AS eid, ef.face_id
        FROM public.entity_faces ef
        JOIN facetracker.faces f ON f.id = ef.face_id
        WHERE ef.entity_id = ANY($1::uuid[])
        ORDER BY ef.entity_id, f.quality_score DESC NULLS LAST
    """, ids)
    return {r["eid"]: r["face_id"] for r in rows}


def face_crop_url(face_id) -> str | None:
    """URL for a face crop (served by the gallery route), or None."""
    return f"/api/face/gallery/faces/{face_id}/crop" if face_id is not None else None
