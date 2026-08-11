from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.notifications import telegram


@dataclass(frozen=True)
class StreamAlertFingerprint:
    fingerprint: str
    alert_type: str
    entity_id: str | None
    source: str | None
    window_start: datetime
    window_end: datetime


def make_alert_fingerprint(
    alert_type: str,
    *,
    entity_id: str | None,
    source: str | None,
    bucket_key: str,
    window_start: datetime,
    window_end: datetime,
) -> StreamAlertFingerprint:
    raw = "|".join([
        alert_type,
        entity_id or "",
        source or "",
        bucket_key,
        window_start.astimezone(timezone.utc).isoformat(),
        window_end.astimezone(timezone.utc).isoformat(),
    ])
    return StreamAlertFingerprint(
        fingerprint=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        alert_type=alert_type,
        entity_id=entity_id,
        source=source,
        window_start=window_start,
        window_end=window_end,
    )


def is_suppressed(
    suppressions: list[dict[str, Any]],
    *,
    alert_type: str,
    entity_id: str | None,
    source: str | None,
    now: datetime,
) -> bool:
    for row in suppressions:
        if row.get("alert_type") not in {None, alert_type, "*"}:
            continue
        if row.get("entity_id") not in {None, entity_id}:
            continue
        if row.get("source") not in {None, source, "*"}:
            continue
        starts = row.get("starts_at")
        ends = row.get("ends_at")
        if starts and now < starts:
            continue
        if ends and now > ends:
            continue
        return True
    return False


async def stream_alert_status(conn) -> dict[str, Any]:
    offsets = await conn.fetch(
        """
        SELECT source_name, cursor_table, cursor_value, last_seen_at, updated_at
        FROM stream_alert_offsets
        ORDER BY updated_at DESC
        LIMIT 50
        """
    )
    sent = await conn.fetchval(
        "SELECT COUNT(*) FROM alert_fingerprints WHERE status = 'sent'"
    )
    suppressed = await conn.fetchval(
        "SELECT COUNT(*) FROM alert_fingerprints WHERE status = 'suppressed'"
    )
    active_suppressions = await conn.fetchval(
        """
        SELECT COUNT(*) FROM alert_suppressions
        WHERE starts_at <= NOW() AND (ends_at IS NULL OR ends_at >= NOW())
        """
    )
    return {
        "offsets": [dict(row) for row in offsets],
        "sent_fingerprints": int(sent or 0),
        "suppressed_fingerprints": int(suppressed or 0),
        "active_suppressions": int(active_suppressions or 0),
    }


def alert_detail_json(**kwargs: Any) -> str:
    return json.dumps({k: v for k, v in kwargs.items() if v is not None}, default=str)


def _hour_bucket(value: datetime) -> tuple[datetime, datetime]:
    start = value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return start, start + timedelta(hours=1)


