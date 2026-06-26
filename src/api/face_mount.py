"""Mount the vendored facetracker face API under the analyzer app (Stage 4).

docs/facetracker_merge_plan.md §7: surface the face engine's endpoints under
`/api/face/*` in the unified analyzer API, instead of running facetracker's
standalone FastAPI + operations-dashboard.

The face routers (stats / identity / files / search) use the face engine's
SQLAlchemy layer, which via src/face/config.Settings.database_url now points at
the unified analyzer DB with search_path=facetracker (src/face/storage/database).

CAVEATS / TODO (handoff — next agent):
  * FAISS-backed endpoints (search.*, stats /faiss-health) read app.state
    (faiss_reaper / indexing_manager) that only live in the face_worker
    process, not this API process. Mounted here they degrade gracefully
    (the routes guard with getattr(app.state, ..., None)). Proper fix: a
    shared read-only FAISS handle or a worker RPC.                      # TODO(F4)
  * No faces exist until collector source media is restored (task B1) and the
    re-index (R6) runs — these endpoints return zeros/empty until then.
  * Face API auth (src/face/api/auth.py) is NOT wired here yet.         # TODO(F4)
  * Frontend Faces/Identities page still to be added.                   # TODO(F4)
"""
import logging

logger = logging.getLogger(__name__)

# Face route modules to mount, each contributing a `router` (APIRouter).
# Imported individually so one failing module (e.g. a FAISS-heavy import in
# `search`) does not drop the others.
_FACE_ROUTE_MODULES = ("stats", "identity", "files", "search", "gallery")


def mount_face_api(app, prefix: str = "/api/face") -> list[str]:
    """Include the face routers under `prefix`. Returns the list mounted.

    Guarded end-to-end: any import/wiring failure is logged and swallowed so a
    face-side problem can never block analyzer API startup.
    """
    mounted: list[str] = []
    for name in _FACE_ROUTE_MODULES:
        try:
            mod = __import__(f"src.face.api.routes.{name}", fromlist=["router"])
            router = getattr(mod, "router", None)
            if router is None:
                logger.warning("face route module %s has no `router`; skipping", name)
                continue
            app.include_router(router, prefix=prefix, tags=["face"])
            mounted.append(name)
        except Exception:  # pragma: no cover - defensive
            logger.exception("face route module %s failed to mount; skipping", name)
    if mounted:
        logger.info("Face API mounted under %s: %s", prefix, ", ".join(mounted))
    else:
        logger.warning("Face API mounted nothing under %s", prefix)
    return mounted
