import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from src.db.backup import BackupConfig, BackupError, run_due_backups
from src.db.connection import check_db_connectivity, get_analyzer_pool, get_collector_pool
from src.pipeline.incremental_runner import (
    run_incremental,
    run_full_resolution,
    get_last_run_time,
    clear_orphaned_run_locks,
)
from src.pipeline.run_reporting import production_run_types, probe_phase_names
from src.notifications.alerts import (
    notify_collector_health, notify_daily_digest, notify_merge_candidate,
    notify_status,
)
from src.merge_candidates import merge_candidate_notify_min_confidence

logger = logging.getLogger(__name__)

_running = False
_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE = merge_candidate_notify_min_confidence()


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("Invalid %s; using default %d", name, default)
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _backup_window_open(now: datetime, backup_hour_utc: int) -> bool:
    return now.astimezone(timezone.utc).hour >= backup_hour_utc


async def _run_db_backup_check(now: datetime) -> None:
    try:
        config = BackupConfig.from_env()
        result = await asyncio.to_thread(run_due_backups, config, now=now)
    except BackupError as exc:
        logger.error("Analyzer DB backup failed: %s", exc)
        return
    except Exception:
        logger.exception("Analyzer DB backup failed")
        return

    if result.created:
        logger.info(
            "Analyzer DB backup created: %s",
            ", ".join(str(path) for path in result.created),
        )
    if result.deleted:
        logger.info(
            "Analyzer DB backup retention pruned: %s",
            ", ".join(str(path) for path in result.deleted),
        )


def _collector_no_run_issue(
    source: str,
    last_run: datetime | None,
    interval_hours: int | None,
    now: datetime,
) -> dict | None:
    """Return a collector quiet issue only after its configured cadence is overdue."""
    if not last_run:
        return {"source": source, "message": "no successful scheduled run yet"}
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    cadence = max(1.0, float(interval_hours or 24))
    grace = max(1.0, cadence * 0.10)
    hours_ago = (now - last_run).total_seconds() / 3600
    if hours_ago <= cadence + grace:
        return None
    overdue = max(0.0, hours_ago - cadence)
    return {
        "source": source,
        "message": (
            f"overdue by {overdue:.0f}h "
            f"(last run {hours_ago:.0f}h ago, cadence {cadence:.0f}h)"
        ),
    }


async def _fetch_failing_production_phases(conn) -> list[str]:
    """Return phases whose latest production run status is failed.

    Probe/manual run types are kept in run history, but production health should
    be based on real scheduled run types only. The phase filter keeps synthetic
    forced-failure probes from warning forever even if a probe row is latest.
    """
    rows = await conn.fetch("""
        WITH latest AS (
            SELECT DISTINCT ON (phase) phase, status
            FROM run_phase_status
            WHERE run_type = ANY($1::text[])
              AND phase <> ALL($2::text[])
            ORDER BY phase, created_at DESC
        )
        SELECT phase FROM latest WHERE status = 'failed'
        ORDER BY phase
    """, production_run_types(), probe_phase_names())
    return [r["phase"] for r in rows]


async def _check_collector_health() -> list[dict]:
    try:
        collector = get_collector_pool()
        async with collector.acquire() as conn:
            rows = await conn.fetch("""
                WITH runs AS (
                    SELECT source,
                           MAX(started_at) AS last_run,
                           COUNT(*) FILTER (WHERE status = 'failed'
                               AND started_at > NOW() - INTERVAL '24 hours') AS failed_24h
                    FROM collection_runs
                    GROUP BY source
                )
                SELECT s.source,
                       COALESCE(s.last_run, r.last_run) AS last_run,
                       s.interval_hours,
                       COALESCE(r.failed_24h, 0)::int AS failed_24h
                FROM collection_schedules s
                LEFT JOIN runs r ON r.source = s.source
                WHERE s.enabled
            """)
        issues = []
        now = datetime.now(timezone.utc)
        for r in rows:
            quiet_issue = _collector_no_run_issue(
                r["source"], r["last_run"], r["interval_hours"], now
            )
            if quiet_issue:
                issues.append(quiet_issue)
            if r["failed_24h"] and r["failed_24h"] >= 3:
                issues.append({
                    "source": r["source"],
                    "message": f"{r['failed_24h']} failures in 24h",
                })
        return issues
    except Exception:
        logger.debug("Collector health check failed", exc_info=True)
        return []


