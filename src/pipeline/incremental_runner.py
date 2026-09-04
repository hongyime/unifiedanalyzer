import asyncio
import inspect
import json
import logging
import os
import time
from datetime import datetime, timezone

from asyncpg import UniqueViolationError

from src.db.connection import get_analyzer_pool, is_collector_unavailable_error
from src.pipeline.entity_resolver import resolve_entities
from src.pipeline.beeper_bridge import bridge_beeper
from src.pipeline.facebook_author_resolver import resolve_facebook_author_entities
from src.pipeline.ig_geo_resolver import resolve_ig_geo_entities
from src.pipeline.strava_athlete_resolver import resolve_strava_athlete_entities
from src.pipeline.cross_source_signals import emit_cross_source_signals
from src.pipeline.timeline_builder import build_timeline
from src.pipeline.interaction_graph import build_interaction_graph
from src.pipeline.account_proximity import compute_account_proximity
from src.pipeline.alert_engine import run_alerts
from src.pipeline.behavioral_profiler import compute_behavioral_profiles
from src.pipeline.group_graph import build_whatsapp_group_graph, build_telegram_group_graph
from src.pipeline.strava_patterns import analyze_strava_patterns
from src.pipeline.bio_nlp import analyze_bios
from src.pipeline.entity_enrichment import enrich_entities_with_ner
from src.pipeline.shared_life_context import emit_shared_life_context_signals
from src.pipeline.graph_analytics import compute_graph_analytics
from src.pipeline.graph_overlap import compute_graph_overlap
from src.pipeline.bio_mention import detect_bio_mentions
from src.pipeline.location_inference import infer_locations
from src.pipeline.content_fingerprint import fingerprint_content
from src.pipeline.temporal_correlation import correlate_activity
from src.pipeline.contact_extraction import extract_contacts
from src.pipeline.route_similarity import analyze_route_similarity
from src.pipeline.relationship_intelligence import refresh_relationship_intelligence
from src.pipeline.media_analysis import (
    MEDIA_EXIF_BATCH_SIZE,
    MEDIA_PDF_IMAGE_BATCH_SIZE,
    MEDIA_PDF_TEXT_BATCH_SIZE,
    MEDIA_PHASH_BATCH_SIZE,
    analyze_media_exif,
    analyze_media_office_text,
    analyze_media_pdf_text,
    analyze_media_pdf_text,
    analyze_media_phash,
    extract_pdf_images,
)
from src.pipeline.media_analysis_tier1 import (
    analyze_media_faces,
    analyze_media_ocr,
    extract_video_frames,
    rebuild_face_match_signals,
)
from src.pipeline.identity_scorer import compute_identity_scores
from src.pipeline.face_clustering import run_face_clustering, propagate_drive_faces_via_knn
from src.pipeline.face_pair_signals import emit_face_pair_signals
from src.pipeline.face_associations import build_face_associations
from src.pipeline.social_face_link import emit_social_face_link_signals
from src.pipeline.identity_calibration import maybe_retrain
from src.pipeline.auto_labeler import seed_ground_truth_labels
from src.pipeline.timeline_text_features import build_timeline_text_features
from src.pipeline.language_id import backfill_language_profiles
from src.pipeline.translation_worker import run_translation_backfill, translation_max_per_run
from src.pipeline.sentiment_emotion import enrich_timeline_sentiment
from src.pipeline.conversation_analytics import build_conversation_analytics
from src.pipeline.timeline_embedder import embed_new_timeline_events
from src.pipeline.topical_similarity import emit_topical_similarity_signals
from src.pipeline.calibration_watchdog import check_calibration_readiness
from src.pipeline.phone_enrichment import enrich_phone_signals
from src.pipeline.email_breach import check_email_breaches
from src.pipeline.handle_fanout import run_handle_fanout
from src.pipeline.email_recognition import run_email_recognition
from src.pipeline.geocode import geocode_step
from src.pipeline.run_reporting import production_run_types, probe_phase_names
from src.notifications.alerts import notify_run_summary, notify_error, notify_new_alerts

logger = logging.getLogger(__name__)


