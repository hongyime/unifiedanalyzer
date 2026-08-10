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
        target = f" term <code>{html.escape(str(term))}</code>" if term else ""
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
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cursor = await conn.fetchrow(
        """
        SELECT cursor_value
        FROM stream_alert_offsets
        WHERE source_name = 'timeline_text_features'
          AND cursor_table = 'timeline_text_features'
        """
    )
    if not cursor or not cursor["cursor_value"]:
        latest = await conn.fetchval("SELECT MAX(occurred_at) FROM timeline_text_features")
        await conn.execute(
            """
            INSERT INTO stream_alert_offsets (source_name, cursor_table, cursor_value, last_seen_at, updated_at)
            VALUES ('timeline_text_features', 'timeline_text_features', $1, $2, NOW())
            ON CONFLICT (source_name, cursor_table) DO UPDATE SET
                cursor_value = EXCLUDED.cursor_value,
                last_seen_at = EXCLUDED.last_seen_at,
                updated_at = NOW()
            """,
            latest.isoformat() if latest else now.isoformat(),
            latest or now,
        )
        return {"processed": 0, "bootstrapped": True, "alerts": 0}

    cursor_dt = parse_cursor_datetime(cursor["cursor_value"], now)
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
    for bucket in term_buckets.values():
        await conn.execute(
            """
            INSERT INTO alert_windows (
                bucket_start, bucket_end, alert_type, bucket_key, source, count, metadata, updated_at
            )
            VALUES ($1, $2, 'TERM_BURST', $3, $4, $5, $6::jsonb, NOW())
            ON CONFLICT (bucket_start, alert_type, bucket_key) DO UPDATE SET
                count = alert_windows.count + EXCLUDED.count,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            bucket["window_start"],
            bucket["window_end"],
            bucket["term"],
            bucket["source"],
            bucket["count"],
            alert_detail_json(sample_event_ids=bucket["events"][:10]),
        )
        if bucket["count"] < term_threshold:
            continue
        fp = make_alert_fingerprint(
            "TERM_BURST",
            entity_id=None,
            source=bucket["source"],
            bucket_key=bucket["term"],
            window_start=bucket["window_start"],
            window_end=bucket["window_end"],
        )
        await conn.execute(
            """
            INSERT INTO alert_fingerprints (
                fingerprint, alert_type, source, window_start, window_end,
                count, status, detail, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7::jsonb, NOW(), NOW())
            ON CONFLICT (fingerprint) DO UPDATE SET
                count = alert_fingerprints.count + EXCLUDED.count,
                updated_at = NOW()
            """,
            fp.fingerprint,
            fp.alert_type,
            fp.source,
            fp.window_start,
            fp.window_end,
            bucket["count"],
            alert_detail_json(term=bucket["term"], sample_event_ids=bucket["events"][:10]),
        )
        alerts += 1

    for bucket in event_buckets.values():
        alert_type = bucket["alert_type"]
        threshold = message_threshold if alert_type == "MESSAGE_BURST" else media_threshold
        await conn.execute(
            """
            INSERT INTO alert_windows (
                bucket_start, bucket_end, alert_type, bucket_key, entity_id, source, count, metadata, updated_at
            )
            VALUES ($1, $2, $3, $4, $5::uuid, $6, $7, $8::jsonb, NOW())
            ON CONFLICT (bucket_start, alert_type, bucket_key) DO UPDATE SET
                count = alert_windows.count + EXCLUDED.count,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            bucket["window_start"],
            bucket["window_end"],
            alert_type,
            bucket["bucket_key"],
            bucket["entity_id"],
            bucket["source"],
            bucket["count"],
            alert_detail_json(event_types=bucket["event_types"], sample_event_ids=bucket["events"][:10]),
        )
        if bucket["count"] < threshold:
            continue
        fp = make_alert_fingerprint(
            alert_type,
            entity_id=bucket["entity_id"],
            source=bucket["source"],
            bucket_key=bucket["bucket_key"],
            window_start=bucket["window_start"],
            window_end=bucket["window_end"],
        )
        await conn.execute(
            """
            INSERT INTO alert_fingerprints (
                fingerprint, alert_type, entity_id, source, window_start, window_end,
                count, status, detail, created_at, updated_at
            )
            VALUES ($1, $2, $3::uuid, $4, $5, $6, $7, 'pending', $8::jsonb, NOW(), NOW())
            ON CONFLICT (fingerprint) DO UPDATE SET
                count = alert_fingerprints.count + EXCLUDED.count,
                updated_at = NOW()
            """,
            fp.fingerprint,
            fp.alert_type,
            fp.entity_id,
            fp.source,
            fp.window_start,
            fp.window_end,
            bucket["count"],
            alert_detail_json(event_types=bucket["event_types"], sample_event_ids=bucket["events"][:10]),
        )
        alerts += 1

    await conn.execute(
        """
        INSERT INTO stream_alert_offsets (source_name, cursor_table, cursor_value, last_seen_at, updated_at)
        VALUES ('timeline_text_features', 'timeline_text_features', $1, $2, NOW())
        ON CONFLICT (source_name, cursor_table) DO UPDATE SET
            cursor_value = EXCLUDED.cursor_value,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = NOW()
        """,
        latest_seen.isoformat(),
        latest_seen,
    )
    return {
        "processed": len(rows),
        "bootstrapped": False,
        "alerts": alerts,
        "terms": len(term_buckets),
        "event_buckets": len(event_buckets),
    }
