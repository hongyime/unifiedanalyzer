import os
import asyncio
import hashlib
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
from src.pipeline.collector_priority_hints import export_collector_priority_hints
from src.pipeline.identity_truth import promote_spiderfoot_truth
from src.pipeline.indicator_export import (
    export_pending_supabase_indicators,
    extract_indicators_from_text,
    resolve_domain_to_ips,
    upsert_normalized_indicators,
    normalize_indicator,
)
from src.pipeline.exposure_indicators import stage_exposure_findings_as_indicators
from src.pipeline.run_reporting import production_run_types, probe_phase_names
from src.notifications.alerts import (
    notify_collector_health, notify_daily_digest, notify_merge_candidate,
    notify_status,
)
from src.notifications.intelligence import build_intelligence_status
from src.merge_candidates import merge_candidate_notify_min_confidence
from src.util.audit_log import retry_pending_decision_jsonl

logger = logging.getLogger(__name__)

_running = False
_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE = merge_candidate_notify_min_confidence()
# In-memory dedup set for merge-candidate notifications — prevents re-notifying
# the same pair every scheduler cycle. Reset on process restart (acceptable).
_notified_pairs: set[str] = set()


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


def _run_skipped_due_lock(stats) -> bool:
    return isinstance(stats, dict) and stats.get("skipped") is True


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


async def _stage_collector_priority_hints(run_type: str, stats: dict | None) -> dict:
    """Refresh analyzer-owned collector priority hints after successful runs.

    Analyzer owns identity confidence. Collector owns scheduling. This only
    stages hints in the analyzer DB; collector imports them on its own cadence
    without letting analyzer overwrite collector state.
    """
    if not _env_flag("ANALYZER_COLLECTOR_PRIORITY_HINTS_ENABLED", "1"):
        return {"skipped": "disabled"}
    if stats and stats.get("skipped"):
        return {"skipped": "run_skipped"}

    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            report = await export_collector_priority_hints(conn, write=True)
    except Exception as exc:
        logger.warning(
            "Collector priority hint staging failed after %s: %s",
            run_type,
            exc,
            exc_info=True,
        )
        return {"error": str(exc)[:300]}

    summary = {
        "planned": report.planned,
        "written": report.written,
        "skipped": report.skipped,
    }
    logger.info(
        "Collector priority hints staged after %s: planned=%d written=%d skipped=%s",
        run_type,
        report.planned,
        report.written,
        report.skipped,
    )
    return summary