# P0-3 (identity_system_review_plan.md): a legitimate run heartbeats every ~60s
# while it executes (see _run_with_heartbeat). The stale-lock cleaner reclaims a
# lock only when the heartbeat (falling back to started_at for pre-heartbeat rows)
# has been silent longer than this, so a genuinely long run (full-res is routinely
# 2-5h) is never reclaimed mid-flight, while a crashed run is freed shortly after
# its heartbeat stops. The old fixed 30-min-since-start timeout reclaimed EVERY
# real full-res's lock, letting an API-triggered run_incremental start concurrently
# and double-write.
#
# CAVEAT: some resolver/media phases are CPU-bound and synchronous (e.g. Phase 4.5
# fuzzy-matches tens of thousands of username-less profiles), which blocks the
# event loop so the heartbeat task cannot fire meanwhile. The threshold must
# therefore exceed the longest single synchronous phase, not just the heartbeat
# interval — hence 30 min, comfortably above observed phase blocks yet far below
# the 2-5h total runtime the old start-based timeout kept tripping on.
_STALE_HEARTBEAT_MINUTES = int(os.getenv("STALE_RUN_HEARTBEAT_MINUTES", "30"))
_HEARTBEAT_INTERVAL_SECONDS = 60
_RUN_CLAIM_LOCK_KEY = 0x55414E4152554E  # "UANARUN": serialize run creation.
_COVERAGE_SOURCE_BUCKETS = ("by_source", "by_type", "by_platform")
_PROCESSED_KEYS = (
    "processed",
    "total",
    "scanned",
    "entities_scanned",
    "events_scanned",
    "media_scanned",
    "pairs_scored",
    "emails_checked",
    "handles_queried",
    "bios_processed",
    "entities_analyzed",
    "candidates",
    "faces",
)
_ATTRIBUTED_KEYS = (
    "attributed",
    "resolved",
    "inserted",
    "updated",
    "rows",
    "signals_emitted",
    "associations_added",
    "location_evidence_upserted",
    "entities_updated",
    "discoveries_added",
    "new_findings",
    "new_alerts",
    "relationship_rows",
    "links_created",
    "geocoded",
)
_UNRESOLVED_KEYS = (
    "unresolved",
    "skipped_unresolved",
    "unparseable",
    "unmatched",
    "missing",
    "parse_failed",
)
_SKIPPED_KEYS = (
    "skipped_count",
    "skipped_invalid_time",
    "skipped_self",
    "skipped_unchanged",
    "below_threshold_skipped",
    "below_min_matches_skipped",
)
_ERROR_KEYS = ("error_count", "errors", "failed", "failures")


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _translation_phase_enabled() -> bool:
    if os.getenv("ENABLE_TRANSLATION_PHASE") is not None:
        return _env_bool("ENABLE_TRANSLATION_PHASE", False)
    provider = os.getenv("TRANSLATION_PROVIDER", "noop").strip().lower()
    return provider not in {"", "noop", "none", "disabled", "off"}


async def _run_language_id_phase() -> dict:
    return await backfill_language_profiles(
        batch_size=_env_int("PIPELINE_LANGUAGE_ID_BATCH_SIZE", 1000),
        max_events=_env_int("PIPELINE_LANGUAGE_ID_MAX_EVENTS", 1000),
    )


async def _run_translation_phase() -> dict:
    provider = os.getenv("TRANSLATION_PROVIDER", "noop").strip().lower() or "noop"
    if not _translation_phase_enabled():
        return {
            "processed": 0,
            "written": 0,
            "translated": 0,
            "failed": 0,
            "skipped": 0,
            "reason": "disabled",
            "provider": provider,
        }
    max_events = min(
        _env_int("PIPELINE_TRANSLATION_MAX_EVENTS", 100),
        translation_max_per_run(),
    )
    return await run_translation_backfill(
        batch_size=_env_int("PIPELINE_TRANSLATION_BATCH_SIZE", max_events),
        max_events=max_events,
    )

_PHASE_RESOURCE_CLASSES = {
    "resolve_entities": "db",
    "beeper_bridge": "collector_db",
    "ig_geo_entities": "collector_db",
    "facebook_author_entities": "collector_db",
    "strava_athlete_entities": "collector_db",
    "cross_source_signals": "collector_db",
    "timeline": "collector_db",
    "interactions": "collector_db",
    "account_proximity": "db",
    "entity_event_ranges": "db",
    "lexical_nlp": "cpu",
    "language_id": "cpu",
    "translation": "cpu",
    "alerts": "db",
    "behavioral_profiles": "db",
    "whatsapp_group_graph": "collector_db",
    "telegram_group_graph": "collector_db",
    "strava_patterns": "collector_db",
    "bio_nlp": "collector_db",
    "entity_enrichment": "cpu",
    "shared_life_context": "db",
    "graph_analytics": "cpu",
    "graph_overlap": "db",
    "bio_mention": "db",
    "location_inference": "collector_db",
    "content_fingerprint": "cpu",
    "temporal_correlation": "db",
    "contact_extraction": "cpu",
    "route_similarity": "collector_db",
    "relationship_intelligence": "db",
    "media_phash": "media_io",
    "media_exif": "media_io",
    "media_pdf_text": "media_io",
    "media_office_text": "media_io",
    "media_pdf_images": "media_io",
    "media_ocr": "media_io",
    "media_faces": "media_io",
    "media_video_frames": "media_io",
    "face_clustering": "vector",
    "drive_face_xref": "vector",
    "face_pair_knn": "vector",
    "face_associations": "vector",
    "social_face_link": "vector",
    "face_match_signals": "vector",
    "auto_label_seed": "db",
    "content_embedding": "vector",
    "topical_similarity": "vector",
    "identity_scoring": "db",
    "calibration_retrain": "cpu",
    "collector_priority_hints": "db",
    "phone_enrichment": "cpu",
    "email_breach_check": "notification",
    "handle_fanout": "notification",
    "email_recognition": "notification",
    "calibration_watchdog": "db",
    "geocode": "geocode",
}


async def _clear_stale_run_locks(conn, error_message: str) -> list[str]:
    rows = await conn.fetch("""
        UPDATE analysis_runs
        SET status = 'failed', finished_at = NOW(),
            error_message = $2
        WHERE status = 'running'
          AND COALESCE(heartbeat_at, started_at) < NOW() - make_interval(mins => $1)
        RETURNING id::text
    """, _STALE_HEARTBEAT_MINUTES, error_message)
    return [r["id"] for r in rows]


async def _is_run_locked() -> bool:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        cleaned = await _clear_stale_run_locks(
            conn,
            "Stale lock (heartbeat silent) - cleaned up automatically",
        )
        for run_id in cleaned:
            logger.warning("Cleaned up stale run lock: %s", run_id)
        row = await conn.fetchval("""
            SELECT id FROM analysis_runs WHERE status = 'running' LIMIT 1
        """)
    return row is not None


