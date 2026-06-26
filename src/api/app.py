import asyncio
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from src.db.connection import init_pools, close_pools
from src.scheduler.scheduler import start_scheduler, stop_scheduler
from src.notifications.alerts import notify_startup, notify_shutdown
from src.api.routes.entities import router as entities_router
from src.api.routes.timeline import router as timeline_router
from src.api.routes.alerts import router as alerts_router
from src.api.routes.health import router as health_router
from src.api.routes.entity_actions import router as entity_actions_router
from src.api.routes.behavior import router as behavior_router
from src.api.routes.export import router as export_router
from src.api.routes.collector_health import router as collector_health_router
from src.api.routes.graph import router as graph_router
from src.api.routes.intelligence import router as intelligence_router
from src.api.routes.metrics import router as metrics_router
from src.api.routes.media import router as media_router
from src.api.routes.triage import router as triage_router
from src.api.routes.cases import router as cases_router
from src.api.websocket import router as websocket_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(title="UnifiedAnalyzer", version="0.1.0")

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

app.include_router(entities_router, prefix="/api")
app.include_router(timeline_router, prefix="/api")
app.include_router(alerts_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(entity_actions_router, prefix="/api")
app.include_router(behavior_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(collector_health_router, prefix="/api")
app.include_router(graph_router, prefix="/api")
app.include_router(intelligence_router, prefix="/api")
app.include_router(metrics_router, prefix="/api")
app.include_router(media_router, prefix="/api")
app.include_router(triage_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
# Websocket router mounted at root so the path is exactly /ws/health.
app.include_router(websocket_router)

# Face engine API (merge Stage 4 — docs/facetracker_merge_plan.md §7): mount the
# vendored facetracker routes under /api/face. Guarded so a face-side failure
# never blocks analyzer startup. Returns empty until faces are indexed (B1/R6).
from src.api.face_mount import mount_face_api
mount_face_api(app)

_scheduler_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup():
    global _scheduler_task
    await init_pools()
    # The scheduler runs the heavy Phase-6 pipeline (cv2 / ffmpeg / pypdf) whose
    # blocking C calls would freeze this uvicorn event loop for the whole run,
    # making the dashboard unresponsive. So it now runs as a SEPARATE process
    # (the `scheduler` compose service / `python -m src.main scheduler`). Set
    # RUN_SCHEDULER=1 to co-host it in-process (single-process / dev fallback).
    if os.getenv("RUN_SCHEDULER", "0") == "1":
        _scheduler_task = asyncio.create_task(start_scheduler())
        logging.getLogger(__name__).info("UnifiedAnalyzer started (scheduler in-process)")
    else:
        logging.getLogger(__name__).info("UnifiedAnalyzer started (API only; scheduler is a separate process)")
    asyncio.create_task(notify_startup())


@app.on_event("shutdown")
async def shutdown():
    await notify_shutdown()
    stop_scheduler()
    if _scheduler_task:
        _scheduler_task.cancel()
    await close_pools()


# Serve frontend build if it exists
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
