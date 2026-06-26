"""Face gallery routes — actually SHOW the faces.

The faces page previously only listed identities (empty) and rendered no images.
This serves face crops on-the-fly (cropped from the source image via the stored
bbox — no pre-generated thumbnails needed) plus a paginated faces list and a
cluster ("people") grouping built from facetracker.faces.cluster_id.

Mounted at /api/face/gallery (see src/api/face_mount.py).
"""
import logging

import cv2
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.face.config import settings
from src.face.storage.database import get_db_session, Face, Image
from src.pipeline.media_common import resolve_media_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/gallery", tags=["face-gallery"])

_CROP_MARGIN = 0.25   # fraction of bbox added on each side for a nicer headshot
_CROP_MAX = 200       # max output crop dimension (px)


def get_db():
    yield from get_db_session(settings.database_url)


@router.get("/faces")
def list_faces(
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=200),
    cluster_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Paginated faces (highest quality first), each with a crop URL. Optionally
    filtered to one cluster."""
    q = db.query(Face, Image.file_path).join(Image, Image.id == Face.image_id)
    if cluster_id is not None:
        q = q.filter(Face.cluster_id == cluster_id)
    total = q.count()
    rows = (
        q.order_by(Face.quality_score.desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    faces = [{
        "face_id": f.id,
        "cluster_id": f.cluster_id,
        "quality": round(f.quality_score or 0.0, 3),
        "crop_url": f"/api/face/gallery/faces/{f.id}/crop",
        "source": (file_path or "").rsplit("/", 1)[-1],
    } for f, file_path in rows]
    return {"faces": faces, "total": total, "page": page, "page_size": page_size}


@router.get("/clusters")
def list_clusters(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Face clusters as 'people' (largest first), each with a representative
    face crop. This is the real grouping until the engine's identities populate."""
    base = db.query(Face.cluster_id, func.count(Face.id).label("n")) \
        .filter(Face.cluster_id.isnot(None)) \
        .group_by(Face.cluster_id) \
        .having(func.count(Face.id) > 1)
    total = base.count()
    rows = base.order_by(text("n DESC")).offset((page - 1) * page_size).limit(page_size).all()
    clusters = []
    for cluster_id, n in rows:
        rep = (
            db.query(Face.id)
            .filter(Face.cluster_id == cluster_id)
            .order_by(Face.quality_score.desc().nullslast())
            .first()
        )
        clusters.append({
            "cluster_id": cluster_id,
            "size": n,
            "cover_url": f"/api/face/gallery/faces/{rep[0]}/crop" if rep else None,
        })
    return {"clusters": clusters, "total": total, "page": page, "page_size": page_size}


@router.get("/faces/{face_id}/similar")
def similar_faces(face_id: int, k: int = Query(40, ge=1, le=120), db: Session = Depends(get_db)):
    """"Who is this?" — kNN over the ArcFace corpus (pgvector cosine). Returns
    the most similar faces with similarity, cluster, and bridged entity. Powers
    face search + the face->entity bridging triage."""
    rows = db.execute(text("""
        SELECT f.id AS face_id, f.cluster_id,
               1 - (f.embedding_vec <=> t.embedding_vec) AS sim,
               (SELECT ef.entity_id FROM public.entity_faces ef WHERE ef.face_id = f.id LIMIT 1) AS entity_id,
               (SELECT e.canonical_name FROM public.entity_faces ef
                  JOIN public.entities e ON e.id = ef.entity_id
                  WHERE ef.face_id = f.id LIMIT 1) AS entity_name
        FROM facetracker.faces f, facetracker.faces t
        WHERE t.id = :fid AND f.id != :fid AND f.embedding_vec IS NOT NULL
        ORDER BY f.embedding_vec <=> t.embedding_vec
        LIMIT :k
    """), {"fid": face_id, "k": k}).fetchall()
    return {"matches": [{
        "face_id": r.face_id,
        "cluster_id": r.cluster_id,
        "similarity": round(float(r.sim), 4),
        "crop_url": f"/api/face/gallery/faces/{r.face_id}/crop",
        "entity_id": str(r.entity_id) if r.entity_id else None,
        "entity_name": r.entity_name,
    } for r in rows]}


@router.get("/faces/{face_id}/crop")
def face_crop(face_id: int, db: Session = Depends(get_db)):
    """Crop the face from its source image (bbox + margin) and return a JPEG.
    On-demand — no stored thumbnails. 404 if the source media isn't reachable
    (e.g. drive faces whose drive isn't mounted in the API container)."""
    row = db.query(Face, Image.file_path).join(Image, Image.id == Face.image_id) \
        .filter(Face.id == face_id).first()
    if not row:
        raise HTTPException(404, "face not found")
    face, file_path = row
    disk = resolve_media_path(file_path)
    if disk is None:
        raise HTTPException(404, "source media not reachable")
    img = cv2.imread(str(disk))
    if img is None:
        raise HTTPException(404, "source image unreadable")

    h, w = img.shape[:2]
    x1, y1, x2, y2 = face.bbox_px_x1, face.bbox_px_y1, face.bbox_px_x2, face.bbox_px_y2
    mx, my = int((x2 - x1) * _CROP_MARGIN), int((y2 - y1) * _CROP_MARGIN)
    x1 = max(0, x1 - mx); y1 = max(0, y1 - my)
    x2 = min(w, x2 + mx); y2 = min(h, y2 + my)
    if x2 <= x1 or y2 <= y1:
        raise HTTPException(404, "invalid bbox")
    crop = img[y1:y2, x1:x2]

    ch, cw = crop.shape[:2]
    if max(ch, cw) > _CROP_MAX:
        s = _CROP_MAX / max(ch, cw)
        crop = cv2.resize(crop, (max(1, int(cw * s)), max(1, int(ch * s))))
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise HTTPException(500, "encode failed")
    return Response(content=buf.tobytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})