async def _build_daily_digest() -> dict:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity_count = await conn.fetchval("SELECT COUNT(*) FROM entities")
        primary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'primary'")
        secondary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'secondary'")
        alerts_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM alerts WHERE detected_at > NOW() - INTERVAL '24 hours'"
        )
        unread = await conn.fetchval(
            "SELECT COUNT(*) FROM alerts WHERE is_read = false"
        )
        events_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM timeline_events WHERE created_at > NOW() - INTERVAL '24 hours'"
        )
        runs_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM analysis_runs "
            "WHERE started_at > NOW() - INTERVAL '24 hours' "
            "AND run_type = ANY($1::text[])",
            production_run_types(),
        )
        failed_runs = await conn.fetchval("""
            SELECT COUNT(*) FROM analysis_runs
            WHERE started_at > NOW() - INTERVAL '24 hours' AND status = 'failed'
              AND run_type = ANY($1::text[])
        """, production_run_types())
        most_active = await conn.fetchrow("""
            SELECT e.canonical_name, COUNT(*) AS cnt
            FROM timeline_events te
            JOIN entities e ON te.entity_id = e.id
            WHERE te.created_at > NOW() - INTERVAL '24 hours'
            GROUP BY e.canonical_name
            ORDER BY cnt DESC LIMIT 1
        """)
        try:
            faces_linked = await conn.fetchval("SELECT COUNT(*) FROM entity_faces")
        except Exception:
            faces_linked = 0
        try:
            failing_phases = await _fetch_failing_production_phases(conn)
        except Exception:
            failing_phases = []

    collector_issues = await _check_collector_health()

    return {
        "entity_count": entity_count,
        "primary": primary,
        "secondary": secondary,
        "faces_linked": faces_linked or 0,
        "alerts_24h": alerts_24h,
        "unread": unread,
        "events_24h": events_24h,
        "runs_24h": runs_24h,
        "failed_runs": failed_runs,
        "failing_phases": failing_phases,
        "most_active": f"{most_active['canonical_name']} ({most_active['cnt']} events)"
            if most_active else None,
        "collectors_down": [i["source"] for i in collector_issues],
    }