def _detail_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _format_window(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        except ValueError:
            return str(value)
    return "unknown"


def format_stream_alert_notification(rows: list[dict[str, Any]], *, dashboard_url: str) -> str:
    lines = [f"\U0001f514 <b>Stream alert digest</b>", f"{len(rows)} grouped alert(s) ready for triage."]
    for row in rows[:12]:
        detail = _detail_dict(row.get("detail"))
        alert_type = str(row.get("alert_type") or "alert").replace("_", " ").title()
        source = html.escape(str(row.get("source") or "unknown source"))
        count = int(row.get("count") or 0)
        window = _format_window(row.get("window_start"))
        term = detail.get("term")
        summary = detail.get("summary")
        target = f" term <code>{html.escape(str(term))}</code>" if term else ""
        if not target and summary:
            target = f" {html.escape(str(summary))}"
        lines.append(
            f"• <b>{html.escape(alert_type)}</b> on {source}{target}: "
            f"{count:,} signal(s), window {html.escape(window)}."
        )
    if len(rows) > 12:
        lines.append(f"...and {len(rows) - 12} more grouped alert(s).")
    lines.append(f"\n{html.escape(dashboard_url)}/alerts")
    return "\n".join(lines)


async def send_pending_stream_alert_notifications(
    conn,
    *,
    limit: int = 20,
    retry_after_hours: int = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    retry_after = now - timedelta(hours=retry_after_hours)
    rows = await conn.fetch(
        """
        SELECT fingerprint, alert_type, entity_id::text, source, window_start, window_end,
               last_sent_at, count, status, detail
        FROM alert_fingerprints
        WHERE status = 'pending'
           OR (status = 'notify_failed' AND (last_sent_at IS NULL OR last_sent_at <= $2::timestamptz))
        ORDER BY window_end DESC, updated_at DESC
        LIMIT $1
        """,
        limit,
        retry_after,
    )
    payload = [dict(row) for row in rows]
    if not payload:
        return {"pending": 0, "sent": 0, "failed": 0}

    ok = await telegram.send(
        format_stream_alert_notification(payload, dashboard_url=telegram.get_dashboard_url()),
        message_type="stream_alerts",
    )
    fingerprints = [row["fingerprint"] for row in payload]
    status = "sent" if ok else "notify_failed"
    await conn.execute(
        """
        UPDATE alert_fingerprints
        SET status = $1, last_sent_at = $2, updated_at = NOW()
        WHERE fingerprint = ANY($3::text[])
        """,
        status,
        now,
        fingerprints,
    )
    return {
        "pending": len(payload),
        "sent": len(payload) if ok else 0,
        "failed": 0 if ok else len(payload),
    }


_TERM_RE = re.compile(r"(?:[#@][\w_]{3,}|https?://[^\s]+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b)", re.IGNORECASE)
_MESSAGE_EVENT_TYPES = {"MESSAGE_SENT", "REPLIED", "FORWARDED_MESSAGE"}
_MEDIA_EVENT_TYPES = {
    "CONTENT_PUBLISHED",
    "VIDEO_PUBLISHED",
    "STORY_POSTED",
    "HIGHLIGHT_POSTED",
    "TAGGED_IN",
    "PHOTO_COAPPEARANCE",
}


def burst_alert_type_for_event_type(event_type: str | None) -> str | None:
    if event_type in _MESSAGE_EVENT_TYPES:
        return "MESSAGE_BURST"
    if event_type in _MEDIA_EVENT_TYPES:
        return "MEDIA_BURST"
    return None


def collector_resume_from_status(previous: str | None, current: str | None) -> bool:
    return previous in {"stale", "degraded"} and current == "fresh"


def emotional_z_score(current_mean: float, baseline_mean: float, baseline_stddev: float | None) -> float:
    stddev = baseline_stddev if baseline_stddev and baseline_stddev > 0 else 0.05
    return (current_mean - baseline_mean) / stddev


async def _active_suppressions(conn, now: datetime) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT alert_type, entity_id::text, source, starts_at, ends_at
        FROM alert_suppressions
        WHERE starts_at <= $1::timestamptz
          AND (ends_at IS NULL OR ends_at >= $1::timestamptz)
        """,
        now,
    )
    return [dict(row) for row in rows]


async def _set_offset(conn, source_name: str, cursor_table: str, cursor_value: datetime, last_seen_at: datetime) -> None:
    await conn.execute(
        """
        INSERT INTO stream_alert_offsets (source_name, cursor_table, cursor_value, last_seen_at, updated_at)
        VALUES ($1, $2, $3, $4, NOW())
        ON CONFLICT (source_name, cursor_table) DO UPDATE SET
            cursor_value = EXCLUDED.cursor_value,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = NOW()
        """,
        source_name,
        cursor_table,
        cursor_value.isoformat(),
        last_seen_at,
    )


async def _get_offset(conn, source_name: str, cursor_table: str) -> datetime | None:
    row = await conn.fetchrow(
        """
        SELECT cursor_value
        FROM stream_alert_offsets
        WHERE source_name = $1 AND cursor_table = $2
        """,
        source_name,
        cursor_table,
    )
    return parse_cursor_datetime(row["cursor_value"]) if row and row["cursor_value"] else None


async def _upsert_alert_window(
    conn,
    *,
    alert_type: str,
    bucket_key: str,
    window_start: datetime,
    window_end: datetime,
    count: int,
    detail: str,
    entity_id: str | None = None,
    source: str | None = None,
    baseline: float | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO alert_windows (
            bucket_start, bucket_end, alert_type, bucket_key, entity_id, source,
            count, baseline, metadata, updated_at
        )
        VALUES ($1, $2, $3, $4, $5::uuid, $6, $7, $8, $9::jsonb, NOW())
        ON CONFLICT (bucket_start, alert_type, bucket_key) DO UPDATE SET
            count = alert_windows.count + EXCLUDED.count,
            baseline = COALESCE(EXCLUDED.baseline, alert_windows.baseline),
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        """,
        window_start,
        window_end,
        alert_type,
        bucket_key,
        entity_id,
        source,
        count,
        baseline,
        detail,
    )


async def _upsert_alert_fingerprint(
    conn,
    *,
    alert_type: str,
    bucket_key: str,
    window_start: datetime,
    window_end: datetime,
    count: int,
    detail: str,
    suppressions: list[dict[str, Any]],
    degraded_sources: set[str],
    now: datetime,
    entity_id: str | None = None,
    source: str | None = None,
) -> str:
    suppressed = source in degraded_sources or is_suppressed(
        suppressions,
        alert_type=alert_type,
        entity_id=entity_id,
        source=source,
        now=now,
    )
    detail_obj = _detail_dict(detail)
    detail_obj["repeat_bucket"] = bucket_key
    if not suppressed:
        recent = await conn.fetchval(
            """
            SELECT 1
            FROM alert_fingerprints
            WHERE alert_type = $1
              AND COALESCE(entity_id::text, '') = COALESCE($2, '')
              AND COALESCE(source, '') = COALESCE($3, '')
              AND detail->>'repeat_bucket' = $4
              AND status = 'sent'
              AND last_sent_at > $5::timestamptz - INTERVAL '6 hours'
            LIMIT 1
            """,
            alert_type,
            entity_id,
            source,
            bucket_key,
            now,
        )
        suppressed = bool(recent)
    fp = make_alert_fingerprint(
        alert_type,
        entity_id=entity_id,
        source=source,
        bucket_key=bucket_key,
        window_start=window_start,
        window_end=window_end,
    )
    await conn.execute(
        """
        INSERT INTO alert_fingerprints (
            fingerprint, alert_type, entity_id, source, window_start, window_end,
            count, status, detail, created_at, updated_at
        )
        VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, $8, $9::jsonb, NOW(), NOW())
        ON CONFLICT (fingerprint) DO UPDATE SET
            count = alert_fingerprints.count + EXCLUDED.count,
            status = CASE
                WHEN alert_fingerprints.status IN ('sent', 'suppressed') THEN alert_fingerprints.status
                ELSE EXCLUDED.status
            END,
            detail = EXCLUDED.detail,
            updated_at = NOW()
        """,
        fp.fingerprint,
        fp.alert_type,
        fp.entity_id,
        fp.source,
        fp.window_start,
        fp.window_end,
        count,
        "suppressed" if suppressed else "pending",
        json.dumps(detail_obj),
    )
    return "suppressed" if suppressed else "pending"


def extract_burst_terms(text: str) -> list[str]:
    terms = []
    for match in _TERM_RE.finditer(text or ""):
        term = match.group(0).strip().lower().rstrip(".,);]")
        if term.startswith("http"):
            try:
                from urllib.parse import urlparse

                host = urlparse(term).hostname
                if host:
                    term = host.lower()
            except Exception:  # noqa: BLE001
                continue
        terms.append(term)
    return sorted(set(terms))


def parse_cursor_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    if fallback is not None:
        return fallback
    raise ValueError("missing stream alert cursor")


async def run_stream_alert_once(
    conn,
    *,
    batch_size: int = 1000,
    term_threshold: int = 10,
    message_threshold: int = 50,
    media_threshold: int = 25,
    degraded_sources: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    degraded_sources = degraded_sources or set()
    suppressions = await _active_suppressions(conn, now)
    cursor_dt = await _get_offset(conn, "timeline_text_features", "timeline_text_features")
    if cursor_dt is None:
        latest = await conn.fetchval("SELECT MAX(occurred_at) FROM timeline_text_features")
        await _set_offset(conn, "timeline_text_features", "timeline_text_features", latest or now, latest or now)
        return {"processed": 0, "bootstrapped": True, "alerts": 0}

    rows = await conn.fetch(
        """
        SELECT event_id::text, entity_id::text, source, event_type, occurred_at, canonical_text
        FROM timeline_text_features
        WHERE occurred_at > $1::timestamptz
        ORDER BY occurred_at ASC
        LIMIT $2
        """,
        cursor_dt,
        batch_size,
    )
    if not rows:
        return {"processed": 0, "bootstrapped": False, "alerts": 0}

    term_buckets: dict[tuple[str, str | None, datetime], dict[str, Any]] = {}
    event_buckets: dict[tuple[str, str | None, str | None, datetime], dict[str, Any]] = {}
    latest_seen = rows[-1]["occurred_at"]
    for row in rows:
        window_start = row["occurred_at"].replace(minute=0, second=0, microsecond=0)
        window_end = window_start + timedelta(hours=1)
        for term in extract_burst_terms(row["canonical_text"]):
            key = (term, row["source"], window_start)
            bucket = term_buckets.setdefault(key, {
                "term": term,
                "source": row["source"],
                "window_start": window_start,
                "window_end": window_end,
                "count": 0,
                "events": [],
            })
            bucket["count"] += 1
            bucket["events"].append(row["event_id"])

        event_type = row["event_type"]
        alert_type = burst_alert_type_for_event_type(event_type)
        if alert_type:
            # Entity-level when attributed; source-level when not. This keeps
            # private content out of notifications while preserving a useful pivot.
            bucket_key = row["entity_id"] or f"source:{row['source']}"
            key = (alert_type, row["source"], row["entity_id"], window_start)
            bucket = event_buckets.setdefault(key, {
                "alert_type": alert_type,
                "bucket_key": bucket_key,
                "entity_id": row["entity_id"],
                "source": row["source"],
                "window_start": window_start,
                "window_end": window_end,
                "count": 0,
                "events": [],
                "event_types": {},
            })
            bucket["count"] += 1
            bucket["events"].append(row["event_id"])
            bucket["event_types"][event_type] = bucket["event_types"].get(event_type, 0) + 1

    alerts = 0
    suppressed = 0
    for bucket in term_buckets.values():
        await _upsert_alert_window(
            conn,
            alert_type="TERM_BURST",
            bucket_key=bucket["term"],
            window_start=bucket["window_start"],
            window_end=bucket["window_end"],
            source=bucket["source"],
            count=bucket["count"],
            detail=alert_detail_json(sample_event_ids=bucket["events"][:10]),
        )
        if bucket["count"] < term_threshold:
            continue
        status = await _upsert_alert_fingerprint(
            conn,
            alert_type="TERM_BURST",
            bucket_key=bucket["term"],
            window_start=bucket["window_start"],
            window_end=bucket["window_end"],
            source=bucket["source"],
            count=bucket["count"],
            detail=alert_detail_json(term=bucket["term"], sample_event_ids=bucket["events"][:10]),
            suppressions=suppressions,
            degraded_sources=degraded_sources,
            now=now,
        )
        if status == "suppressed":
            suppressed += 1
        else:
            alerts += 1

    for bucket in event_buckets.values():
        alert_type = bucket["alert_type"]
        threshold = message_threshold if alert_type == "MESSAGE_BURST" else media_threshold
        await _upsert_alert_window(
            conn,
            alert_type=alert_type,
            bucket_key=bucket["bucket_key"],
            window_start=bucket["window_start"],
            window_end=bucket["window_end"],
            entity_id=bucket["entity_id"],
            source=bucket["source"],
            count=bucket["count"],
            detail=alert_detail_json(event_types=bucket["event_types"], sample_event_ids=bucket["events"][:10]),
        )
        if bucket["count"] < threshold:
            continue
        status = await _upsert_alert_fingerprint(
            conn,
            alert_type=alert_type,
            bucket_key=bucket["bucket_key"],
            entity_id=bucket["entity_id"],
            source=bucket["source"],
            window_start=bucket["window_start"],
            window_end=bucket["window_end"],
            count=bucket["count"],
            detail=alert_detail_json(event_types=bucket["event_types"], sample_event_ids=bucket["events"][:10]),
            suppressions=suppressions,
            degraded_sources=degraded_sources,
            now=now,
        )
        if status == "suppressed":
            suppressed += 1
        else:
            alerts += 1

    await _set_offset(conn, "timeline_text_features", "timeline_text_features", latest_seen, latest_seen)
    return {
        "processed": len(rows),
        "bootstrapped": False,
        "alerts": alerts,
        "suppressed": suppressed,
        "terms": len(term_buckets),
        "event_buckets": len(event_buckets),
    }


async def run_location_evidence_stream_alert_once(
    conn,
    *,
    batch_size: int = 1000,
    threshold: int = 8,
    min_confidence: float = 0.55,
    degraded_sources: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    degraded_sources = degraded_sources or set()
    suppressions = await _active_suppressions(conn, now)
    cursor_dt = await _get_offset(conn, "location_evidence", "location_evidence")
    if cursor_dt is None:
        latest = await conn.fetchval("SELECT MAX(created_at) FROM location_evidence")
        await _set_offset(conn, "location_evidence", "location_evidence", latest or now, latest or now)
        return {"processed": 0, "bootstrapped": True, "alerts": 0, "suppressed": 0}

    rows = await conn.fetch(
        """
        SELECT entity_id::text, source,
               date_trunc('hour', COALESCE(occurred_at, created_at)) AS bucket_start,
               count(*)::int AS event_count,
               avg(confidence) AS avg_confidence,
               (array_agg(evidence_key ORDER BY COALESCE(occurred_at, created_at) DESC))[1:10] AS evidence_keys,
               max(created_at) AS latest_created_at
        FROM location_evidence
        WHERE created_at > $1::timestamptz
          AND COALESCE(status, 'active') = 'active'
          AND COALESCE(confidence, 0) >= $3
          AND COALESCE(evidence_type, '') NOT IN ('caption_derived', 'venue_geocode')
        GROUP BY entity_id, source, date_trunc('hour', COALESCE(occurred_at, created_at))
        ORDER BY max(created_at)
        LIMIT $2
        """,
        cursor_dt,
        batch_size,
        min_confidence,
    )
    if not rows:
        return {"processed": 0, "bootstrapped": False, "alerts": 0, "suppressed": 0}

    alerts = suppressed = processed = 0
    latest_seen = max(row["latest_created_at"] for row in rows)
    for row in rows:
        processed += int(row["event_count"] or 0)
        window_start, window_end = _hour_bucket(row["bucket_start"])
        bucket_key = f"{row['entity_id']}:{row['source']}"
        detail = alert_detail_json(
            summary="location evidence spike",
            avg_confidence=float(row["avg_confidence"] or 0),
            evidence_keys=list(row["evidence_keys"] or []),
        )
        await _upsert_alert_window(
            conn,
            alert_type="LOCATION_EVIDENCE_BURST",
            bucket_key=bucket_key,
            entity_id=row["entity_id"],
            source=row["source"],
            window_start=window_start,
            window_end=window_end,
            count=int(row["event_count"] or 0),
            detail=detail,
        )
        if int(row["event_count"] or 0) < threshold:
            continue
        status = await _upsert_alert_fingerprint(
            conn,
            alert_type="LOCATION_EVIDENCE_BURST",
            bucket_key=bucket_key,
            entity_id=row["entity_id"],
            source=row["source"],
            window_start=window_start,
            window_end=window_end,
            count=int(row["event_count"] or 0),
            detail=detail,
            suppressions=suppressions,
            degraded_sources=degraded_sources,
            now=now,
        )
        if status == "suppressed":
            suppressed += 1
        else:
            alerts += 1
    await _set_offset(conn, "location_evidence", "location_evidence", latest_seen, latest_seen)
    return {"processed": processed, "bootstrapped": False, "alerts": alerts, "suppressed": suppressed, "windows": len(rows)}


async def run_emotional_spike_stream_alert_once(
    conn,
    *,
    batch_size: int = 500,
    min_baseline: int = 20,
    min_current: int = 5,
    z_threshold: float = 2.5,
    confidence_floor: float = 0.55,
    degraded_sources: set[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    degraded_sources = degraded_sources or set()
    suppressions = await _active_suppressions(conn, now)
    cursor_dt = await _get_offset(conn, "sentiment_emotion", "timeline_text_features")
    if cursor_dt is None:
        latest = await conn.fetchval("SELECT MAX(processed_at) FROM timeline_text_features WHERE sentiment_label IS NOT NULL")
        await _set_offset(conn, "sentiment_emotion", "timeline_text_features", latest or now, latest or now)
        return {"processed": 0, "bootstrapped": True, "alerts": 0, "suppressed": 0}

    touched = await conn.fetch(
        """
        SELECT DISTINCT entity_id::text AS entity_id,
               max(processed_at) AS latest_processed_at
        FROM timeline_text_features
        WHERE processed_at > $1::timestamptz
          AND entity_id IS NOT NULL
          AND vader_compound IS NOT NULL
          AND COALESCE(sentiment_confidence, 0) >= $3
        GROUP BY entity_id
        ORDER BY max(processed_at)
        LIMIT $2
        """,
        cursor_dt,
        batch_size,
        confidence_floor,
    )
    if not touched:
        return {"processed": 0, "bootstrapped": False, "alerts": 0, "suppressed": 0}

    entity_ids = [row["entity_id"] for row in touched]
    latest_seen = max(row["latest_processed_at"] for row in touched)
    rows = await conn.fetch(
        """
        WITH baseline AS (
            SELECT entity_id,
                   count(*)::int AS baseline_count,
                   avg(vader_compound) AS baseline_mean,
                   stddev_pop(vader_compound) AS baseline_stddev
            FROM timeline_text_features
            WHERE entity_id = ANY($1::uuid[])
              AND vader_compound IS NOT NULL
              AND occurred_at >= NOW() - INTERVAL '60 days'
              AND occurred_at < NOW() - INTERVAL '24 hours'
              AND COALESCE(sentiment_confidence, 0) >= $4
            GROUP BY entity_id
        ),
        current AS (
            SELECT entity_id,
                   count(*)::int AS event_count,
                   avg(vader_compound) AS current_mean,
                   avg(sentiment_confidence) AS avg_confidence,
                   (array_agg(event_id::text ORDER BY occurred_at DESC))[1:10] AS event_ids,
                   (array_agg(source ORDER BY occurred_at DESC))[1] AS source
            FROM timeline_text_features
            WHERE entity_id = ANY($1::uuid[])
              AND vader_compound IS NOT NULL
              AND occurred_at >= NOW() - INTERVAL '24 hours'
              AND COALESCE(sentiment_confidence, 0) >= $4
            GROUP BY entity_id
        )
        SELECT c.entity_id::text, c.source, b.baseline_count, c.event_count,
               b.baseline_mean, b.baseline_stddev, c.current_mean,
               c.avg_confidence, c.event_ids
        FROM current c
        JOIN baseline b ON b.entity_id = c.entity_id
        WHERE b.baseline_count >= $2
          AND c.event_count >= $3
        """,
        entity_ids,
        min_baseline,
        min_current,
        confidence_floor,
    )

    alerts = suppressed = 0
    for row in rows:
        z_score = emotional_z_score(
            float(row["current_mean"] or 0),
            float(row["baseline_mean"] or 0),
            float(row["baseline_stddev"] or 0),
        )
        if abs(z_score) < z_threshold:
            continue
        direction = "positive" if z_score > 0 else "negative"
        window_start, window_end = _hour_bucket(now)
        bucket_key = f"{row['entity_id']}:{direction}:{window_start.date().isoformat()}"
        detail = alert_detail_json(
            summary=f"{direction} emotional spike",
            z_score=round(z_score, 3),
            baseline_count=row["baseline_count"],
            event_count=row["event_count"],
            avg_confidence=float(row["avg_confidence"] or 0),
            event_ids=list(row["event_ids"] or []),
        )
        await _upsert_alert_window(
            conn,
            alert_type="EMOTIONAL_SPIKE_FAST",
            bucket_key=bucket_key,
            entity_id=row["entity_id"],
            source=row["source"],
            window_start=window_start,
            window_end=window_end,
            count=int(row["event_count"] or 0),
            baseline=float(row["baseline_mean"] or 0),
            detail=detail,
        )
        status = await _upsert_alert_fingerprint(
            conn,
            alert_type="EMOTIONAL_SPIKE_FAST",
            bucket_key=bucket_key,
            entity_id=row["entity_id"],
            source=row["source"],
            window_start=window_start,
            window_end=window_end,
            count=int(row["event_count"] or 0),
            detail=detail,
            suppressions=suppressions,
            degraded_sources=degraded_sources,
            now=now,
        )
        if status == "suppressed":
            suppressed += 1
        else:
            alerts += 1
    await _set_offset(conn, "sentiment_emotion", "timeline_text_features", latest_seen, latest_seen)
    return {"processed": len(touched), "bootstrapped": False, "alerts": alerts, "suppressed": suppressed}


async def run_collector_resume_stream_alert_once(
    analyzer_conn,
    collector_conn,
    *,
    batch_size: int = 200,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    suppressions = await _active_suppressions(analyzer_conn, now)
    cursor_dt = await _get_offset(analyzer_conn, "collector_coverage_snapshots", "collection_coverage_snapshots")
    if cursor_dt is None:
        latest = await collector_conn.fetchval("SELECT MAX(created_at) FROM collection_coverage_snapshots")
        await _set_offset(analyzer_conn, "collector_coverage_snapshots", "collection_coverage_snapshots", latest or now, latest or now)
        return {"processed": 0, "bootstrapped": True, "alerts": 0, "suppressed": 0}

    rows = await collector_conn.fetch(
        """
        SELECT source, status, latest_data_at, latest_run_at, rows_24h, media_24h,
               errors_24h, rate_limits_24h, created_at
        FROM collection_coverage_snapshots
        WHERE created_at > $1::timestamptz
        ORDER BY created_at ASC
        LIMIT $2
        """,
        cursor_dt,
        batch_size,
    )
    if not rows:
        return {"processed": 0, "bootstrapped": False, "alerts": 0, "suppressed": 0}

    alerts = suppressed = 0
    latest_seen = max(row["created_at"] for row in rows)
    for row in rows:
        previous = await analyzer_conn.fetchval(
            """
            SELECT metadata->>'status'
            FROM alert_windows
            WHERE alert_type = 'COLLECTOR_SOURCE_STATUS'
              AND bucket_key = $1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            row["source"],
        )
        window_start, window_end = _hour_bucket(row["created_at"])
        status_detail = alert_detail_json(
            status=row["status"],
            latest_data_at=row["latest_data_at"],
            latest_run_at=row["latest_run_at"],
            rows_24h=row["rows_24h"],
            media_24h=row["media_24h"],
            errors_24h=row["errors_24h"],
            rate_limits_24h=row["rate_limits_24h"],
        )
        await _upsert_alert_window(
            analyzer_conn,
            alert_type="COLLECTOR_SOURCE_STATUS",
            bucket_key=row["source"],
            source=row["source"],
            window_start=window_start,
            window_end=window_end,
            count=1,
            detail=status_detail,
        )
        if not collector_resume_from_status(previous, row["status"]):
            continue
        fp_status = await _upsert_alert_fingerprint(
            analyzer_conn,
            alert_type="COLLECTOR_SILENCE_RESUME",
            bucket_key=row["source"],
            source=row["source"],
            window_start=window_start,
            window_end=window_end,
            count=1,
            detail=alert_detail_json(summary=f"resumed after {previous}", previous_status=previous, current_status=row["status"]),
            suppressions=suppressions,
            degraded_sources=set(),
            now=now,
        )
        if fp_status == "suppressed":
            suppressed += 1
        else:
            alerts += 1
    await _set_offset(analyzer_conn, "collector_coverage_snapshots", "collection_coverage_snapshots", latest_seen, latest_seen)
    return {"processed": len(rows), "bootstrapped": False, "alerts": alerts, "suppressed": suppressed}
