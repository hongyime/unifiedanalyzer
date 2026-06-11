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

_scheduler_task: asyncio.Task | None = None


@app.on_event("startup")
async def startup():
    global _scheduler_task
    await init_pools()
    _scheduler_task = asyncio.create_task(start_scheduler())
    logging.getLogger(__name__).info("UnifiedAnalyzer started")
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