async def _build_status() -> dict:
    """Compact current-state snapshot for the periodic status heartbeat.

    Reports ACCURATE live state: entity tier breakdown, faces DETECTED vs
    actually LINKED to entities (these differ by orders of magnitude — most
    detected faces are untracked web/search media), whether a run is in progress
    (with heartbeat freshness) vs the last completed run, any pipeline phases
    currently failing (run_phase_status), and quiet collector sources.
    """
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        entity_count = await conn.fetchval("SELECT COUNT(*) FROM entities")
        primary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'primary'")
        secondary = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE tier = 'secondary'")
        alerts_24h = await conn.fetchval(
            "SELECT COUNT(*) FROM alerts WHERE detected_at > NOW() - INTERVAL '24 hours'"
        )
        unread = await conn.fetchval("SELECT COUNT(*) FROM alerts WHERE is_read = false")
        # Faces live in the facetracker schema (merge); tolerate it being absent.
        # DETECTED = all faces in the corpus; LINKED = faces actually bridged to a
        # tracked entity (public.entity_faces). Reporting only the former is
        # misleading (it's dominated by untracked search/website media).
        try:
            faces_detected = await conn.fetchval("SELECT COUNT(*) FROM facetracker.faces")
        except Exception:
            faces_detected = 0
        try:
            faces_linked = await conn.fetchval("SELECT COUNT(*) FROM entity_faces")
        except Exception:
            faces_linked = 0

        # Current in-progress run (if any) + its heartbeat freshness.
        running = await conn.fetchrow(
            "SELECT run_type, started_at, heartbeat_at FROM analysis_runs "
            "WHERE status = 'running' "
            "AND run_type = ANY($1::text[]) "
            "ORDER BY started_at DESC LIMIT 1",
            production_run_types(),
        )
        last = await conn.fetchrow(
            "SELECT run_type, finished_at FROM analysis_runs "
            "WHERE status = 'completed' AND finished_at IS NOT NULL "
            "AND run_type IN ('incremental', 'full_resolution') "
            "ORDER BY finished_at DESC LIMIT 1"
        )
        operational_failure_patterns = [
            "Interrupted by scheduler restart%",
            "Stale run lock%",
            "Stale lock%",
            "orphaned run lock%",
        ]
        failed_counts = await conn.fetchrow(
            """
            WITH failed AS (
                SELECT f.id, f.run_type, f.finished_at, f.error_message,
                       COALESCE(f.error_message, '') ILIKE ANY($2::text[]) AS operational,
                       EXISTS (
                           SELECT 1
                           FROM analysis_runs s
                           WHERE s.status = 'completed'
                             AND s.run_type = f.run_type
                             AND s.finished_at > f.finished_at
                       ) AS recovered
                FROM analysis_runs f
                WHERE f.status = 'failed'
                  AND f.finished_at > NOW() - INTERVAL '24 hours'
                  AND f.run_type = ANY($1::text[])
            )
            SELECT
                COUNT(*) FILTER (
                    WHERE NOT operational AND NOT recovered
                )::int AS actionable,
                COUNT(*) FILTER (
                    WHERE NOT operational AND recovered
                )::int AS recovered,
                COUNT(*) FILTER (WHERE operational)::int AS operational
            FROM failed
            """,
            production_run_types(),
            operational_failure_patterns,
        )
        recent_failed = await conn.fetch(
            """
            SELECT run_type, finished_at, LEFT(COALESCE(error_message, 'no error captured'), 240) AS error_message
            FROM analysis_runs f
            WHERE f.status = 'failed'
              AND f.finished_at > NOW() - INTERVAL '24 hours'
              AND f.run_type = ANY($1::text[])
              AND NOT (COALESCE(f.error_message, '') ILIKE ANY($2::text[]))
              AND NOT EXISTS (
                  SELECT 1
                  FROM analysis_runs s
                  WHERE s.status = 'completed'
                    AND s.run_type = f.run_type
                    AND s.finished_at > f.finished_at
              )
            ORDER BY finished_at DESC
            LIMIT 3
            """,
            production_run_types(),
            operational_failure_patterns,
        )
        recovered_failed = await conn.fetch(
            """
            SELECT f.run_type, f.finished_at,
                   MIN(s.finished_at) AS recovered_at,
                   LEFT(COALESCE(f.error_message, 'no error captured'), 160) AS error_message
            FROM analysis_runs f
            JOIN analysis_runs s
              ON s.status = 'completed'
             AND s.run_type = f.run_type
             AND s.finished_at > f.finished_at
            WHERE f.status = 'failed'
              AND f.finished_at > NOW() - INTERVAL '24 hours'
              AND f.run_type = ANY($1::text[])
              AND NOT (COALESCE(f.error_message, '') ILIKE ANY($2::text[]))
            GROUP BY f.run_type, f.finished_at, f.error_message
            ORDER BY f.finished_at DESC
            LIMIT 3
            """,
            production_run_types(),
            operational_failure_patterns,
        )
        # Pipeline phases whose most-recent run failed (P2-3 run_phase_status).
        try:
            failing_phases = await _fetch_failing_production_phases(conn)
        except Exception:
            failing_phases = []

    now = datetime.now(timezone.utc)
    run_state = None
    if running:
        mins = int((now - running["started_at"]).total_seconds() // 60)
        hb = running["heartbeat_at"]
        hb_age = int((now - hb).total_seconds()) if hb else None
        hb_str = f", heartbeat {hb_age}s ago" if hb_age is not None else ""
        run_state = f"running {running['run_type']} ({mins}m{hb_str})"
    elif last and last["finished_at"]:
        run_state = f"idle · last {last['run_type']} @ {last['finished_at'].strftime('%Y-%m-%d %H:%M UTC')}"

    issues = await _check_collector_health()
    return {
        "db_ok": True,
        "entity_count": entity_count or 0,
        "primary": primary or 0,
        "secondary": secondary or 0,
        "faces_detected": faces_detected or 0,
        "faces_linked": faces_linked or 0,
        "alerts_24h": alerts_24h or 0,
        "unread": unread or 0,
        "run_state": run_state,
        "failed_runs_24h": (failed_counts["actionable"] if failed_counts else 0) or 0,
        "recovered_failed_runs_24h": (failed_counts["recovered"] if failed_counts else 0) or 0,
        "interrupted_runs_24h": (failed_counts["operational"] if failed_counts else 0) or 0,
        "recent_failed_runs": [dict(r) for r in recent_failed],
        "recent_recovered_failed_runs": [dict(r) for r in recovered_failed],
        "failing_phases": failing_phases,
        "collectors_down": [i["source"] for i in issues],
    }


async def _check_merge_candidates():
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.entity_id, s.signal_type, s.value, s.confidence,
                   s.source_platform, s.target_platform,
                   e.canonical_name
            FROM identity_signals s
            JOIN entities e ON s.entity_id = e.id
            WHERE s.created_at > NOW() - INTERVAL '2 hours'
              AND s.confidence >= $1
        """, _MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE)

        seen = set()
        for r in rows:
            entity_id = str(r["entity_id"])
            if entity_id in seen:
                continue
            seen.add(entity_id)

            other_entities = await conn.fetch("""
                SELECT DISTINCT e2.canonical_name, e2.id
                FROM identity_signals s2
                JOIN entities e2 ON s2.entity_id = e2.id
                WHERE s2.value = $1 AND s2.signal_type = $2
                  AND s2.entity_id != $3
            """, r["value"], r["signal_type"], r["entity_id"])

            for other in other_entities:
                await notify_merge_candidate(
                    r["canonical_name"] or "Unknown",
                    other["canonical_name"] or "Unknown",
                    r["confidence"] / 100,
                    r["signal_type"],
                )


async def start_scheduler() -> None:
    global _running
    _running = True

    # A fresh scheduler process is the only scheduler — clear any 'running' lock
    # orphaned by a predecessor killed mid-run (container recreate), so we don't
    # skip runs for up to 30 min waiting on the stale-lock timer.
    try:
        await clear_orphaned_run_locks()
    except Exception:
        logger.exception("Failed clearing orphaned run locks at startup (non-fatal)")

    interval = int(os.getenv("INCREMENTAL_RUN_INTERVAL_MINUTES", "60")) * 60
    full_interval = timedelta(hours=int(os.getenv("FULL_RESOLUTION_INTERVAL_HOURS", "12")))
    digest_hour = int(os.getenv("DAILY_DIGEST_HOUR", "8"))
    # Recurring status heartbeat to the group chat (hours; 0 disables).
    status_interval_h = int(os.getenv("STATUS_HEARTBEAT_INTERVAL_HOURS", "6"))
    backup_enabled = _env_flag("ANALYZER_DB_BACKUP_ENABLED")
    backup_hour = _env_int("ANALYZER_DB_BACKUP_HOUR_UTC", 2, minimum=0, maximum=23)
    backup_check_interval = _env_int(
        "ANALYZER_DB_BACKUP_CHECK_INTERVAL_SECONDS",
        3600,
        minimum=300,
    )

    logger.info("Scheduler started: incremental every %d min, full resolution every %s, "
                "digest at %02d:00 UTC, status heartbeat every %dh, DB backups %s",
                interval // 60, full_interval, digest_hour, status_interval_h,
                "enabled" if backup_enabled else "disabled")

    last_digest_date: str | None = None
    last_health_check: datetime | None = None
    last_status: datetime | None = None
    last_backup_check: datetime | None = None
    was_offline = False

    while _running:
        if not await check_db_connectivity():
            if not was_offline:
                logger.warning("Database unreachable — scheduler pausing until connectivity returns")
                was_offline = True
            await asyncio.sleep(30)
            continue

        if was_offline:
            logger.info("Database connectivity restored — scheduler resuming")
            was_offline = False

        now = datetime.now(timezone.utc)

        # Daily digest
        if now.hour == digest_hour and last_digest_date != now.strftime("%Y-%m-%d"):
            try:
                digest = await _build_daily_digest()
                await notify_daily_digest(digest)
                last_digest_date = now.strftime("%Y-%m-%d")
            except Exception:
                logger.exception("Daily digest failed")

        # Collector health check (every 3 hours)
        if last_health_check is None or (now - last_health_check).total_seconds() > 10800:
            try:
                issues = await _check_collector_health()
                await notify_collector_health(issues)
                last_health_check = now
            except Exception:
                logger.exception("Collector health check failed")

        # Recurring status heartbeat to the group chat. Fires on the first loop
        # (confirms the system is up) then every status_interval_h hours.
        if status_interval_h > 0 and (
            last_status is None
            or (now - last_status).total_seconds() >= status_interval_h * 3600
        ):
            try:
                await notify_status(await _build_status())
                last_status = now
            except Exception:
                logger.exception("Status heartbeat failed")

        if backup_enabled and _backup_window_open(now, backup_hour) and (
            last_backup_check is None
            or (now - last_backup_check).total_seconds() >= backup_check_interval
        ):
            await _run_db_backup_check(now)
            last_backup_check = now

        last_full_run = await get_last_run_time("full_resolution")
        if last_full_run is None or (now - last_full_run) >= full_interval:
            logger.info("Starting full resolution (last run: %s)", last_full_run)
            try:
                await run_full_resolution()
            except Exception:
                logger.exception("Full resolution failed")
        else:
            logger.info("Starting incremental run")
            try:
                await run_incremental()
            except Exception:
                logger.exception("Incremental run failed")

        # Check for merge candidates after each run
        try:
            await _check_merge_candidates()
        except Exception:
            logger.debug("Merge candidate check failed", exc_info=True)

        await asyncio.sleep(interval)


def stop_scheduler() -> None:
    global _running
    _running = False
