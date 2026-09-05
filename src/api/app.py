import asyncio
import logging
import os
import contextlib
import importlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from src.db.connection import init_pools, close_pools
from src.notifications.alerts import notify_startup, notify_shutdown

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_face_api_mount_task: asyncio.Task | None = None
_face_api_mounted: list[str] = []
_deferred_routes_task: asyncio.Task | None = None
_deferred_routes_mounted: list[str] = []

_scheduler_task: asyncio.Task | None = None
_stop_scheduler = None


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    global _scheduler_task, _stop_scheduler, _face_api_mount_task, _deferred_routes_task
    apply_schema = os.getenv("ANALYZER_APPLY_SCHEMA_ON_STARTUP", "1") != "0"
    print("UnifiedAnalyzer startup: initializing DB pools", flush=True)
    await init_pools(apply_schema_ddl=apply_schema)
    print("UnifiedAnalyzer startup: DB pools initialized", flush=True)
    # The scheduler runs the heavy Phase-6 pipeline (cv2 / ffmpeg / pypdf) whose
    # blocking C calls would freeze this uvicorn event loop for the whole run,
    # making the dashboard unresponsive. So it now runs as a SEPARATE process
    # (the `scheduler` compose service / `python -m src.main scheduler`). Set
    # RUN_SCHEDULER=1 to co-host it in-process (single-process / dev fallback).
    if os.getenv("RUN_SCHEDULER", "0") == "1":
        from src.scheduler.scheduler import start_scheduler, stop_scheduler
        _stop_scheduler = stop_scheduler
        _scheduler_task = asyncio.create_task(start_scheduler())
        logging.getLogger(__name__).info("UnifiedAnalyzer started (scheduler in-process)")
    else:
        logging.getLogger(__name__).info("UnifiedAnalyzer started (API only; scheduler is a separate process)")
    print("UnifiedAnalyzer startup: scheduling startup notification", flush=True)

    async def _notify_startup_fail_open() -> None:
        try:
            await asyncio.wait_for(notify_startup(), timeout=float(os.getenv("ANALYZER_STARTUP_NOTIFY_TIMEOUT_SECONDS", "10")))
        except Exception:
            logging.getLogger(__name__).warning("startup notification failed or timed out", exc_info=True)

    asyncio.create_task(_notify_startup_fail_open())
    if _deferred_routes_task is None:
        async def _mount_deferred_routes_fail_open() -> None:
            mounted: list[str] = []
            for module_path, prefix in _DEFERRED_ROUTE_MODULES:
                try:
                    router = await asyncio.to_thread(_load_router, module_path)
                    if prefix is None:
                        app.include_router(router)
                    else:
                        app.include_router(router, prefix=prefix)
                    mounted.append(module_path)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "deferred API route failed to mount: %s",
                        module_path,
                        exc_info=True,
                    )
            _deferred_routes_mounted[:] = mounted
            _ensure_spa_fallback_last()

        _deferred_routes_task = asyncio.create_task(_mount_deferred_routes_fail_open())
    if _face_api_mount_task is None:
        async def _mount_face_api_fail_open() -> None:
            try:
                from src.api.face_mount import mount_face_api

                mounted = await asyncio.to_thread(mount_face_api, app)
                _face_api_mounted[:] = mounted
                _ensure_spa_fallback_last()
            except Exception:
                logging.getLogger(__name__).warning("face API mount failed or timed out", exc_info=True)

        _face_api_mount_task = asyncio.create_task(_mount_face_api_fail_open())
    print("UnifiedAnalyzer startup: complete", flush=True)

    yield

    with contextlib.suppress(Exception):
        await asyncio.wait_for(notify_shutdown(), timeout=float(os.getenv("ANALYZER_SHUTDOWN_NOTIFY_TIMEOUT_SECONDS", "10")))
    if _stop_scheduler:
        _stop_scheduler()
    if _scheduler_task:
        _scheduler_task.cancel()
    await close_pools()


app = FastAPI(title="UnifiedAnalyzer", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:8001",
        "http://localhost:8002",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8001",
        "http://127.0.0.1:8002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _include_router(module_path: str, *, prefix: str | None = "/api") -> str:
    router = _load_router(module_path)
    if prefix is None:
        app.include_router(router)
    else:
        app.include_router(router, prefix=prefix)
    return module_path


def _load_router(module_path: str):
    module = importlib.import_module(module_path)
    return getattr(module, "router")


def _ensure_spa_fallback_last() -> None:
    """The SPA catch-all (/{full_path:path}) is registered at import time, so any
    router mounted LATER (deferred routes + face API) is appended AFTER it and
    shadowed — Starlette matches in list order and the catch-all 404s every
    /api/* path. Re-append the fallback so real API routes always match first."""
    routes = app.router.routes
    fallback = [r for r in routes if getattr(r, "path", None) == "/{full_path:path}"]
    for r in fallback:
        routes.remove(r)
        routes.append(r)


_CORE_ROUTE_MODULES: tuple[tuple[str, str | None], ...] = (
    ("src.api.routes.health", "/api"),
    ("src.api.routes.export", "/api"),
    ("src.api.routes.collector_health", "/api"),
    ("src.api.routes.data_quality", "/api"),
    ("src.api.routes.readiness", "/api"),
    # Analyst workflow routes must be mounted before the SPA fallback. These are
    # core production UX surfaces and are light enough to import during startup.
    ("src.api.routes.entities", "/api"),
    ("src.api.routes.triage", "/api"),
    ("src.api.routes.cases", "/api"),
    ("src.api.routes.entity_actions", "/api"),
)

_DEFERRED_ROUTE_MODULES: tuple[tuple[str, str | None], ...] = (
    ("src.api.routes.timeline", "/api"),
    ("src.api.routes.alerts", "/api"),
    ("src.api.routes.behavior", "/api"),
    ("src.api.routes.graph", "/api"),
    ("src.api.routes.intelligence", "/api"),
    ("src.api.routes.metrics", "/api"),
    ("src.api.routes.media", "/api"),
    ("src.api.routes.changelog", "/api"),
    ("src.api.routes.intersections", "/api"),
    ("src.api.routes.face_search", "/api"),
    ("src.api.routes.eval", "/api"),
    ("src.api.routes.multilingual", "/api"),
    ("src.api.routes.search", None),
    ("src.api.websocket", None),
)

for module_path, prefix in _CORE_ROUTE_MODULES:
    _include_router(module_path, prefix=prefix)

# Face engine API (merge Stage 4 — docs/facetracker_merge_plan.md §7) is mounted
# after core startup. Face route imports load FAISS/OpenCV and can take tens of
# seconds on this host, so core health/readiness must not wait on them.


# Serve the frontend build (single-page app). Static assets are served directly;
# any OTHER GET path falls back to index.html so client-side routes (/help,
# /entities/…, /review) work on refresh / deep-link instead of 404ing (the old
# StaticFiles(html=True) mount only served index.html for "/"). API routers and
# the /api/face + /assets mounts are all registered ABOVE, so they take
# precedence over this fallback.
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    _assets_dir = frontend_dist / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")
    _index_html = frontend_dist / "index.html"

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        # Never mask API/websocket paths with the HTML shell.
        if full_path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404, detail="Not found")
        # A real file (favicon.svg, etc.) → serve it; otherwise the SPA shell.
        candidate = frontend_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_index_html))