async def _heartbeat_loop(run_id: str) -> None:
    """Bump heartbeat_at every _HEARTBEAT_INTERVAL_SECONDS while a run executes.
    Cancelled by _run_with_heartbeat when the run finishes. Errors are swallowed
    so a transient DB hiccup never kills the run it is guarding."""
    pool = get_analyzer_pool()
    while True:
        try:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE analysis_runs SET heartbeat_at = NOW() WHERE id = $1::uuid",
                    run_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Heartbeat update failed (non-fatal)", exc_info=True)


async def _stop_heartbeat(hb: asyncio.Task) -> None:
    """Cancel a heartbeat task started with asyncio.create_task(_heartbeat_loop(...))
    and await its teardown. Safe to call from a finally block."""
    hb.cancel()
    try:
        await hb
    except asyncio.CancelledError:
        pass


async def clear_orphaned_run_locks() -> int:
    """Clear only STALE still-'running' analysis_runs rows at scheduler startup.

    Q5 fix (2026-07-16): the old version marked EVERY status='running' row failed
    on startup ("Orphaned by scheduler restart — cleared on startup"). In the
    single-scheduler model that is wrong for the common case of a *restart*: a
    scheduler recreate that happens WHILE a legitimate full_resolution (~2.9h) is
    executing would kill it every time, so full-res never reached 'completed'
    (verified: all recent full_resolution rows failed with exactly that message,
    last completed run was 2026-07-12).

    Correct rule — identical to the stale-lock test in _is_run_locked: only clear
    a run whose heartbeat (falling back to started_at for pre-heartbeat rows) has
    been silent longer than _STALE_HEARTBEAT_MINUTES. A run with a FRESH heartbeat
    means the previous scheduler process was, until seconds ago, actively driving
    it. Two cases at startup:
      * the old process is genuinely gone (crash/recreate) → its heartbeat stops,
        so within _STALE_HEARTBEAT_MINUTES this cleaner (or _is_run_locked's own
        stale-lock UPDATE, which runs before every scheduled run) frees the lock.
      * the old process is momentarily still finishing → a fresh heartbeat keeps
        the run alive; the single-scheduler safety is preserved because
        _is_run_locked still refuses to start a NEW run while any 'running' row
        exists, so we never double-write.
    Trading a few minutes of lock latency after a hard crash for not orphaning
    every in-flight full-res is the whole point. Returns the number cleared."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        cleared = await _clear_stale_run_locks(
            conn,
            "Stale run lock (heartbeat silent) - cleared on scheduler startup",
        )
    if cleared:
        logger.warning(
            "Cleared %d stale run lock(s) on scheduler startup (heartbeat older than %d min)",
            len(cleared), _STALE_HEARTBEAT_MINUTES,
        )
    return len(cleared)


async def update_entity_event_ranges() -> int:
    """Maintain entities.first_event_at / last_event_at = min/max(occurred_at) of the
    entity's timeline events. These bound per-entity timeline queries to the entity's
    own active months so Postgres partition-prunes timeline_events (373 partitions)
    instead of MergeAppend-ing all of them (~6.6s). IS DISTINCT FROM guard avoids
    dead-tuple churn on unchanged rows. Returns rows updated."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        updated = await conn.fetchval("""
            WITH r AS (
                SELECT entity_id, min(occurred_at) AS mn, max(occurred_at) AS mx
                FROM timeline_events
                WHERE entity_id IS NOT NULL
                GROUP BY entity_id
            ), upd AS (
                UPDATE entities e
                SET first_event_at = r.mn, last_event_at = r.mx
                FROM r
                WHERE e.id = r.entity_id
                  AND (e.first_event_at IS DISTINCT FROM r.mn
                       OR e.last_event_at IS DISTINCT FROM r.mx)
                RETURNING 1
            )
            SELECT count(*) FROM upd
        """)
    return updated or 0


async def get_last_run_time(run_type: str) -> datetime | None:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchval("""
            SELECT finished_at FROM analysis_runs
            WHERE run_type = $1 AND status = 'completed'
            ORDER BY finished_at DESC LIMIT 1
        """, run_type)
    return row


async def _try_create_run(run_type: str) -> str | None:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _RUN_CLAIM_LOCK_KEY)
            cleaned = await _clear_stale_run_locks(
                conn,
                "Stale lock (heartbeat silent) - cleaned up automatically",
            )
            for stale_id in cleaned:
                logger.warning("Cleaned up stale run lock before starting %s: %s", run_type, stale_id)

            running_id = await conn.fetchval(
                "SELECT id::text FROM analysis_runs WHERE status = 'running' LIMIT 1"
            )
            if running_id:
                return None

            try:
                return await conn.fetchval("""
                    INSERT INTO analysis_runs (run_type, status, heartbeat_at)
                    VALUES ($1, 'running', NOW()) RETURNING id::text
                """, run_type)
            except UniqueViolationError:
                logger.warning("Run claim lost unique-index race for %s", run_type)
                return None


async def _get_recent_alerts(since_minutes: int = 5) -> list[dict]:
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT alert_type, severity, title
            FROM alerts
            WHERE detected_at > NOW() - INTERVAL '%s minutes'
            ORDER BY detected_at DESC
        """ % since_minutes)
    return [dict(r) for r in rows]


