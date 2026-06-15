"""Connectivity checks for graceful pause/resume on network or DB outage.

Design principle: log ONCE on state transition (up->down, down->up), stay
quiet in between. All background loops should call `wait_for_db()` at the
top of their cycle to block quietly until postgres is reachable.

Machine startup: the lifespan handler calls `wait_for_postgres_startup()`
which blocks until postgres responds, with no error spam — just one INFO
log every 30s so the operator knows it's waiting.
"""

from __future__ import annotations

import threading
import time
import logging

from sqlalchemy import text


logger = logging.getLogger(__name__)


def check_db(engine) -> bool:
    """Non-blocking DB reachability check. Returns True if SELECT 1 succeeds."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def wait_for_postgres_startup(
    database_url: str,
    stop_event: threading.Event | None = None,
    timeout: float = 300,
    poll_interval: float = 5,
) -> bool:
    """Block until postgres is reachable or timeout expires.

    Used at application startup (lifespan) to wait for the DB container
    to finish initializing. Logs once at INFO when waiting starts, once
    every 30s as a heartbeat, and once when connected.

    Returns True if connected, False if timed out or stopped.
    """
    from sqlalchemy import create_engine

    engine = create_engine(database_url, pool_pre_ping=True, pool_size=1)
    start = time.monotonic()
    logged_waiting = False
    last_heartbeat = 0.0

    try:
        while True:
            elapsed = time.monotonic() - start
            if stop_event and stop_event.is_set():
                return False
            if elapsed > timeout:
                logger.error(
                    "Timed out waiting for postgres after %.0fs — giving up", timeout
                )
                return False

            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                if logged_waiting:
                    logger.info(
                        "Postgres is ready (waited %.1fs)", elapsed
                    )
                return True
            except Exception:
                if not logged_waiting:
                    logger.info(
                        "Waiting for postgres at startup — will retry every %.0fs "
                        "(timeout %.0fs)...",
                        poll_interval,
                        timeout,
                    )
                    logged_waiting = True
                elif elapsed - last_heartbeat >= 30:
                    logger.info(
                        "Still waiting for postgres (%.0fs elapsed)...", elapsed
                    )
                    last_heartbeat = elapsed

            if stop_event:
                stop_event.wait(poll_interval)
            else:
                time.sleep(poll_interval)
    finally:
        engine.dispose()


class ConnectivityGuard:
    """Tracks DB-reachable state and provides quiet-wait gating for loops.

    Usage in a background loop::

        guard = ConnectivityGuard(engine)

        while not stop_event.is_set():
            guard.wait_for_db(stop_event)  # blocks quietly if DB is down
            # ... do work ...

    Logging: one INFO when connectivity drops, one INFO when it recovers.
    Nothing in between — no log spam during extended outages.
    """

    def __init__(self, engine, poll_interval: float = 10, label: str = ""):
        self._engine = engine
        self._poll_interval = poll_interval
        self._label = label or "ConnectivityGuard"
        self._was_healthy = True

    def is_healthy(self) -> bool:
        return check_db(self._engine)

    def wait_for_db(
        self,
        stop_event: threading.Event,
        poll_interval: float | None = None,
    ) -> bool:
        """Block until DB is reachable. Returns False if stop_event fired."""
        interval = poll_interval or self._poll_interval

        if self.is_healthy():
            if not self._was_healthy:
                logger.info("[%s] Database connection restored — resuming", self._label)
                self._was_healthy = True
            return True

        if self._was_healthy:
            logger.info(
                "[%s] Database unreachable — pausing until connection is restored "
                "(checking every %.0fs)",
                self._label,
                interval,
            )
            self._was_healthy = False

        last_heartbeat = time.monotonic()
        while not stop_event.is_set():
            stop_event.wait(interval)
            if stop_event.is_set():
                return False
            if self.is_healthy():
                logger.info("[%s] Database connection restored — resuming", self._label)
                self._was_healthy = True
                return True
            now = time.monotonic()
            if now - last_heartbeat >= 120:
                logger.info("[%s] Still waiting for database...", self._label)
                last_heartbeat = now

        return False
