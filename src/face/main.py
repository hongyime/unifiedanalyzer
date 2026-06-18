"""Main FastAPI application for Face Tracker."""

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from src.face.config import settings, get_settings
from src.face.utils.logging import setup_logging, get_logger
from src.face.storage.database import get_database, Base
from src.face.storage.faiss_index import BatchedFAISSIndex
from src.face.storage.outbox import FaissReaper, FaissOutbox  # noqa: F401  (FaissOutbox import ensures table is registered with Base)
from src.face.engine.detector import FaceDetector
from src.face.pipeline.processor import PipelineProcessor
from src.face.discovery.manifest import FileManifestManager
from src.face.discovery.watcher import FileWatcher
from src.face.discovery.manager import IndexingManager
from src.face.api.routes import search, identity, stats, files
from src.face.api.auth import require_token
from pathlib import Path

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown.

    Lifecycle order matters:

      startup:  wait-for-postgres -> db -> faiss -> reaper -> processor -> indexing manager
      shutdown: indexing manager -> reaper -> db

    The reaper is started BEFORE the indexing manager so any face the
    pipeline writes is drained immediately. It is stopped AFTER the
    indexing manager so the last few outbox rows the pipeline emitted
    are flushed before we exit.
    """
    # Startup
    logger.info("Starting Face Tracker API...")

    # Wait for postgres to be reachable before initialising anything.
    # On machine boot the DB container may still be starting; this blocks
    # quietly (one log line every 30s) instead of crash-looping.
    from src.face.utils.connectivity import wait_for_postgres_startup
    if not wait_for_postgres_startup(settings.database_url, timeout=600):
        logger.error("Postgres never became reachable — aborting startup")
        raise RuntimeError("Postgres unavailable after startup timeout")

    setup_logging(settings.log_level)

    # 0. OneDrive eviction-daemon health check.
    #
    # OneDrive Files-On-Demand: when the container reads a cloud-only file,
    # the bytes get hydrated to local C: cache. Without an eviction daemon
    # those bytes stay forever and C: bloats unbounded. The Windows-host
    # daemon (scripts/onedrive_evict.ps1) polls images.onedrive_revert_pending
    # and runs `attrib +U -P` to flag files for re-eviction.
    #
    # Boot-time check: if there are >0 pending rows AND the eviction log shows
    # no run in the last 6h, log a warning. We don't refuse to boot — the
    # daemon may legitimately be late (host rebooted, schtasks missed a run)
    # and we don't want a transient daemon failure to take down the API.
    #
    # Set FACETRACKER_DISABLE_ONEDRIVE_HEALTHCHECK=1 to suppress this entirely.
    import os as _os
    if _os.environ.get("FACETRACKER_DISABLE_ONEDRIVE_HEALTHCHECK", "").strip() != "1":
        try:
            _evict_log = "/mnt/c/facetracker/logs/onedrive_evict.log"
            _stale = True
            if _os.path.exists(_evict_log):
                import time as _time
                _age = _time.time() - _os.path.getmtime(_evict_log)
                _stale = _age > 21600  # 6 hours
            if _stale:
                logger.warning(
                    "OneDrive eviction daemon log is stale or missing "
                    f"(path={_evict_log}). If you're scanning OneDrive, "
                    "schedule scripts/onedrive_evict.ps1 hourly via Task Scheduler "
                    "or C: bloat will accumulate. See docs/onedrive-sidecar-plan.md."
                )
        except Exception as _e:
            logger.debug(f"OneDrive health check skipped: {_e}")

    # 1. Database (engine + SessionLocal)
    db = get_database(settings.database_url)
    db.create_tables()  # creates faiss_outbox via the FaissOutbox import above
    logger.info("Database connected")

    # 2. FAISS index (in-memory + on-disk)
    faiss_index = BatchedFAISSIndex(settings)

    # 3. Outbox reaper — runs whenever the API is up, regardless of whether
    #    the indexing manager is actively scanning. Keeps recently-ingested
    #    faces searchable within `faiss_reaper_poll_ms` of their DB commit.
    reaper = FaissReaper(
        database=db,
        faiss_index=faiss_index,
        poll_interval_ms=settings.faiss_reaper_poll_ms,
        batch_size=settings.faiss_reaper_batch_size,
        stuck_timeout_s=settings.faiss_reaper_stuck_timeout_s,
        max_attempts=settings.faiss_reaper_max_attempts,
    )
    reaper.start()
    app.state.faiss_reaper = reaper
    app.state.faiss_index = faiss_index   # shared ref — search routes must use this, not load from disk
    app.state.detector = FaceDetector()   # single detector instance shared across search routes

    # 4. Pipeline processor — does NOT touch FAISS directly anymore;
    #    writes go through the outbox.
    processor = PipelineProcessor(
        db=db,
        faiss_index=faiss_index,
        thumbnail_cache_path=Path(settings.thumbnail_cache_path)
    )

    # 5. Discovery + indexing manager (workers open per-file Sessions)
    manifest = FileManifestManager(settings)
    # Wire the DB engine so needs_processing() queries the images table
    # (authoritative, restart-resilient) instead of the JSON manifest alone.
    manifest.wire_db(db.engine)
    watcher = FileWatcher(settings)
    app.state.indexing_manager = IndexingManager(
        config=settings,
        processor=processor,
        manifest=manifest,
        watcher=watcher,
        db=db,
    )
    app.state.indexing_manager.start()

    logger.info("Face Tracker API started successfully")

    yield

    # Shutdown
    logger.info("Shutting down Face Tracker API...")

    mgr = getattr(app.state, "indexing_manager", None)
    if mgr is not None:
        try:
            mgr.stop()
        except Exception as e:
            logger.error(f"Error stopping indexing manager: {e}")

    rpr = getattr(app.state, "faiss_reaper", None)
    if rpr is not None:
        try:
            rpr.stop()
        except Exception as e:
            logger.error(f"Error stopping FAISS reaper: {e}")

    db.close()
    logger.info("Database connection closed")


# Create FastAPI application
app = FastAPI(
    title="Face Tracker API",
    description="Private face search engine API",
    version="4.0.0",
    lifespan=lifespan,
)

# Configure CORS
# SECURITY: allow_credentials=True requires origins to remain localhost-only.
# Widening allow_origins (e.g. to a tailnet hostname) with credentials=True
# enables CSRF — review both settings together if origins ever change.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5454", "http://localhost:3000", "http://localhost:8700", "http://localhost:8701", "http://127.0.0.1:5454", "http://127.0.0.1:8700", "http://127.0.0.1:8701"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers — all /api/v1 routes require bearer token when API_TOKEN is set.
# /health is exempt (no prefix, no auth dependency).
_api_deps = [Depends(require_token)]
app.include_router(search.router, prefix="/api/v1", tags=["search"], dependencies=_api_deps)
app.include_router(identity.router, prefix="/api/v1", tags=["identity"], dependencies=_api_deps)
app.include_router(stats.router, prefix="/api/v1", tags=["stats"], dependencies=_api_deps)
app.include_router(files.router, prefix="/api/v1", tags=["files"], dependencies=_api_deps)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Face Tracker API",
        "version": "4.0.0",
        "description": "Private face search engine",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check(request: Request):
    """Real health check — probes DB, FAISS index, drift, and outbox.

    Returns HTTP 200 if all components are healthy, 503 otherwise.
    Designed to be polled by docker healthcheck and ops dashboards.
    Never raises — always returns a structured payload so callers
    can diagnose which component failed.

    Signals surfaced (P1.1 / P4.1):
      - postgres connectivity
      - faiss live_count, staging_count, index_trained
      - db_face_count vs faiss counts → drift (the silent-corruption detector)
      - outbox backlog by status including failed rows
    """
    from fastapi.responses import JSONResponse
    from sqlalchemy import text

    checks: dict = {}
    overall_ok = True
    degraded_reasons: list = []

    # 1. Postgres — lightweight SELECT 1
    try:
        _db = get_database(settings.database_url)
        with _db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as e:
        checks["postgres"] = f"error: {e}"
        overall_ok = False

    # 2. FAISS index — confirm it's loaded, report drift and train state
    db_face_count = None
    try:
        _db2 = get_database(settings.database_url)
        with _db2.engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM faces WHERE embedding_vec IS NOT NULL")
            ).fetchone()
            db_face_count = int(row[0]) if row else 0
    except Exception:
        pass

    try:
        fi = getattr(request.app.state, "faiss_index", None)
        if fi is None:
            checks["faiss"] = "not_initialized"
            overall_ok = False
        else:
            faiss_live = fi.live_count
            faiss_staging = len(fi.staging_ids)
            index_trained = getattr(fi.live_index, "is_trained", True)

            drift = None
            if db_face_count is not None:
                drift = db_face_count - (faiss_live + faiss_staging)

            checks["faiss"] = {
                "status": "ok",
                "live_count": faiss_live,
                "staging_count": faiss_staging,
                "db_face_count": db_face_count,
                "drift": drift,
                "index_trained": index_trained,
            }

            if drift is not None and drift > 0:
                degraded_reasons.append(
                    f"faiss drift={drift} (faces in DB missing from FAISS)"
                )
                overall_ok = False
            if not index_trained:
                degraded_reasons.append(
                    "IVF index untrained — faces not searchable"
                )
                overall_ok = False
    except Exception as e:
        checks["faiss"] = f"error: {e}"
        overall_ok = False

    # 3. Outbox — full status breakdown including failed rows
    try:
        _db3 = get_database(settings.database_url)
        with _db3.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT status, COUNT(*) AS n FROM faiss_outbox GROUP BY status")
            ).fetchall()
            outbox = {r.status: int(r.n) for r in rows}
        checks["outbox"] = outbox

        pending = outbox.get("pending", 0)
        failed = outbox.get("failed", 0)
        if pending > 50000:
            degraded_reasons.append(f"outbox backlog: {pending} pending rows")
            overall_ok = False
        if failed > 0:
            degraded_reasons.append(
                f"outbox has {failed} failed row(s) — faces not in FAISS"
            )
            overall_ok = False
    except Exception as e:
        checks["outbox"] = f"error: {e}"

    if degraded_reasons:
        checks["degraded_reasons"] = degraded_reasons

    status_code = 200 if overall_ok else 503
    return JSONResponse(
        content={"status": "healthy" if overall_ok else "degraded", "checks": checks},
        status_code=status_code,
    )


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.dashboard_port,
        reload=False,
    )
