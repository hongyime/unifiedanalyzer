import asyncio
from datetime import datetime, timedelta, timezone

from src.api.routes import alerts, collector_health, media


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def test_alert_windows_route_decodes_detail_and_filters(monkeypatch):
    now = datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)

    class Conn:
        def __init__(self):
            self.args = None

        async def fetch(self, _sql, *args):
            self.args = args
            return [{
                "bucket_type": "term",
                "bucket_key": "#test",
                "source": "telegram",
                "window_start": now - timedelta(minutes=5),
                "window_end": now,
                "count": 12,
                "baseline": 3.5,
                "detail": '{"term":"#test"}',
                "created_at": now,
                "updated_at": now,
            }]

    conn = Conn()
    monkeypatch.setattr(alerts, "get_analyzer_pool", lambda: _Pool(conn))

    result = asyncio.run(alerts.list_alert_windows(bucket_type="term", source="telegram", limit=25))

    assert conn.args == ("term", "telegram", 25)
    assert result["total"] == 1
    assert result["data"][0]["detail"] == {"term": "#test"}
    assert result["data"][0]["baseline"] == 3.5


def test_alert_suppression_patch_and_delete(monkeypatch):
    now = datetime(2026, 8, 12, 3, 0, tzinfo=timezone.utc)

    class Conn:
        def __init__(self):
            self.calls = []

        async def fetchrow(self, sql, *args):
            self.calls.append((sql, args))
            return {
                "id": "00000000-0000-0000-0000-000000000001",
                "scope": "manual",
                "alert_type": "TERM_BURST",
                "entity_id": None,
                "source": "telegram",
                "reason": args[1] if len(args) > 1 and args[1] else "noise",
                "starts_at": now - timedelta(hours=1),
                "ends_at": now,
                "created_at": now - timedelta(hours=1),
            }

    conn = Conn()
    monkeypatch.setattr(alerts, "get_analyzer_pool", lambda: _Pool(conn))

    patched = asyncio.run(alerts.update_alert_suppression(
        "00000000-0000-0000-0000-000000000001",
        alerts.AlertSuppressionPatch(reason="maintenance", status="expired"),
    ))
    deleted = asyncio.run(alerts.expire_alert_suppression("00000000-0000-0000-0000-000000000001"))

    assert patched["reason"] == "maintenance"
    assert deleted["ok"] is True
    assert len(conn.calls) == 2
    assert "UPDATE alert_suppressions" in conn.calls[0][0]


def test_collector_coverage_route_returns_summary_total_and_snapshot_age(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [
        {
            "source": "telegram",
            "expected_cadence": "00:05:00",
            "latest_data_at": now - timedelta(minutes=2),
            "latest_run_at": now - timedelta(minutes=2),
            "status": "fresh",
            "rows_24h": 100,
            "media_24h": 10,
            "errors_24h": 0,
            "rate_limits_24h": 0,
            "private_access_failures": 0,
            "stale_targets": 0,
            "created_at": now - timedelta(minutes=10),
        },
        {
            "source": "x",
            "expected_cadence": "00:30:00",
            "latest_data_at": now - timedelta(hours=4),
            "latest_run_at": now - timedelta(hours=4),
            "status": "stale",
            "rows_24h": 0,
            "media_24h": 0,
            "errors_24h": 1,
            "rate_limits_24h": 0,
            "private_access_failures": 0,
            "stale_targets": 2,
            "created_at": now - timedelta(minutes=10),
        },
    ]

    class Conn:
        async def fetch(self, _sql, *args):
            return rows

    monkeypatch.setattr(collector_health, "get_collector_pool", lambda: _Pool(Conn()))

    result = asyncio.run(collector_health.collector_coverage())

    assert result["collector_db"] == "connected"
    assert result["total"] == 2
    assert result["summary"] == {"total": 2, "fresh": 1, "degraded": 0, "stale": 1}
    assert result["snapshot_age_seconds"] >= 0
    assert result["snapshot_stale"] is False


def test_media_coverage_route_reports_named_production_surfaces(monkeypatch):
    class Conn:
        async def fetchrow(self, _sql, *args):
            assert not args
            return {
                "rows_total": 25,
                "items_total": 10,
                "pdf_text_rows": 3,
                "pdf_text_with_text": 2,
                "pdf_image_markers": 1,
                "pdf_image_rows": 4,
                "ocr_rows": 5,
                "ocr_with_text": 4,
                "video_frame_markers": 2,
                "video_frame_rows": 8,
                "face_rows": 6,
                "exif_rows": 7,
                "exif_with_gps": 1,
                "phash_rows": 9,
                "derived_rows": 12,
            }

        async def fetch(self, _sql, *args):
            assert args[0] == ["pdf_text", "ocr_text"]
            return [
                {"source_column": "pdf_text", "signal_type": "email_match", "n": 2},
                {"source_column": "ocr_text", "signal_type": "phone_match", "n": 1},
            ]

    monkeypatch.setattr(media, "get_analyzer_pool", lambda: _Pool(Conn()))

    result = asyncio.run(media.media_coverage(exact=True))

    by_key = {item["key"]: item for item in result["coverage"]}
    assert result["rows_total"] == 25
    assert result["derived_rows"] == 12
    assert by_key["pdf_text"]["count"] == 2
    assert by_key["pdf_text"]["processed"] == 3
    assert by_key["pdf_images"]["count"] == 4
    assert by_key["video_frames"]["count"] == 8
    assert by_key["exif_gps"]["count"] == 1
    assert by_key["contact_signals"]["count"] == 3
    assert result["contact_signals"]["by_source_column"][0]["source_column"] == "pdf_text"
