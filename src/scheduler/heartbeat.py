import logging
from pathlib import Path

import anyio


logger = logging.getLogger(__name__)


async def run_scheduler_heartbeat(interval_seconds: float) -> None:
    while True:
        try:
            Path("/tmp/scheduler_heartbeat").touch()
        except OSError as exc:
            logger.warning("Scheduler heartbeat write failed: %s", exc)
        await anyio.sleep(interval_seconds)
