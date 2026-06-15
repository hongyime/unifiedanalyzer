"""Identity management API routes.

Option A identity subsystem: full CRUD for identities with merge, split,
rename, and face listing. Incremental clustering runs automatically after
each scan cycle (see manager.py). These endpoints let the user review and
correct the auto-clustering results.
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
import numpy as np

from src.face.config import settings
from src.face.storage.database import get_database, get_db_session, Identity, Face, FaceIdentityMap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/identities", tags=["identities"])


class IdentityResponse(BaseModel):
    """Response model for identity information."""
    identity_id: str
    name: Optional[str]
    face_count: int
    created_at: str
    updated_at: str
    avg_quality_score: float
    thumbnail_url: Optional[str]


class FaceResponse(BaseModel):
    """Response model for a face within an identity."""
    face_id: int
    embedding_id: str
    similarity_to_centroid: Optional[float]
    quality_score: float
    thumbnail_path: Optional[str]
    file_path: Optional[str]
    is_primary: bool


class IdentityListResponse(BaseModel):
    """Response model for listing identities."""
    identities: List[IdentityResponse]
    total: int
    page: int
    page_size: int


class MergeRequest(BaseModel):
    """Request to merge identities into a target."""
    source_ids: List[int]
    target_id: int


class SplitRequest(BaseModel):
    """Request to split faces from an identity into a new one."""
    face_ids: List[int]
    new_identity_name: Optional[str] = None


class RenameRequest(BaseModel):
    """Request to rename an identity."""
    name: str


def get_db():
    """Dependency to get database session — request-scoped, pool-safe."""
    yield from get_db_session(settings.database_url)


def _identity_response(identity: Identity, db: Session) -> IdentityResponse:
    """Build an IdentityResponse from an Identity row."""
    face_count = db.query(func.count(FaceIdentityMap.id)).filter(
        FaceIdentityMap.identity_id == identity.id
    ).scalar()

    avg_quality = db.query(func.avg(Face.quality_score)).join(
        FaceIdentityMap, FaceIdentityMap.face_id == Face.id
    ).filter(FaceIdentityMap.identity_id == identity.id).scalar()

    primary_face = db.query(Face).join(FaceIdentityMap).filter(
        FaceIdentityMap.identity_id == identity.id,
        FaceIdentityMap.is_primary == True
    ).first()
    if not primary_face:
        primary_face = db.query(Face).join(FaceIdentityMap).filter(
            FaceIdentityMap.identity_id == identity.id
        ).first()

    return IdentityResponse(
        identity_id=str(identity.id),
        name=identity.name,
        face_count=face_count or 0,
        created_at=identity.created_at.isoformat(),
        updated_at=identity.updated_at.isoformat(),
        avg_quality_score=float(avg_quality) if avg_quality is not None else 0.0,
        thumbnail_url=primary_face.thumbnail_path if primary_face else None,
    )


def _recompute_centroid(identity_id: int, db: Session) -> None:
    """Recompute and store the centroid for an identity from its member faces."""
    rows = (
        db.query(Face.embedding_vec)
        .join(FaceIdentityMap, FaceIdentityMap.face_id == Face.id)
        .filter(FaceIdentityMap.identity_id == identity_id)
        .filter(Face.embedding_vec.isnot(None))
        .all()
    )
    if not rows:
        return
    vecs = [np.asarray(r.embedding_vec, dtype=np.float32) for r in rows]
    centroid = np.mean(vecs, axis=0).astype(np.float32)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm
    db.query(Identity).filter(Identity.id == identity_id).update(
        {"centroid_embedding": centroid.tolist()}
    )


@router.get("", response_model=IdentityListResponse)
async def list_identities(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    min_faces: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """List all identities with pagination."""
    query = db.query(Identity)

    if min_faces:
        query = query.join(FaceIdentityMap).group_by(Identity.id).having(
            func.count(FaceIdentityMap.id) >= min_faces
        )

    total = query.count()
    offset = (page - 1) * page_size
    identities = query.offset(offset).limit(page_size).all()

    results = [_identity_response(ident, db) for ident in identities]

    return IdentityListResponse(
        identities=results,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{identity_id}", response_model=IdentityResponse)
async def get_identity(identity_id: int, db: Session = Depends(get_db)):
    """Get details of a specific identity."""
    identity = db.query(Identity).filter(Identity.id == identity_id).first()
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")
    return _identity_response(identity, db)


@router.get("/{identity_id}/faces", response_model=List[FaceResponse])
async def get_identity_faces(
    identity_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List all faces belonging to an identity."""
    identity = db.query(Identity).filter(Identity.id == identity_id).first()
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")

    offset = (page - 1) * page_size
    mappings = (
        db.query(FaceIdentityMap)
        .filter(FaceIdentityMap.identity_id == identity_id)
        .order_by(FaceIdentityMap.similarity_to_centroid.desc().nullslast())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    results = []
    for m in mappings:
        face = db.query(Face).filter(Face.id == m.face_id).first()
        if not face:
            continue
        image = face.image
        results.append(FaceResponse(
            face_id=face.id,
            embedding_id=face.embedding_id,
            similarity_to_centroid=m.similarity_to_centroid,
            quality_score=face.quality_score,
            thumbnail_path=face.thumbnail_path,
            file_path=image.file_path if image else None,
            is_primary=bool(m.is_primary),
        ))

    return results


@router.put("/{identity_id}/name")
async def rename_identity(
    identity_id: int,
    req: RenameRequest,
    db: Session = Depends(get_db),
):
    """Rename an identity (assign a human-readable name)."""
    identity = db.query(Identity).filter(Identity.id == identity_id).first()
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")

    identity.name = req.name.strip()
    identity.label = req.name.strip()
    db.commit()

    return {"identity_id": identity_id, "name": identity.name}


@router.post("/merge")
async def merge_identities(req: MergeRequest, db: Session = Depends(get_db)):
    """Merge source identities into a target identity.

    All face mappings from source identities are reassigned to the target.
    Source identities are deleted after merge. The target centroid is
    recomputed from the combined face set.
    """
    target = db.query(Identity).filter(Identity.id == req.target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target identity not found")

    if req.target_id in req.source_ids:
        raise HTTPException(status_code=400, detail="Target cannot be in source_ids")

    sources = db.query(Identity).filter(Identity.id.in_(req.source_ids)).all()
    found_ids = {s.id for s in sources}
    missing = set(req.source_ids) - found_ids
    if missing:
        raise HTTPException(status_code=404, detail=f"Source identities not found: {missing}")

    moved = 0
    for source in sources:
        count = (
            db.query(FaceIdentityMap)
            .filter(FaceIdentityMap.identity_id == source.id)
            .update({"identity_id": req.target_id, "assigned_by": "merge"})
        )
        moved += count
        db.delete(source)

    _recompute_centroid(req.target_id, db)

    # Re-mark primary face as the one closest to new centroid
    db.execute(text(
        "UPDATE face_identity_map SET is_primary = FALSE "
        "WHERE identity_id = :tid"
    ), {"tid": req.target_id})
    db.execute(text(
        "UPDATE face_identity_map SET is_primary = TRUE "
        "WHERE id = ("
        "  SELECT id FROM face_identity_map "
        "  WHERE identity_id = :tid "
        "  ORDER BY similarity_to_centroid DESC NULLS LAST "
        "  LIMIT 1"
        ")"
    ), {"tid": req.target_id})

    db.commit()

    return {
        "target_id": req.target_id,
        "sources_merged": list(found_ids),
        "faces_moved": moved,
    }


@router.post("/{identity_id}/split")
async def split_identity(
    identity_id: int,
    req: SplitRequest,
    db: Session = Depends(get_db),
):
    """Split faces from an identity into a new identity.

    The specified face_ids are moved out of identity_id into a newly
    created identity. Both the old and new identity centroids are
    recomputed.
    """
    identity = db.query(Identity).filter(Identity.id == identity_id).first()
    if not identity:
        raise HTTPException(status_code=404, detail="Identity not found")

    # Verify all face_ids belong to this identity
    mappings = (
        db.query(FaceIdentityMap)
        .filter(
            FaceIdentityMap.identity_id == identity_id,
            FaceIdentityMap.face_id.in_(req.face_ids),
        )
        .all()
    )
    found_face_ids = {m.face_id for m in mappings}
    missing = set(req.face_ids) - found_face_ids
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Faces not in this identity: {missing}",
        )

    # Don't allow splitting ALL faces — that would empty the source identity
    total_faces = db.query(func.count(FaceIdentityMap.id)).filter(
        FaceIdentityMap.identity_id == identity_id
    ).scalar()
    if len(req.face_ids) >= total_faces:
        raise HTTPException(
            status_code=400,
            detail="Cannot split all faces — would leave source identity empty. Use rename instead.",
        )

    # Create new identity
    max_cluster_id = db.execute(
        text("SELECT COALESCE(MAX(cluster_id), -1) FROM identities")
    ).scalar() or -1
    new_identity = Identity(
        name=req.new_identity_name,
        label=req.new_identity_name,
        cluster_id=max_cluster_id + 1,
        is_verified=False,
    )
    db.add(new_identity)
    db.flush()

    # Move the face mappings
    (
        db.query(FaceIdentityMap)
        .filter(
            FaceIdentityMap.identity_id == identity_id,
            FaceIdentityMap.face_id.in_(req.face_ids),
        )
        .update(
            {"identity_id": new_identity.id, "assigned_by": "split"},
            synchronize_session="fetch",
        )
    )

    # Recompute centroids for both identities
    _recompute_centroid(identity_id, db)
    _recompute_centroid(new_identity.id, db)

    # Re-mark primary faces for both
    for iid in [identity_id, new_identity.id]:
        db.execute(text(
            "UPDATE face_identity_map SET is_primary = FALSE "
            "WHERE identity_id = :iid"
        ), {"iid": iid})
        db.execute(text(
            "UPDATE face_identity_map SET is_primary = TRUE "
            "WHERE id = ("
            "  SELECT id FROM face_identity_map "
            "  WHERE identity_id = :iid "
            "  ORDER BY similarity_to_centroid DESC NULLS LAST "
            "  LIMIT 1"
            ")"
        ), {"iid": iid})

    db.commit()

    return {
        "source_identity_id": identity_id,
        "new_identity_id": new_identity.id,
        "faces_moved": len(req.face_ids),
        "new_identity_name": new_identity.name,
    }
