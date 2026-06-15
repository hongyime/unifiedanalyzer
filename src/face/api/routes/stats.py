"""Stats API routes for dashboard and monitoring."""

from fastapi import APIRouter, Depends, Request, HTTPException
from typing import Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from src.face.config import settings
from src.face.storage.database import get_database, get_db_session, Image, Face, Identity

router = APIRouter(prefix="/stats")

def get_db():
    """Dependency to get database session — request-scoped, pool-safe."""
    yield from get_db_session(settings.database_url)

@router.get("")
async def get_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get overall system statistics."""
    total_faces = db.query(func.count(Face.id)).scalar()
    total_images = db.query(func.count(Image.id)).scalar()
    total_identities = db.query(func.count(Identity.id)).scalar()
    total_videos = db.query(func.count(Image.id)).filter(Image.is_video == True).scalar()
    
    files_processed = db.query(func.count(Image.id)).filter(Image.status == "completed").scalar()
    files_failed = db.query(func.count(Image.id)).filter(Image.status == "failed").scalar()
    
    return {
        "total_faces": total_faces or 0,
        "total_images": total_images or 0,
        "total_identities": total_identities or 0,
        "total_videos": total_videos or 0,
        "indexing": {
            "files_processed": files_processed or 0,
            "files_failed": files_failed or 0,
            "faces_per_image_avg": round(total_faces / total_images, 2) if total_images and total_faces else 0,
        }
    }

@router.get("/scan-progress")
async def get_scan_progress(request: Request) -> Dict[str, Any]:
    """Get current scan progress."""
    indexing_manager = getattr(request.app.state, "indexing_manager", None)
    
    if indexing_manager:
        return indexing_manager.get_progress()
        
    return {
        "is_scanning": False,
        "current_file": None,
        # Legacy aliases (kept for dashboard JS).
        "files_scanned": 0,
        "files_total": 0,
        # Three-counter view (3A).
        "files_discovered": 0,
        "files_queued": 0,
        "files_skipped": 0,
        "files_processed": 0,
        "files_failed": 0,
        "progress_percent": 0,
        "eta_seconds": None,
        "per_drive": {},
        "queue_depth": 0,
        "queue_high_water_mark": 0,
        "processing_rate_per_sec": None,
        "processing_eta_seconds": None,
    }

@router.get("/onedrive")
async def get_onedrive_stats(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """OneDrive ingestion + eviction status.

    Reports:
    - ingested_count: OneDrive files in DB
    - revert_pending: files awaiting host-side `attrib +U -P` (the
                      onedrive_evict.ps1 daemon should drain these hourly)
    - evict_log_age_seconds: how long ago the daemon last ran. None if
                             the log file doesn't exist.
    - audit_log_age_seconds: how long ago the auditor last ran (verifies
                             eviction actually completed; runs every 6h).
    - evict_healthy: True if (pending<=threshold) AND (daemon ran in last 6h)
                     AND (auditor ran in last 30h). False if any signal is
                     stale or pending grew past the threshold (indicates
                     daemon is firing but OneDrive is rejecting evictions).

    Dashboard surfaces unhealthy state as a banner so we don't silently
    accumulate C: drive bloat or let the auditor go dormant.
    """
    from sqlalchemy import or_
    import os as _os
    import time as _time

    # Tunable: number of pending rows beyond which the eviction daemon is
    # presumed unable to keep up. At 17k faces / 14k identities the steady
    # state is ~0; a single ingest wave bumps this temporarily. 100 is a
    # large enough headroom that one batch ingest won't trip the alarm,
    # and small enough that a real failure mode (signals fired but
    # OneDrive rejecting them) shows within ~2 hourly runs.
    PENDING_CEILING = 100
    EVICT_STALE_SECONDS = 21600  # 6h (daemon runs hourly; allow several misses)
    AUDIT_STALE_SECONDS = 108000  # 30h (auditor runs 6-hourly; allow >1 miss)

    ingested = db.query(func.count(Image.id)).filter(
        or_(
            Image.file_path.like("%/OneDrive/%"),
            Image.file_path.like("%/OneDrive - %"),
        )
    ).scalar() or 0

    pending = db.query(func.count(Image.id)).filter(
        Image.onedrive_revert_pending == True  # noqa: E712
    ).scalar() or 0

    def _log_age(path: str):
        if not _os.path.exists(path):
            return None
        try:
            return int(_time.time() - _os.path.getmtime(path))
        except OSError:
            return None

    evict_age = _log_age("/mnt/c/facetracker/logs/onedrive_evict.log")
    audit_age = _log_age("/mnt/c/facetracker/logs/onedrive_audit.log")

    # Compose health signal. Order matters for the message: most actionable
    # failure first.
    reasons = []
    if pending > PENDING_CEILING:
        reasons.append(
            f"pending={pending} exceeds ceiling={PENDING_CEILING} "
            f"(daemon firing but evictions not landing; check OneDrive sync state)"
        )
    if evict_age is None or evict_age > EVICT_STALE_SECONDS:
        reasons.append(
            f"eviction daemon stale (last_run={evict_age}s ago, threshold={EVICT_STALE_SECONDS}s); "
            f"check FacetrackerOneDriveEvict scheduled task"
        )
    if audit_age is None or audit_age > AUDIT_STALE_SECONDS:
        reasons.append(
            f"audit daemon stale (last_run={audit_age}s ago, threshold={AUDIT_STALE_SECONDS}s); "
            f"check FacetrackerOneDriveAudit scheduled task"
        )

    healthy = len(reasons) == 0

    if healthy:
        message = (
            f"OneDrive eviction daemon healthy. {pending} pending, "
            f"evict last_run={evict_age}s ago, audit last_run={audit_age}s ago."
        )
    else:
        message = "WARNING: " + "; ".join(reasons)

    return {
        "ingested_count": int(ingested),
        "revert_pending": int(pending),
        "evict_log_age_seconds": evict_age,
        "audit_log_age_seconds": audit_age,
        "evict_healthy": bool(healthy),
        "pending_ceiling": PENDING_CEILING,
        "message": message,
    }

@router.get("/recent-activity")
async def get_recent_activity(limit: int = 50) -> Dict[str, Any]:
    """Get recent indexing activity.

    NOT IMPLEMENTED — returns HTTP 501 rather than a fabricated empty list.
    """
    raise HTTPException(status_code=501, detail="stats.recent-activity is not implemented yet")


@router.get("/faiss-health")
async def get_faiss_health(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """FAISS index health: drift detection + outbox backlog + IVF train state.

    This is the detector for the one unguarded silent-corruption path in the
    system. The merge path persists the on-disk ids/index files crash-safely,
    BUT if the process dies after live_index.add() and before the ids/index
    are saved, the in-memory vectors are lost while the outbox rows are (or
    will be) marked 'committed'. Committed rows are never re-drained, so those
    faces live in faces.embedding_vec but vanish from FAISS — permanently
    unsearchable, with nothing watching. This endpoint surfaces that as drift.

    Signals:
      - db_face_count        : faces with a non-null embedding (should be in FAISS)
      - faiss_live_count     : vectors actually in the live index
      - faiss_ids_count      : id-list length (must equal live_count or recovery trims)
      - drift                : db_face_count - faiss_live_count
                               > 0  => faces missing from FAISS (the corruption path)
                               < 0  => extra FAISS vectors w/o DB rows (orphans)
      - index_trained        : IVF must be trained or adds are postponed and
                               faces silently never become searchable
      - outbox               : status breakdown; 'failed' or large 'pending'/'merging'
                               backlog indicates the reaper is not keeping up
      - healthy              : True only if drift==0, index trained, no failed rows,
                               and ids/live counts agree
      - message              : human-readable summary; actionable failure first

    When unhealthy with drift>0, the remedy is scripts/faiss_rebuild_from_db.py.
    """
    DRIFT_TOLERANCE = 0  # exact match expected; staging is counted separately below

    reaper = getattr(request.app.state, "faiss_reaper", None)
    faiss_index = getattr(reaper, "faiss_index", None) if reaper is not None else None

    # DB side: faces that SHOULD be searchable (have an embedding).
    db_face_count = db.query(func.count(Face.id)).filter(
        Face.embedding_vec.isnot(None)
    ).scalar() or 0

    if faiss_index is None:
        # FAISS not reachable (e.g. reaper not yet attached). Report degraded
        # rather than pretending healthy.
        return {
            "healthy": False,
            "db_face_count": int(db_face_count),
            "faiss_live_count": None,
            "faiss_ids_count": None,
            "faiss_staging_count": None,
            "drift": None,
            "index_trained": None,
            "outbox": {},
            "message": "WARNING: FAISS index not reachable from app.state; "
                       "reaper may not be initialized.",
        }

    # FAISS side. Read these under no lock — they're plain ints/lists and a
    # transient off-by-staging is acceptable for a health probe. live_count
    # and len(live_ids) must agree (the merge invariant); report both.
    faiss_live_count = int(faiss_index.live_count)
    faiss_ids_count = int(len(faiss_index.live_ids))
    faiss_staging_count = int(len(faiss_index.staging_ids))

    # IVF train state. HNSW indexes report is_trained=True always; for IVF an
    # untrained index silently postpones every merge.
    try:
        index_trained = bool(faiss_index.live_index.is_trained)
    except Exception:
        index_trained = True  # non-IVF or no index attr; treat as trained

    # Outbox backlog by status (cheap GROUP BY). Reuse the reaper helper.
    try:
        outbox = reaper.count_by_status()
    except Exception:
        outbox = {}

    # Drift accounting. A face is searchable if it's in live OR staging (staging
    # gets merged on next force_merge). So the corruption signal is:
    #   db_face_count - (faiss_live_count + faiss_staging_count)
    searchable = faiss_live_count + faiss_staging_count
    drift = int(db_face_count - searchable)

    reasons = []
    if drift > DRIFT_TOLERANCE:
        reasons.append(
            f"drift={drift}: {drift} face(s) in DB are missing from FAISS "
            f"(db={db_face_count}, faiss_live+staging={searchable}). "
            f"Run scripts/faiss_rebuild_from_db.py to recover."
        )
    elif drift < -DRIFT_TOLERANCE:
        reasons.append(
            f"drift={drift}: FAISS has {abs(drift)} vector(s) with no DB face "
            f"(orphans). Index may need rebuild."
        )
    if faiss_ids_count != faiss_live_count:
        reasons.append(
            f"id/vector mismatch: ids={faiss_ids_count} live={faiss_live_count} "
            f"(merge crashed mid-write; restart trims ids, or rebuild)."
        )
    if not index_trained:
        reasons.append(
            f"IVF index UNTRAINED: merges are postponed until staging reaches "
            f"the training floor; faces are accumulating but NOT searchable. "
            f"Bootstrap via the IVF migration script."
        )
    failed_rows = int(outbox.get("failed", 0))
    if failed_rows > 0:
        reasons.append(
            f"outbox has {failed_rows} 'failed' row(s) (exceeded max_attempts); "
            f"these faces are not in FAISS. Inspect last_error and re-drive."
        )

    healthy = len(reasons) == 0
    if healthy:
        message = (
            f"FAISS healthy. db={db_face_count} live={faiss_live_count} "
            f"staging={faiss_staging_count} drift=0 trained={index_trained}."
        )
    else:
        message = "WARNING: " + "; ".join(reasons)

    return {
        "healthy": bool(healthy),
        "db_face_count": int(db_face_count),
        "faiss_live_count": faiss_live_count,
        "faiss_ids_count": faiss_ids_count,
        "faiss_staging_count": faiss_staging_count,
        "drift": drift,
        "index_trained": bool(index_trained),
        "outbox": {k: int(v) for k, v in outbox.items()},
        "message": message,
    }
