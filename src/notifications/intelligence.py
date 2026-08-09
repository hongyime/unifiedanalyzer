"""Compact intelligence summaries for Telegram notifications."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from src.pipeline.face_bridge_audit import audit_face_bridge_collisions

INTELLIGENCE_ALERT_TYPES = {
    "EMOTIONAL_SPIKE",
    "FACE_LINK_DRIFT",
    "LOCATION_EVIDENCE_SPIKE",
}

INTELLIGENCE_PHASES = (
    "lexical_nlp",
    "sentiment_emotion",
    "conversation_analytics",
    "alerts",
    "content_embedding",
    "face_clustering",
    "face_pair_knn",
    "face_match_signals",
    "location_inference",
)


def _num(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return "0"


def _pct(part: int | float | None, total: int | float | None) -> str:
    if not total:
        return "0%"
    return f"{round((float(part or 0) / float(total)) * 100):.0f}%"


def _age(value: datetime | None) -> str:
    if not value:
        return "never"
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 90:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _safe_int(stats: dict, key: str) -> int:
    value = stats.get(key, 0)
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def intelligence_run_lines(stats: dict) -> list[str]:
    text_features = _safe_int(stats, "text_features")
    sentiment_features = _safe_int(stats, "sentiment_features")
    chat_threads = _safe_int(stats, "conversation_threads")
    alerts = _safe_int(stats, "alerts")
    alert_breakdown = stats.get("alert_breakdown") if isinstance(stats.get("alert_breakdown"), dict) else {}

    lines: list[str] = []
    if text_features or sentiment_features or chat_threads or alerts:
        lines.append(
            "Intel: "
            f"{_num(text_features)} text, "
            f"{_num(sentiment_features)} sentiment, "
            f"{_num(chat_threads)} chat threads, "
            f"{_num(alerts)} alerts."
        )

    signal_alerts = {
        key: int(value)
        for key, value in alert_breakdown.items()
        if key in {"emotional_spike", "face_link_drift", "location_evidence_spike"}
        and isinstance(value, (int, float))
        and value
    }
    if signal_alerts:
        rendered = ", ".join(f"{key.replace('_', ' ')} {value}" for key, value in signal_alerts.items())
        lines.append(f"Spikes: {rendered}.")
    return lines[:2]


async def _fetchval(conn, query: str, *args, default=0):
    try:
        value = await conn.fetchval(query, *args)
        return default if value is None else value
    except Exception:
        return default


async def _fetchrow(conn, query: str, *args) -> dict:
    try:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else {}
    except Exception:
        return {}


async def build_intelligence_status(conn) -> dict:
    text = await _fetchrow(conn, """
        SELECT
            count(*)::int AS total,
            count(*) FILTER (WHERE search_vector IS NOT NULL)::int AS fts_ready,
            count(*) FILTER (WHERE sentiment_label IS NOT NULL)::int AS sentiment_ready,
            max(processed_at) AS latest_text,
            max(processed_at) FILTER (WHERE sentiment_label IS NOT NULL) AS latest_sentiment
        FROM timeline_text_features
    """)
    chat = await _fetchrow(conn, """
        SELECT count(*)::int AS threads, max(updated_at) AS latest
        FROM conversation_threads
    """)
    location = await _fetchrow(conn, """
        SELECT
            count(*)::int AS total,
            count(*) FILTER (WHERE status IN ('active', 'confirmed'))::int AS active,
            count(*) FILTER (WHERE status = 'rejected')::int AS rejected,
            count(*) FILTER (WHERE status = 'suppressed')::int AS suppressed,
            count(*) FILTER (
                WHERE status IN ('active', 'confirmed') AND COALESCE(confidence, 0) < 0.45
            )::int AS weak
        FROM location_evidence
    """)
    latest_phases = await _fetchrow(conn, """
        WITH latest AS (
            SELECT DISTINCT ON (phase) phase, status
            FROM run_phase_status
            WHERE phase = ANY($1::text[])
            ORDER BY phase, created_at DESC
        )
        SELECT
            count(*) FILTER (WHERE status = 'failed')::int AS failed,
            array_remove(array_agg(phase ORDER BY phase) FILTER (WHERE status = 'failed'), NULL) AS failed_phases
        FROM latest
    """, list(INTELLIGENCE_PHASES))
    alerts = await _fetchrow(conn, """
        SELECT
            count(*)::int AS total,
            count(*) FILTER (WHERE alert_type = 'EMOTIONAL_SPIKE')::int AS emotional_spikes,
            count(*) FILTER (WHERE alert_type = 'FACE_LINK_DRIFT')::int AS face_drift,
            count(*) FILTER (WHERE alert_type = 'LOCATION_EVIDENCE_SPIKE')::int AS location_spikes
        FROM alerts
        WHERE detected_at > NOW() - INTERVAL '24 hours'
          AND alert_type = ANY($1::text[])
    """, list(INTELLIGENCE_ALERT_TYPES))

    face = {"available": False}
    try:
        face = await audit_face_bridge_collisions(conn, sample_limit=0)
    except Exception:
        pass

    semantic_enabled = os.getenv("TEXT_SEARCH_HYBRID_SEMANTIC", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }
    total_text = int(text.get("total") or 0)
    return {
        "text_total": total_text,
        "fts_ready": int(text.get("fts_ready") or 0),
        "fts_pct": _pct(text.get("fts_ready"), total_text),
        "sentiment_ready": int(text.get("sentiment_ready") or 0),
        "sentiment_pct": _pct(text.get("sentiment_ready"), total_text),
        "latest_text": text.get("latest_text"),
        "latest_sentiment": text.get("latest_sentiment"),
        "chat_threads": int(chat.get("threads") or 0),
        "chat_latest": chat.get("latest"),
        "semantic_enabled": semantic_enabled,
        "search_mode": "semantic-enabled" if semantic_enabled else "semantic-fallback",
        "face_available": bool(face.get("available")),
        "face_ok": face.get("ok"),
        "face_entity_collisions": int(face.get("face_entity_collisions") or 0),
        "cluster_entity_collisions": int(face.get("cluster_entity_collisions") or 0),
        "contested_cluster_count": int(face.get("contested_cluster_count") or 0),
        "location_total": int(location.get("total") or 0),
        "location_active": int(location.get("active") or 0),
        "location_rejected": int(location.get("rejected") or 0),
        "location_suppressed": int(location.get("suppressed") or 0),
        "location_weak": int(location.get("weak") or 0),
        "intel_alerts_24h": int(alerts.get("total") or 0),
        "emotional_spikes_24h": int(alerts.get("emotional_spikes") or 0),
        "face_drift_24h": int(alerts.get("face_drift") or 0),
        "location_spikes_24h": int(alerts.get("location_spikes") or 0),
        "intel_failed_phases": latest_phases.get("failed_phases") or [],
        "intel_failed_phase_count": int(latest_phases.get("failed") or 0),
    }


def intelligence_status_lines(intel: dict | None) -> list[str]:
    if not intel:
        return ["Intelligence: unavailable."]

    face_state = "unknown"
    if intel.get("face_available"):
        face_state = "OK" if intel.get("face_ok") else "needs review"
    location_total = intel.get("location_total") or 0
    location_active = intel.get("location_active") or 0
    location_suppressed = intel.get("location_suppressed") or 0
    location_weak = intel.get("location_weak") or 0

    lines = [
        (
            "Text: "
            f"{_num(intel.get('sentiment_ready'))}/{_num(intel.get('text_total'))} sentiment "
            f"({intel.get('sentiment_pct')}); "
            f"FTS {intel.get('fts_pct')}; latest NLP {_age(intel.get('latest_text'))}."
        ),
        (
            "Chat/search: "
            f"{_num(intel.get('chat_threads'))} Telegram threads; "
            f"hybrid search keyword-ready, {intel.get('search_mode')}."
        ),
        (
            "Face/location: "
            f"face audit {face_state} "
            f"({intel.get('face_entity_collisions', 0)} face collisions, "
            f"{intel.get('cluster_entity_collisions', 0)} drift clusters); "
            f"location {_pct(location_active, location_total)} active, "
            f"{_num(location_suppressed)} suppressed, {_num(location_weak)} weak."
        ),
    ]
    if intel.get("intel_alerts_24h"):
        lines.append(
            "Intel alerts 24h: "
            f"{_num(intel.get('emotional_spikes_24h'))} emotional, "
            f"{_num(intel.get('face_drift_24h'))} face drift, "
            f"{_num(intel.get('location_spikes_24h'))} location."
        )
    if intel.get("intel_failed_phases"):
        lines.append("Intel phase failures: " + ", ".join(intel["intel_failed_phases"][:6]) + ".")
    return lines[:5]
