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
from sqlalchemy import func, or_, text
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


def _servable_media_filter():
    """DB-side prefilter for face crops the API can actually serve.

    resolve_media_path() is still the final safety check in /crop. This filter
    keeps the gallery from filling first pages with high-quality but unreachable
    archival paths like /mnt/w/... that are indexed in DB but not mounted in the
    API container.
    """
    return or_(
        Image.file_path.like("/media/%"),
        Image.file_path.like("/vault/media/%"),
        Image.file_path.ilike("%/media/%"),
        Image.file_path.ilike("%\\media\\%"),
        Image.file_path.ilike("%media_derived/%"),
    )


@router.get("/faces")
def list_faces(
    page: int = Query(1, ge=1),
    page_size: int = Query(60, ge=1, le=200),
    cluster_id: int | None = None,
    include_unreachable: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Paginated faces (highest quality first), each with a crop URL. Optionally
    filtered to one cluster."""
    q = db.query(Face.id, Face.cluster_id, Face.quality_score, Image.file_path).join(
        Image, Image.id == Face.image_id
    )
    count_q = db.query(func.count(Face.id)).join(Image, Image.id == Face.image_id)
    if not include_unreachable:
        q = q.filter(_servable_media_filter())
        count_q = count_q.filter(_servable_media_filter())
    if cluster_id is not None:
        q = q.filter(Face.cluster_id == cluster_id)
        count_q = count_q.filter(Face.cluster_id == cluster_id)
    total = count_q.scalar()
    rows = (
        q.order_by(Face.quality_score.desc().nullslast())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    faces = [{
        "face_id": face_id,
        "cluster_id": cluster_id,
        "quality": round(quality_score or 0.0, 3),
        "crop_url": f"/api/face/gallery/faces/{face_id}/crop",
        "source": (file_path or "").rsplit("/", 1)[-1],
    } for face_id, cluster_id, quality_score, file_path in rows]
    return {"faces": faces, "total": total, "page": page, "page_size": page_size}


@router.get("/clusters")
def list_clusters(
    page: int = Query(1, ge=1),
    page_size: int = Query(40, ge=1, le=100),
    include_unreachable: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Face clusters as 'people' (largest first), each with a representative
    face crop. This is the real grouping until the engine's identities populate."""
    servable_sql = ""
    if not include_unreachable:
        servable_sql = """
              AND (
                   i.file_path LIKE '/media/%'
                OR i.file_path LIKE '/vault/media/%'
                OR i.file_path ILIKE '%/media/%'
                OR i.file_path ILIKE '%\\media\\%'
                OR i.file_path ILIKE '%media_derived/%'
              )
        """
    rows = db.execute(text(f"""
        WITH eligible AS (
            SELECT f.id, f.cluster_id, f.quality_score
            FROM facetracker.faces f
            JOIN facetracker.images i ON i.id = f.image_id
            WHERE f.cluster_id IS NOT NULL
              {servable_sql}
        ),
        grouped AS (
            SELECT
                cluster_id,
                COUNT(*)::int AS n,
                (ARRAY_AGG(id ORDER BY quality_score DESC NULLS LAST))[1] AS rep_id
            FROM eligible
            GROUP BY cluster_id
            HAVING COUNT(*) > 1
        )
        SELECT cluster_id, n, rep_id, COUNT(*) OVER()::int AS total
        FROM grouped
        ORDER BY n DESC
        OFFSET :offset
        LIMIT :limit
    """), {"offset": (page - 1) * page_size, "limit": page_size}).fetchall()
    total = rows[0].total if rows else 0
    clusters = [{
        "cluster_id": r.cluster_id,
        "size": r.n,
        "cover_url": f"/api/face/gallery/faces/{r.rep_id}/crop" if r.rep_id else None,
    } for r in rows]
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
    row = db.query(Face, Image.file_path, Image.is_video).join(Image, Image.id == Face.image_id) \
        .filter(Face.id == face_id).first()
    if not row:
        raise HTTPException(404, "face not found")
    face, file_path, is_video = row
    disk = resolve_media_path(file_path)
    if disk is None:
        raise HTTPException(404, "source media not reachable")
    if is_video:
        img = None
        cap = cv2.VideoCapture(str(disk))
        try:
            if cap.isOpened():
                frame_second = face.frame_number if face.frame_number is not None else 0
                if frame_second and frame_second > 0:
                    cap.set(cv2.CAP_PROP_POS_MSEC, float(frame_second) * 1000.0)
                ok, img = cap.read()
                if not ok and frame_second:
                    cap.set(cv2.CAP_PROP_POS_MSEC, 0)
                    ok, img = cap.read()
                if not ok:
                    img = None
        finally:
            cap.release()
    else:
        img = cv2.imread(str(disk))
    if img is None:
        raise HTTPException(404, "source image/frame unreadable")

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
