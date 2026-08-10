import asyncio
from datetime import datetime, timedelta, timezone

from src.pipeline import stream_alerts
from src.pipeline.stream_alerts import (
    extract_burst_terms,
    format_stream_alert_notification,
    is_suppressed,
    make_alert_fingerprint,
    parse_cursor_datetime,
    send_pending_stream_alert_notifications,
)


def test_stream_alert_fingerprint_is_stable():
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)

    first = make_alert_fingerprint("TERM_BURST", entity_id="e1", source="telegram", bucket_key="#tag", window_start=start, window_end=end)
    second = make_alert_fingerprint("TERM_BURST", entity_id="e1", source="telegram", bucket_key="#tag", window_start=start, window_end=end)

    assert first.fingerprint == second.fingerprint


def test_suppression_matches_scope_window():
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    suppressions = [{
        "alert_type": "TERM_BURST",
        "entity_id": None,
        "source": "telegram",
        "starts_at": now - timedelta(minutes=1),
        "ends_at": now + timedelta(minutes=1),
    }]

    assert is_suppressed(suppressions, alert_type="TERM_BURST", entity_id="x", source="telegram", now=now)
    assert not is_suppressed(suppressions, alert_type="MEDIA_BURST", entity_id="x", source="telegram", now=now)


def test_extract_burst_terms_normalizes_urls_handles_and_hashtags():
    terms = extract_burst_terms("see #Topic and @handle at https://Example.com/path")

    assert "#topic" in terms
    assert "@handle" in terms
    assert "example.com" in terms


def test_parse_cursor_datetime_accepts_iso_text():
    parsed = parse_cursor_datetime("2026-08-09T14:53:24+00:00")

    assert parsed.year == 2026
    assert parsed.tzinfo is not None


def test_stream_alert_notification_uses_safe_summary_only():
    text = format_stream_alert_notification(
        [{
            "alert_type": "TERM_BURST",
            "source": "telegram",
            "count": 12,
            "window_start": datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
            "detail": {"term": "#topic", "sample_event_ids": ["event-1"]},
        }],
        dashboard_url="http://127.0.0.1:8002",
    )

    assert "Term Burst" in text
    assert "#topic" in text
    assert "event-1" not in text


def test_send_pending_stream_alert_notifications_marks_sent(monkeypatch):
    class Conn:
        def __init__(self):
            self.updated_status = None
            self.updated_fingerprints = None

        async def fetch(self, sql, *args):
            return [{
                "fingerprint": "fp1",
                "alert_type": "TERM_BURST",
                "entity_id": None,
                "source": "telegram",
                "window_start": datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
                "window_end": datetime(2026, 8, 10, 13, tzinfo=timezone.utc),
                "last_sent_at": None,
                "count": 12,
                "status": "pending",
                "detail": {"term": "#topic"},
            }]

        async def execute(self, sql, *args):
            self.updated_status = args[0]
            self.updated_fingerprints = args[2]

    async def fake_send(text, **kwargs):
        assert kwargs["message_type"] == "stream_alerts"
        assert "#topic" in text
        return True

    conn = Conn()
    monkeypatch.setattr(stream_alerts.telegram, "send", fake_send)
    monkeypatch.setattr(stream_alerts.telegram, "get_dashboard_url", lambda: "http://127.0.0.1:8002")

    stats = asyncio.run(send_pending_stream_alert_notifications(conn))

    assert stats == {"pending": 1, "sent": 1, "failed": 0}
    assert conn.updated_status == "sent"
    assert conn.updated_fingerprints == ["fp1"]


def test_send_pending_stream_alert_notifications_marks_failed(monkeypatch):
    class Conn:
        def __init__(self):
            self.updated_status = None

        async def fetch(self, sql, *args):
            return [{
                "fingerprint": "fp1",
                "alert_type": "TERM_BURST",
                "entity_id": None,
                "source": "telegram",
                "window_start": datetime(2026, 8, 10, 12, tzinfo=timezone.utc),
                "window_end": datetime(2026, 8, 10, 13, tzinfo=timezone.utc),
                "last_sent_at": None,
                "count": 12,
                "status": "pending",
                "detail": {"term": "#topic"},
            }]

        async def execute(self, sql, *args):
            self.updated_status = args[0]

    async def fake_send(text, **kwargs):
        return False

    conn = Conn()
    monkeypatch.setattr(stream_alerts.telegram, "send", fake_send)
    monkeypatch.setattr(stream_alerts.telegram, "get_dashboard_url", lambda: "http://127.0.0.1:8002")

    stats = asyncio.run(send_pending_stream_alert_notifications(conn))

    assert stats == {"pending": 1, "sent": 0, "failed": 1}
    assert conn.updated_status == "notify_failed"