async def _stage_identity_truth_and_indicators(run_type: str, stats: dict | None) -> dict:
    if stats and stats.get("skipped"):
        return {"skipped": "run_skipped"}
    summary: dict[str, object] = {}
    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            if _env_flag("ANALYZER_IDENTITY_TRUTH_ENABLED", "1"):
                summary["truth"] = await promote_spiderfoot_truth(
                    conn,
                    write=True,
                    min_confidence=float(os.getenv("ANALYZER_IDENTITY_TRUTH_MIN_CONFIDENCE", "0.85")),
                    limit=_env_int("ANALYZER_IDENTITY_TRUTH_SCAN_LIMIT", 5000, minimum=100, maximum=50000),
                )
            if _env_flag("ANALYZER_INDICATOR_EXPORT_STAGING_ENABLED", "1"):
                limit = _env_int("ANALYZER_INDICATOR_EXPORT_SCAN_LIMIT", 500, minimum=50, maximum=5000)
                default_region = os.getenv("ANALYZER_DEFAULT_PHONE_REGION", "US")
                written = 0
                domain_values: list[str] = []
                signal_rows = await conn.fetch(
                    """
                    SELECT id::text, source_platform AS source, value
                    FROM identity_signals
                    WHERE value IS NOT NULL AND value <> ''
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                for row in signal_rows:
                    indicators = extract_indicators_from_text(row["value"], default_region=default_region)
                    domain_values.extend(item.normalized_value for item in indicators if item.indicator_type == "domain")
                    written += await upsert_normalized_indicators(
                        conn,
                        indicators,
                        source_family=str(row["source"] or "identity_signals"),
                        evidence_ref={"table": "identity_signals", "id": row["id"]},
                    )

                event_rows = await conn.fetch(
                    """
                    SELECT id::text, source, title, detail
                    FROM timeline_events
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
                for row in event_rows:
                    text = " ".join(part for part in (row["title"], row["detail"]) if part)
                    indicators = extract_indicators_from_text(text, default_region=default_region)
                    domain_values.extend(item.normalized_value for item in indicators if item.indicator_type == "domain")
                    written += await upsert_normalized_indicators(
                        conn,
                        indicators,
                        source_family=str(row["source"] or "timeline_events"),
                        evidence_ref={"table": "timeline_events", "id": row["id"], "source": row["source"]},
                    )

                dns_cap = _env_int("ANALYZER_INDICATOR_DNS_RESOLVE_LIMIT", 25, minimum=0, maximum=250)
                dns_written = 0
                for domain in sorted(set(domain_values))[:dns_cap]:
                    ips = await asyncio.to_thread(resolve_domain_to_ips, domain)
                    indicators = [
                        item for ip in ips
                        if (item := normalize_indicator("ipv4", ip)) is not None
                    ]
                    dns_written += await upsert_normalized_indicators(
                        conn,
                        indicators,
                        source_family="dns",
                        evidence_ref={
                            "table": "normalized_indicators",
                            "domain_hash": hashlib.sha256(domain.encode("utf-8")).hexdigest()[:16],
                        },
                    )
                summary["indicators"] = {
                    "rows_scanned": len(signal_rows) + len(event_rows),
                    "written": written,
                    "dns_domains_checked": min(len(set(domain_values)), dns_cap),
                    "dns_ipv4_written": dns_written,
                }
                if _env_flag("ANALYZER_EXPOSURE_INDICATOR_STAGING_ENABLED", "1"):
                    try:
                        collector_pool = get_collector_pool()
                        async with collector_pool.acquire() as collector_conn:
                            summary["exposure_indicators"] = await stage_exposure_findings_as_indicators(
                                conn,
                                collector_conn,
                                limit=_env_int(
                                    "ANALYZER_EXPOSURE_INDICATOR_SCAN_LIMIT",
                                    1000,
                                    minimum=1,
                                    maximum=10000,
                                ),
                                default_region=default_region,
                            )
                    except Exception as exc:
                        logger.warning("Exposure indicator staging failed: %s", exc, exc_info=True)
                        summary["exposure_indicators"] = {"error": str(exc)[:300]}
                supabase_mode = os.getenv("SUPABASE_INDICATOR_EXPORT_MODE", "disabled").strip().lower()
                if supabase_mode not in {"", "disabled", "off", "0", "false"}:
                    summary["supabase_export"] = await _export_supabase_indicators_until_drained(conn)
    except Exception as exc:
        logger.warning("%s identity truth/indicator staging failed: %s", run_type, exc, exc_info=True)
        return {"error": str(exc)[:300]}
    logger.info("%s identity truth/indicator staging: %s", run_type, summary)
    return summary


async def _export_supabase_indicators_until_drained(conn) -> dict:
    """Drain compact Supabase indicator exports in bounded batches.

    A single scheduled pass can stage hundreds of indicators. Exporting only one
    batch leaves Supabase visibly stale until several later scheduler cycles or a
    manual CLI loop. Keep the operation bounded, but drain normal bursts in one
    self-healing pass.
    """
    batch_size = _env_int("ANALYZER_SUPABASE_EXPORT_BATCH_SIZE", 100, minimum=1, maximum=1000)
    max_batches = _env_int("ANALYZER_SUPABASE_EXPORT_MAX_BATCHES_PER_PASS", 10, minimum=1, maximum=100)
    total_selected = 0
    total_exported = 0
    batches = 0
    last_result: dict | None = None
    drained = False

    for batch_index in range(max_batches):
        result = await export_pending_supabase_indicators(
            conn,
            limit=batch_size,
            ensure_schema=batch_index == 0,
            ensure_schema_when_empty=batch_index == 0,
        )
        batches += 1
        last_result = result
        selected = int(result.get("selected") or 0)
        exported = int(result.get("exported") or 0)
        total_selected += selected
        total_exported += exported

        if result.get("status") != "ok":
            break
        if selected == 0:
            drained = True
            break
        if exported < selected:
            break
        if selected < batch_size:
            drained = True
            break

    return {
        "status": (last_result or {}).get("status", "ok"),
        "mode": (last_result or {}).get("mode"),
        "write_method": (last_result or {}).get("write_method"),
        "payload": (last_result or {}).get("payload", "normalized_indicators_only"),
        "raw_mirror": False,
        "batch_size": batch_size,
        "max_batches": max_batches,
        "batches": batches,
        "selected": total_selected,
        "exported": total_exported,
        "drained": drained,
        "last_batch": last_result or {},
    }


async def _run_decision_outbox_check() -> dict:
    """Retry audit_log rows whose JSONL append failed or was interrupted.

    Human review decisions must not remain DB-only. The dashboard action path
    records JSONL errors on audit_log; this scheduled outbox drains those rows
    without requiring a manual CLI run.
    """
    if not _env_flag("ANALYZER_DECISION_OUTBOX_ENABLED", "1"):
        return {"skipped": "disabled"}
    limit = _env_int("ANALYZER_DECISION_OUTBOX_LIMIT", 100, minimum=1, maximum=5000)
    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            stats = await retry_pending_decision_jsonl(conn, limit=limit)
    except Exception as exc:
        logger.warning("Decision JSONL outbox retry failed: %s", exc, exc_info=True)
        return {"error": str(exc)[:300]}
    if stats.get("pending") or stats.get("failed"):
        logger.info("Decision JSONL outbox retry: %s", stats)
    return stats


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
        intelligence = await build_intelligence_status(conn)

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
        "intelligence": intelligence,
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
        intelligence = await build_intelligence_status(conn)

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
        "intelligence": intelligence,
    }


