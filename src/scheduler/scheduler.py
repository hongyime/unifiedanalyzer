import os
import asyncio
import logging
from datetime import datetime, timezone

from src.pipeline.incremental_runner import run_incremental, run_full_resolution

logger = logging.getLogger(__name__)

_running = False


async def start_scheduler() -> None:
    global _running
    _running = True

    interval = int(os.getenv("INCREMENTAL_RUN_INTERVAL_MINUTES", "60")) * 60
    full_hour = int(os.getenv("FULL_RESOLUTION_HOUR", "3"))

    logger.info("Scheduler started: incremental every %d min, full resolution at %02d:00 UTC",
                interval // 60, full_hour)

    last_full_date: str | None = None

    while _running:
        now = datetime.now(timezone.utc)

        if now.hour == full_hour and last_full_date != now.strftime("%Y-%m-%d"):
            logger.info("Starting nightly full resolution")
            try:
                await run_full_resolution()
                last_full_date = now.strftime("%Y-%m-%d")
            except Exception:
                logger.exception("Full resolution failed")
        else:
            logger.info("Starting incremental run")
            try:
                await run_incremental()
            except Exception:
                logger.exception("Incremental run failed")

        await asyncio.sleep(interval)


def stop_scheduler() -> None:
    global _running
    _running = False