async def _finish_run(run_id: str, stats: dict, error: str | None = None) -> None:
    pool = get_analyzer_pool()
    status = "failed" if error else "completed"
    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE analysis_runs SET
                status = $1,
                finished_at = NOW(),
                entities_processed = $2,
                events_created = $3,
                alerts_created = $4,
                signals_created = $5,
                error_message = $6
            WHERE id = $7::uuid
        """, status,
            stats.get("entities", 0),
            stats.get("events", 0),
            stats.get("alerts", 0),
            stats.get("signals", 0),
            error,
            run_id)


# P2-3: the secondary (non-fatal) pipeline phases, shared verbatim by
# run_incremental and run_full_resolution. Order matters — the media block
# (6C.2/6H) produces derived pdf_image/video_frame rows that later media phases
# consume via fetch_unprocessed_derived(), and run_face_clustering must precede
# rebuild_face_match_signals. Extracted into one list so both runners stay in
# lockstep and every phase gets timed + status-recorded (was ~16 duplicated
# try/except blocks per runner).
def _secondary_phases() -> list[tuple[str, object]]:
    return [
        ("whatsapp_group_graph", build_whatsapp_group_graph),
        ("telegram_group_graph", build_telegram_group_graph),
        ("strava_patterns", analyze_strava_patterns),
        ("bio_nlp", analyze_bios),
        # NER enrichment (spaCy en_core_web_trf) + cross-entity shared rare
        # ORG/school/location signal. Placed after bio_nlp so both share the
        # same bio-source pattern; before identity_scoring (later in this
        # list) so the shared_life_context signal is visible to the scorer.
        ("entity_enrichment", enrich_entities_with_ner),
        ("shared_life_context", emit_shared_life_context_signals),
        ("graph_overlap", compute_graph_overlap),
        ("bio_mention", detect_bio_mentions),
        ("location_inference", infer_locations),
        ("content_fingerprint", fingerprint_content),
        ("temporal_correlation", correlate_activity),
        ("contact_extraction", extract_contacts),
        ("route_similarity", analyze_route_similarity),
        ("relationship_intelligence", refresh_relationship_intelligence),
        ("graph_analytics", compute_graph_analytics),
        ("media_pdf_text", lambda: analyze_media_pdf_text(limit=MEDIA_PDF_TEXT_BATCH_SIZE)),
        ("media_office_text", lambda: analyze_media_office_text(limit=MEDIA_PDF_TEXT_BATCH_SIZE)),
        ("media_pdf_images", lambda: extract_pdf_images(limit=MEDIA_PDF_IMAGE_BATCH_SIZE)),
        ("media_video_frames", extract_video_frames),
        ("media_exif", lambda: analyze_media_exif(limit=MEDIA_EXIF_BATCH_SIZE)),
        ("media_phash", lambda: analyze_media_phash(limit=MEDIA_PHASH_BATCH_SIZE)),
        ("media_ocr", analyze_media_ocr),
        ("media_faces", analyze_media_faces),
        ("face_clustering", run_face_clustering),
        # Axis-3 Change-2: bridge orphan drive faces via direct FAISS kNN vs the
        # bridged-anchor corpus, so a drive face gets attributed even when it
        # didn't co-cluster with a collector face (the co-membership path was
        # starved). Runs AFTER face_clustering — clustering may have added new
        # anchors — and BEFORE face_match_signals so its inserts are visible.
        ("drive_face_xref", propagate_drive_faces_via_knn),
        # Axis-3 Change-3: cross-entity face_pair_knn signal (portrait-gated,
        # >=N matches). Emits identity_signals rows the scorer + auto_labeler
        # now recognise. Runs after drive_face_xref so it sees anchors added
        # by the kNN path.
        ("face_pair_knn", emit_face_pair_signals),
        # Face social graph (2026-07-08). face_associations must precede
        # social_face_link so its inserts are visible; both run BEFORE
        # face_match_signals since neither writes to entity_faces (they read
        # facetracker.faces directly). Placement immediately after face_pair_knn
        # keeps every face-based derivation in one contiguous block.
        ("face_associations", build_face_associations),
        ("social_face_link", emit_social_face_link_signals),
        ("face_match_signals", rebuild_face_match_signals),
        ("auto_label_seed", seed_ground_truth_labels),
        # Axis-1 MVP: sentence-embed new timeline_events.title into
        # timeline_embeddings, then compute cross-entity topical_similarity from
        # centroid cosine. content_embedding runs AFTER auto_label_seed and
        # BEFORE identity_scoring so the topical_similarity signal is visible
        # to the scorer. topical_similarity runs immediately after so it sees
        # this cycle's freshly embedded rows.
        ("content_embedding", embed_new_timeline_events),
        ("topical_similarity", emit_topical_similarity_signals),
        ("calibration_retrain", maybe_retrain),
        ("identity_scoring", compute_identity_scores),
        # Track-C: OSINT enrichment tools. Each is env-gated (default off for
        # network-heavy ones) and returns {"skipped": ...} when disabled.
        # phone_enrichment is cheap + local (phonenumbers lib) so defaults on.
        ("phone_enrichment", enrich_phone_signals),
        ("email_breach_check", check_email_breaches),
        ("handle_fanout", run_handle_fanout),
        ("email_recognition", run_email_recognition),
        # Calibration cutover watchdog: emits CALIBRATION_READY alert + Telegram
        # notification when identity_labels >= CALIBRATION_MONITOR_MIN_LABELS
        # AND LR beats noisy-OR by >= CALIBRATION_MONITOR_MIN_DELTA AUC.
        # Deliberately DOES NOT auto-flip IDENTITY_MODEL_ENABLED - keeps the
        # cutover human-in-the-loop.
        ("calibration_watchdog", check_calibration_readiness),
        ("geocode", geocode_step),
    ]


async def _call_phase(fn):
    result = fn()
    if inspect.isawaitable(result):
        return await result
    return result


def _sum_numeric_stats(stats: dict) -> int:
    """Sum phase counters while ignoring skip/error metadata."""
    return int(sum(
        value for value in stats.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ))


def _safe_count(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    return None


def _count_from_keys(stats: dict, keys: tuple[str, ...]) -> int:
    for key in keys:
        if key not in stats:
            continue
        count = _safe_count(stats.get(key))
        if count is not None:
            return count
    return 0


def _phase_resource_class(phase: str) -> str:
    return _PHASE_RESOURCE_CLASSES.get(phase, "db")


def _source_label(source) -> str:
    label = str(source or "all").strip() or "all"
    return label[:128]


def _normalize_phase_stats(result) -> dict:
    if isinstance(result, dict):
        return result
    count = _safe_count(result)
    if count is not None:
        return {"attributed": count}
    if isinstance(result, (list, tuple, set)):
        return {"processed": len(result)}
    return {}


def _bounded_json_payload(value):
    if value is None or value == "" or value == [] or value == {}:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)[:20]
    if isinstance(value, dict):
        return dict(list(value.items())[:20])
    return value


def _coverage_payload(stats: dict, status: str, error: str | None):
    for key in (
        "top_unresolved_json",
        "top_unresolved",
        "unresolved_samples",
        "unresolved_refs",
        "skipped_tables",
        "error",
        "skipped",
    ):
        payload = _bounded_json_payload(stats.get(key))
        if payload != []:
            return payload
    if status == "failed" and error:
        return {"error": error}
    return []


def _coverage_row(
    run_id: str,
    run_type: str,
    phase: str,
    source: str,
    status: str,
    duration_ms: int,
    stats: dict,
    error: str | None,
) -> tuple:
    processed_count = _count_from_keys(stats, _PROCESSED_KEYS)
    attributed_count = _count_from_keys(stats, _ATTRIBUTED_KEYS)
    unresolved_count = _count_from_keys(stats, _UNRESOLVED_KEYS)
    skipped_count = _count_from_keys(stats, _SKIPPED_KEYS)
    error_count = _count_from_keys(stats, _ERROR_KEYS)

    skipped = stats.get("skipped")
    skipped_numeric = _safe_count(skipped)
    if skipped_numeric is not None:
        skipped_count += skipped_numeric
    elif status == "skipped" or isinstance(skipped, str):
        skipped_count = max(skipped_count, 1)

    if phase == "alerts" and attributed_count == 0:
        attributed_count = _sum_numeric_stats(stats)

    if (status == "failed" or stats.get("error")) and error_count == 0:
        error_count = 1

    return (
        run_id,
        run_type,
        phase,
        _source_label(source),
        status,
        processed_count,
        attributed_count,
        unresolved_count,
        skipped_count,
        error_count,
        json.dumps(_coverage_payload(stats, status, error), default=str),
        duration_ms,
        _phase_resource_class(phase),
    )


def _coverage_snapshots_for_phase_result(
    run_id: str,
    run_type: str,
    phase: str,
    status: str,
    duration_ms: int,
    result,
    error: str | None = None,
) -> list[tuple]:
    stats = _normalize_phase_stats(result)
    rows: list[tuple] = []

    for bucket_name in _COVERAGE_SOURCE_BUCKETS:
        bucket = stats.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for source, source_stats in bucket.items():
            if isinstance(source_stats, dict):
                rows.append(_coverage_row(
                    run_id, run_type, phase, source, status,
                    duration_ms, source_stats, error,
                ))
        if rows:
            break

    seen_sources = {r[3] for r in rows}
    skipped_tables = stats.get("skipped_tables")
    if isinstance(skipped_tables, (list, tuple, set)):
        for source in skipped_tables:
            label = _source_label(source)
            if label in seen_sources:
                continue
            rows.append(_coverage_row(
                run_id,
                run_type,
                phase,
                label,
                "skipped" if status == "ok" else status,
                duration_ms,
                {"skipped": 1, "top_unresolved": [source]},
                error,
            ))

    if not rows:
        rows.append(_coverage_row(
            run_id, run_type, phase, "all", status, duration_ms, stats, error
        ))
    return rows


async def _write_phase_coverage_snapshots(
    conn,
    run_id: str,
    run_type: str,
    phase: str,
    status: str,
    duration_ms: int,
    result,
    error: str | None,
) -> None:
    rows = _coverage_snapshots_for_phase_result(
        run_id, run_type, phase, status, duration_ms, result, error
    )
    await conn.executemany(
        """
        INSERT INTO pipeline_coverage_snapshots (
            run_id, run_type, phase, source, phase_status,
            processed_count, attributed_count, unresolved_count,
            skipped_count, error_count, top_unresolved_json,
            duration_ms, resource_class
        )
        VALUES (
            $1::uuid, $2, $3, $4, $5,
            $6, $7, $8, $9, $10, $11::jsonb,
            $12, $13
        )
        """,
        rows,
    )


async def _run_phase(run_id: str, run_type: str, name: str, fn, *, default=None):
    """Run one non-fatal phase; record ok/skipped/failed + duration.

    Collector reads are optional for analyzer uptime. When the collector DB is
    unreachable, collector-dependent phases are marked skipped so a degraded run
    reaches a clean terminal state while true SQL bugs still surface as failed.
    """
    start = time.monotonic()
    status, err = "ok", None
    result = default
    try:
        result = await _call_phase(fn)
    except Exception as e:  # noqa: BLE001 — phases are intentionally non-fatal
        detail = str(e).strip()
        err = f"{type(e).__name__}: {detail}" if detail else type(e).__name__
        if is_collector_unavailable_error(e):
            logger.warning("%s skipped: collector unavailable: %s", name, err)
            status = "skipped"
            result = {"skipped": "collector_unavailable", "error": err[:500]}
        else:
            logger.exception("%s failed (non-fatal)", name)
            status = "failed"
        err = err[:2000]
    duration_ms = int((time.monotonic() - start) * 1000)
    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO run_phase_status (run_id, run_type, phase, status, duration_ms, error) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6)",
                run_id, run_type, name, status, duration_ms, err)
            try:
                await _write_phase_coverage_snapshots(
                    conn, run_id, run_type, name, status, duration_ms, result, err
                )
            except Exception:
                logger.debug("phase coverage snapshot write failed (non-fatal)", exc_info=True)
    except Exception:
        logger.debug("phase status write failed (non-fatal)", exc_info=True)
    return result


async def _run_secondary_phases(run_id: str, run_type: str) -> None:
    for name, fn in _secondary_phases():
        await _run_phase(run_id, run_type, name, fn)


async def _alert_on_repeated_phase_failures(threshold: int = 3) -> None:
    """Notify when a phase has failed its last `threshold` runs in a row — the
    signal the old all-non-fatal design silently swallowed."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH recent AS (
                SELECT phase, status,
                       row_number() OVER (PARTITION BY phase ORDER BY created_at DESC) AS rn
                FROM run_phase_status
                WHERE run_type = ANY($2::text[])
                  AND phase <> ALL($3::text[])
            )
            SELECT phase FROM recent WHERE rn <= $1
            GROUP BY phase
            HAVING count(*) = $1 AND count(*) FILTER (WHERE status = 'failed') = $1
        """, threshold, production_run_types(), probe_phase_names())
    if rows:
        phases = ", ".join(r["phase"] for r in rows)
        logger.error("Phases failing %d runs in a row: %s", threshold, phases)
        try:
            await notify_error("pipeline phases", f"{phases} failed {threshold} runs in a row")
        except Exception:
            logger.debug("repeated-failure notify failed (non-fatal)", exc_info=True)


async def run_incremental() -> dict:
    run_id = await _try_create_run("incremental")
    if not run_id:
        logger.warning("Another run is in progress, skipping")
        return {"skipped": True}

    stats = {"entities": 0, "events": 0, "alerts": 0, "signals": 0}
    heartbeat = asyncio.create_task(_heartbeat_loop(run_id))

    try:
        since = await get_last_run_time("incremental")

        resolver_stats = await _run_phase(
            run_id, "incremental", "resolve_entities", resolve_entities, default={}
        )
        stats["entities"] = resolver_stats.get("entities", 0)
        stats["signals"] = resolver_stats.get("signals", 0)

        # SYNC #31: bridge beeper native ids into native-source links BEFORE
        # timeline attribution so beeper messages attribute to those entities.
        beeper_stats = await _run_phase(
            run_id, "incremental", "beeper_bridge", bridge_beeper, default={"totals": {}}
        )
        stats["beeper_links"] = beeper_stats.get("totals", {}).get("links_created", 0)

        # GAP-4: mint entities for every IG profile with substantive collected
        # content. Collector-owned maintenance repairs instagram_posts.profile_id.
        ig_geo_stats = await _run_phase(
            run_id, "incremental", "ig_geo_entities", resolve_ig_geo_entities, default={}
        )
        stats["ig_geo_links"] = ig_geo_stats.get("links_created", 0)

        facebook_stats = await _run_phase(
            run_id, "incremental", "facebook_author_entities", resolve_facebook_author_entities, default={}
        )
        stats["facebook_links"] = facebook_stats.get("links_created", 0)

        # gap-round-2: mint entities for every Strava athlete with collected
        # activities (unblocks T5.2 shared-route-origin). Same "create-entity-if-
        # content" pattern as ig_geo; runs BEFORE build_timeline so Strava blocks
        # + route_similarity see the fresh links.
        strava_stats = await _run_phase(
            run_id, "incremental", "strava_athlete_entities", resolve_strava_athlete_entities, default={}
        )
        stats["strava_links"] = strava_stats.get("links_created", 0)

        # SYNC #35: cross-source identity signals (tg<->wa phone, IG external_url).
        xsrc_stats = await _run_phase(
            run_id, "incremental", "cross_source_signals", emit_cross_source_signals, default={}
        )
        stats["xsrc_signals"] = xsrc_stats.get("rows", 0)

        timeline_stats = await _run_phase(
            run_id, "incremental", "timeline", lambda: build_timeline(since=since), default={}
        )
        stats["events"] = timeline_stats.get("inserted", 0)
        interaction_stats = await _run_phase(
            run_id, "incremental", "interactions", lambda: build_interaction_graph(since=since), default={}
        )
        stats["interactions"] = interaction_stats.get("inserted", 0)
        proximity_stats = await _run_phase(
            run_id, "incremental", "account_proximity", compute_account_proximity, default={}
        )
        stats["account_proximity"] = proximity_stats.get("rows", 0)
        # Refresh per-entity date-range for partition-pruned timeline queries.
        stats["entity_ranges"] = await _run_phase(
            run_id, "incremental", "entity_event_ranges", update_entity_event_ranges, default=0
        )
        text_stats = await _run_phase(
            run_id, "incremental", "lexical_nlp", build_timeline_text_features, default={}
        )
        stats["text_features"] = _sum_numeric_stats(text_stats)
        language_stats = await _run_phase(
            run_id, "incremental", "language_id", _run_language_id_phase, default={}
        )
        stats["language_profiles"] = _sum_numeric_stats(language_stats)
        translation_stats = await _run_phase(
            run_id, "incremental", "translation", _run_translation_phase, default={}
        )
        stats["translations"] = _sum_numeric_stats(translation_stats)
        sentiment_stats = await _run_phase(
            run_id, "incremental", "sentiment_emotion", enrich_timeline_sentiment, default={}
        )
        stats["sentiment_features"] = _sum_numeric_stats(sentiment_stats)
        conversation_stats = await _run_phase(
            run_id, "incremental", "conversation_analytics", build_conversation_analytics, default={}
        )
        stats["conversation_threads"] = conversation_stats.get("threads", 0)

        alert_stats = await _run_phase(
            run_id, "incremental", "alerts", run_alerts, default={}
        )
        stats["alert_breakdown"] = dict(alert_stats) if isinstance(alert_stats, dict) else {}
        stats["alerts"] = _sum_numeric_stats(alert_stats)

        # Non-fatal: behavioral profiling scans entities x timeline_events and can
        # time out under heavy concurrent collector write load. It must not abort
        # the run before _run_secondary_phases (content/temporal/embeddings/
        # topical/faces/identity_scoring) — those are the payload. (2026-07-12)
        await _run_phase(
            run_id, "incremental", "behavioral_profiles", compute_behavioral_profiles
        )

        # P2-3: all non-fatal secondary phases (media analysis, graphs, face
        # clustering, identity scoring, geocode) — each timed + status-recorded.
        await _run_secondary_phases(run_id, "incremental")

        await _finish_run(run_id, stats)
        await _alert_on_repeated_phase_failures()
        logger.info("Incremental run complete: %s", stats)
        await notify_run_summary("incremental", stats, run_id=run_id)

        if stats["alerts"] > 0:
            new_alerts = await _get_recent_alerts()
            await notify_new_alerts(new_alerts)

    except Exception as e:
        logger.exception("Incremental run failed")
        await _finish_run(run_id, stats, error=str(e))
        await notify_error("incremental", str(e))
        raise
    finally:
        await _stop_heartbeat(heartbeat)

    return stats


async def hard_reset_entities() -> None:
    """DESTRUCTIVE, manual-only: delete all entities + platform links so the next
    resolution rebuilds the identity graph from scratch with fresh UUIDs.

    Via the schema FKs this CASCADE-deletes entity_faces, identity_signals,
    entity_relationships and behavioral_profiles, NULLs timeline_events.entity_id,
    and orphans identity_labels / entity_views / case_items (which have no FK and
    keep dead UUIDs). This is exactly the damage P0-1 removed from the automatic
    12h full-resolution path — it lives here as an explicit opt-in only. Run it
    solely when you deliberately want to discard the current identity graph, then
    follow with `python -m src.main full` to repopulate."""
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_platform_links")
        await conn.execute("DELETE FROM entities")
    logger.warning(
        "hard_reset_entities: all entities + platform links deleted (CASCADE wiped "
        "entity_faces/signals/relationships). Run a full resolution to rebuild."
    )


async def run_full_resolution() -> dict:
    run_id = await _try_create_run("full_resolution")
    if not run_id:
        logger.warning("Another run is in progress, skipping")
        return {"skipped": True}

    stats = {"entities": 0, "events": 0, "alerts": 0, "signals": 0}
    heartbeat = asyncio.create_task(_heartbeat_loop(run_id))

    try:
        # P0-1 (identity_system_review_plan.md): full resolution re-resolves every
        # entity but MUST NOT delete entities/links first. resolve_entities() is
        # already idempotent and self-cleaning — it UPSERTs entity_platform_links,
        # recovers each entity's existing UUID from those links, handles
        # splits/merges, and deletes orphaned entities at the end. The former
        # `DELETE FROM entity_platform_links; DELETE FROM entities` here emptied the
        # links resolve_entities reads for ID recovery, so EVERY entity got a fresh
        # uuid4() on each ~12h run. Via the schema FKs that CASCADE-wiped
        # entity_faces and orphaned identity_labels / entity_views / case_items
        # (all keyed by entity UUID) twice a day. Keeping IDs stable is what makes
        # bridged faces, human calibration labels, saved cases and review state
        # durable — the precondition for P1-4 (calibration) to accumulate at all.
        #
        # The old `DELETE FROM identity_signals ...` here was additionally dead
        # code: the `DELETE FROM entities` that followed CASCADE-deleted every
        # identity_signals row regardless (FK ON DELETE CASCADE, schema.sql:40).
        #
        # A destructive from-scratch rebuild is now an explicit opt-in
        # (hard_reset_entities below), never the automatic 12h path.
        resolver_stats = await _run_phase(
            run_id, "full_resolution", "resolve_entities", resolve_entities, default={}
        )
        stats["entities"] = resolver_stats.get("entities", 0)
        stats["signals"] = resolver_stats.get("signals", 0)

        # SYNC #31: bridge beeper native ids into native-source links before the
        # full re-attribution rescan.
        beeper_stats = await _run_phase(
            run_id, "full_resolution", "beeper_bridge", bridge_beeper, default={"totals": {}}
        )
        stats["beeper_links"] = beeper_stats.get("totals", {}).get("links_created", 0)

        # GAP-4: mint entities for every IG profile with substantive collected
        # content. Collector-owned maintenance repairs instagram_posts.profile_id.
        ig_geo_stats = await _run_phase(
            run_id, "full_resolution", "ig_geo_entities", resolve_ig_geo_entities, default={}
        )
        stats["ig_geo_links"] = ig_geo_stats.get("links_created", 0)

        facebook_stats = await _run_phase(
            run_id, "full_resolution", "facebook_author_entities", resolve_facebook_author_entities, default={}
        )
        stats["facebook_links"] = facebook_stats.get("links_created", 0)

        # gap-round-2: mint entities for every Strava athlete with collected
        # activities (unblocks T5.2 shared-route-origin). Same "create-entity-if-
        # content" pattern as ig_geo; runs BEFORE the full timeline re-attribution
        # so Strava blocks + route_similarity see the fresh links.
        strava_stats = await _run_phase(
            run_id, "full_resolution", "strava_athlete_entities", resolve_strava_athlete_entities, default={}
        )
        stats["strava_links"] = strava_stats.get("links_created", 0)

        # SYNC #35: cross-source identity signals (tg<->wa phone, IG external_url).
        xsrc_stats = await _run_phase(
            run_id, "full_resolution", "cross_source_signals", emit_cross_source_signals, default={}
        )
        stats["xsrc_signals"] = xsrc_stats.get("rows", 0)

        # Full re-attribution rescan, but skip github: its ~7.3M commits are a
        # hard attribution ceiling (~6 tracked github entities) and dominate the
        # rescan cost. New github events still arrive via the 2-hourly
        # incremental (build_timeline(since=last_run)). Override with
        # FULL_REBUILD_SKIP_SOURCES="" to force a github rescan. (2026-07-10)
        import os as _os_ir
        _skip = _os_ir.getenv("FULL_REBUILD_SKIP_SOURCES", "github")
        skip_sources = {s.strip() for s in _skip.split(",") if s.strip()}
        timeline_stats = await _run_phase(
            run_id,
            "full_resolution",
            "timeline",
            lambda: build_timeline(since=None, skip_sources=skip_sources),
            default={},
        )
        stats["events"] = timeline_stats.get("inserted", 0)
        interaction_stats = await _run_phase(
            run_id, "full_resolution", "interactions", lambda: build_interaction_graph(since=None), default={}
        )
        stats["interactions"] = interaction_stats.get("inserted", 0)
        proximity_stats = await _run_phase(
            run_id, "full_resolution", "account_proximity", compute_account_proximity, default={}
        )
        stats["account_proximity"] = proximity_stats.get("rows", 0)
        stats["entity_ranges"] = await _run_phase(
            run_id, "full_resolution", "entity_event_ranges", update_entity_event_ranges, default=0
        )
        text_stats = await _run_phase(
            run_id, "full_resolution", "lexical_nlp", build_timeline_text_features, default={}
        )
        stats["text_features"] = _sum_numeric_stats(text_stats)
        language_stats = await _run_phase(
            run_id, "full_resolution", "language_id", _run_language_id_phase, default={}
        )
        stats["language_profiles"] = _sum_numeric_stats(language_stats)
        translation_stats = await _run_phase(
            run_id, "full_resolution", "translation", _run_translation_phase, default={}
        )
        stats["translations"] = _sum_numeric_stats(translation_stats)
        sentiment_stats = await _run_phase(
            run_id, "full_resolution", "sentiment_emotion", enrich_timeline_sentiment, default={}
        )
        stats["sentiment_features"] = _sum_numeric_stats(sentiment_stats)
        conversation_stats = await _run_phase(
            run_id, "full_resolution", "conversation_analytics", build_conversation_analytics, default={}
        )
        stats["conversation_threads"] = conversation_stats.get("threads", 0)

        alert_stats = await _run_phase(
            run_id, "full_resolution", "alerts", run_alerts, default={}
        )
        stats["alert_breakdown"] = dict(alert_stats) if isinstance(alert_stats, dict) else {}
        stats["alerts"] = _sum_numeric_stats(alert_stats)

        # Non-fatal: behavioral profiling scans entities x timeline_events and can
        # time out under heavy concurrent collector write load. It must not abort
        # the run before _run_secondary_phases (content/temporal/embeddings/
        # topical/faces/identity_scoring) — those are the payload. (2026-07-12)
        await _run_phase(
            run_id, "full_resolution", "behavioral_profiles", compute_behavioral_profiles
        )

        # P2-3: all non-fatal secondary phases (shared with run_incremental),
        # each timed + status-recorded so a persistently failing step is visible.
        await _run_secondary_phases(run_id, "full_resolution")

        await _finish_run(run_id, stats)
        await _alert_on_repeated_phase_failures()
        logger.info("Full resolution run complete: %s", stats)
        await notify_run_summary("full resolution", stats, run_id=run_id)

        if stats["alerts"] > 0:
            new_alerts = await _get_recent_alerts()
            await notify_new_alerts(new_alerts)

    except Exception as e:
        logger.exception("Full resolution run failed")
        await _finish_run(run_id, stats, error=str(e))
        await notify_error("full resolution", str(e))
        raise
    finally:
        await _stop_heartbeat(heartbeat)

    return stats