_MERGE_PUSH_PACE_SECONDS = float(os.getenv("ANALYZER_MERGE_PUSH_PACE_SECONDS", "3.5"))


async def _check_merge_candidates():
    """Query high-confidence same_person_probability pairs from entity_relationships,
    get platform handles + face crops, and push a 2-button Telegram card.
    Skips pairs already notified this process run (_notified_pairs dedup).
    """
    global _notified_pairs
    pool = get_analyzer_pool()
    min_weight = _MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT r.entity_a_id::text AS entity_a,
                   r.entity_b_id::text AS entity_b,
                   r.weight, r.cross_platform, r.sources,
                   ea.canonical_name AS name_a,
                   eb.canonical_name AS name_b
            FROM entity_relationships r
            JOIN entities ea ON r.entity_a_id = ea.id
            JOIN entities eb ON r.entity_b_id = eb.id
            WHERE r.relationship_type = 'same_person_probability'
              AND r.updated_at > NOW() - INTERVAL '2 hours'
              AND COALESCE(
                    CASE WHEN jsonb_typeof(r.sources->'score') = 'number'
                         THEN (r.sources->>'score')::float8 * 100
                    END,
                    r.weight
                  ) >= $1
            ORDER BY r.weight DESC
            LIMIT 20
        """, min_weight)

        if not rows:
            return

        # Collect entity ids to fetch handles + faces in bulk
        ids = []
        for r in rows:
            ids.append(r["entity_a"])
            ids.append(r["entity_b"])
        ids = list(set(ids))

        # Platform handles: source:username
        handle_rows = await conn.fetch("""
            SELECT entity_id::text AS eid, source,
                   COALESCE(NULLIF(platform_username,''), NULLIF(platform_name,''), platform_id) AS handle
            FROM entity_platform_links
            WHERE entity_id = ANY($1::uuid[])
            ORDER BY source
        """, ids)
        handles: dict[str, list[str]] = {}
        for lr in handle_rows:
            label = f"{lr['source']}:{lr['handle']}" if lr["handle"] else lr["source"]
            handles.setdefault(lr["eid"], [])
            if label not in handles[lr["eid"]]:
                handles[lr["eid"]].append(label)

        # Representative face crop (best quality)
        face_rows = await conn.fetch("""
            SELECT DISTINCT ON (ef.entity_id) ef.entity_id::text AS eid, ef.face_id
            FROM entity_faces ef
            JOIN facetracker.faces f ON f.id = ef.face_id
            WHERE ef.entity_id = ANY($1::uuid[])
            ORDER BY ef.entity_id, f.quality_score DESC NULLS LAST
        """, ids)
        faces: dict[str, str] = {r["eid"]: f"/api/face/gallery/faces/{r['face_id']}/crop" for r in face_rows}

    for r in rows:
        id_a, id_b = r["entity_a"], r["entity_b"]
        pair_key = ":".join(sorted([id_a, id_b]))
        if pair_key in _notified_pairs:
            continue

        import json as _json
        try:
            meta = _json.loads(r["sources"]) if isinstance(r["sources"], str) else (r["sources"] or {})
        except Exception:
            meta = {}
        signals = meta.get("contributing_signals", []) or ["same_person_probability"]
        score = meta.get("score")

        candidate = {
            "entity_a": id_a,
            "entity_b": id_b,
            "name_a": r["name_a"],
            "name_b": r["name_b"],
            "handles_a": handles.get(id_a, []),
            "handles_b": handles.get(id_b, []),
            "score": score,
            "cross_platform": bool(r["cross_platform"]),
            "signals": signals,
            "face_a": faces.get(id_a),
            "face_b": faces.get(id_b),
        }
        confidence = float(score or r["weight"] or 0) / 100
        # Deliver-before-mark + dynamic pacing: only remember a pair as notified
        # once it ACTUALLY delivered, so a 429-throttled card retries next cycle
        # instead of being silently lost. Pace between cards to stay under
        # Telegram's ~20 msg/min group limit (send() also honors retry_after).
        ok = await notify_merge_candidate(
            r["name_a"] or "Unknown",
            r["name_b"] or "Unknown",
            confidence,
            signals[0] if signals else "same_person_probability",
            candidate=candidate,
        )
        if ok:
            _notified_pairs.add(pair_key)
        await asyncio.sleep(_MERGE_PUSH_PACE_SECONDS)

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
    decision_outbox_interval = _env_int(
        "ANALYZER_DECISION_OUTBOX_INTERVAL_SECONDS",
        900,
        minimum=60,
    )
    recon_bridge_interval = _env_int(
        "ANALYZER_RECON_BRIDGE_INTERVAL_SECONDS",
        3600,
        minimum=300,
    )
    recon_bridge_limit = _env_int(
        "ANALYZER_RECON_BRIDGE_LIMIT",
        1000,
        minimum=1,
        maximum=10000,
    )
    lock_retry_seconds = _env_int(
        "ANALYZER_SCHEDULER_LOCK_RETRY_SECONDS",
        300,
        minimum=30,
        maximum=interval,
    )

    logger.info("Scheduler started: incremental every %d min, full resolution every %s, "
                "digest at %02d:00 UTC, status heartbeat every %dh, DB backups %s, "
                "decision outbox every %ds",
                interval // 60, full_interval, digest_hour, status_interval_h,
                "enabled" if backup_enabled else "disabled",
                decision_outbox_interval)

    # Telegram merge-review callback poller (single asyncio Task, not a new process)
    _merge_bot_task = None
    if _env_flag("TELEGRAM_MERGE_BOT_ENABLED"):
        try:
            from src.notifications.merge_bot import run_callback_poller
            _merge_bot_task = asyncio.create_task(run_callback_poller(), name="merge_bot_poller")
            logger.info("merge-bot: callback poller task created (TELEGRAM_MERGE_BOT_ENABLED=1)")
        except Exception:
            logger.exception("merge-bot: failed to start callback poller (non-fatal)")
    else:
        logger.info("merge-bot: TELEGRAM_MERGE_BOT_ENABLED not set — callback poller disabled")

    last_digest_date: str | None = None
    last_health_check: datetime | None = None
    last_status: datetime | None = None
    last_backup_check: datetime | None = None
    last_decision_outbox_check: datetime | None = None
    last_recon_bridge_check: datetime | None = None
    was_offline = False

    while _running:
        sleep_seconds = interval
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

        # Recon-observations -> identity_signals bridge. Runs BEFORE the DB
        # backup and other heavy blocks so a long-running pg_dump never starves
        # the auto-bridge — new collector recon findings flow into analyzer
        # identity_signals every ~hour by default. Idempotent (skips already
        # bridged rows via source_record_id).
        if (
            last_recon_bridge_check is None
            or (now - last_recon_bridge_check).total_seconds() >= recon_bridge_interval
        ):
            try:
                from src.pipeline.recon_bridge import bridge_recon_observations
                summary = await bridge_recon_observations(
                    dry_run=False, limit=recon_bridge_limit,
                )
                logger.info("recon_bridge tick: %s", summary)
            except Exception:
                logger.exception("recon_bridge tick failed")
            last_recon_bridge_check = now

        if backup_enabled and _backup_window_open(now, backup_hour) and (
            last_backup_check is None
            or (now - last_backup_check).total_seconds() >= backup_check_interval
        ):
            await _run_db_backup_check(now)
            last_backup_check = now

        if (
            last_decision_outbox_check is None
            or (now - last_decision_outbox_check).total_seconds() >= decision_outbox_interval
        ):
            await _run_decision_outbox_check()
            last_decision_outbox_check = now

        last_full_run = await get_last_run_time("full_resolution")
        if last_full_run is None or (now - last_full_run) >= full_interval:
            logger.info("Starting full resolution (last run: %s)", last_full_run)
            try:
                stats = await run_full_resolution()
                if _run_skipped_due_lock(stats):
                    sleep_seconds = lock_retry_seconds
                    logger.warning(
                        "Full resolution skipped due active run lock; retrying in %ds",
                        sleep_seconds,
                    )
                await _stage_collector_priority_hints("full_resolution", stats)
                await _stage_identity_truth_and_indicators("full_resolution", stats)
            except Exception:
                logger.exception("Full resolution failed")
        else:
            logger.info("Starting incremental run")
            try:
                stats = await run_incremental()
                if _run_skipped_due_lock(stats):
                    sleep_seconds = lock_retry_seconds
                    logger.warning(
                        "Incremental run skipped due active run lock; retrying in %ds",
                        sleep_seconds,
                    )
                await _stage_collector_priority_hints("incremental", stats)
                await _stage_identity_truth_and_indicators("incremental", stats)
            except Exception:
                logger.exception("Incremental run failed")

        # Check for merge candidates after each run
        try:
            await _check_merge_candidates()
        except Exception:
            logger.debug("Merge candidate check failed", exc_info=True)

        await asyncio.sleep(sleep_seconds)


def stop_scheduler() -> None:
    global _running
    _running = False
