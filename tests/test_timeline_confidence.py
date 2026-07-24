import asyncio
from datetime import datetime, timezone
from uuid import UUID

from src.api.routes import timeline
from src.api.routes.timeline import (
    _SOURCE_LINK_CONFIDENCE_SOURCE,
    _coerce_confidence,
    _derive_timeline_confidence,
    _timeline_event_payload,
)


ENTITY_ID = "00000000-0000-0000-0000-000000000001"


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


class _TimelineConn:
    def __init__(self):
        self.count_call = None
        self.fetch_call = None

    async def fetchval(self, sql, *args):
        if "SELECT 1 FROM entities" in sql:
            return 1
        if "COUNT(*) FROM timeline_events" in sql:
            self.count_call = (sql, args)
            return 1
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql, *args):
        self.fetch_call = (sql, args)
        return [
            {
                "id": UUID(ENTITY_ID),
                "source": "instagram",
                "event_type": "CONTENT_PUBLISHED",
                "source_record_id": "post-1",
                "occurred_at": datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
                "title": "Post",
                "metadata": {"likes_count": 12},
                "source_confidence": 75,
            }
        ]


def test_coerce_confidence_normalizes_fraction_percent_and_whole_number():
    assert _coerce_confidence(0.82) == 0.82
    assert _coerce_confidence(82) == 0.82
    assert _coerce_confidence("82%") == 0.82


def test_coerce_confidence_rejects_invalid_or_negative_values():
    assert _coerce_confidence(None) is None
    assert _coerce_confidence(True) is None
    assert _coerce_confidence("high") is None
    assert _coerce_confidence(-1) is None


def test_derive_timeline_confidence_prefers_semantic_metadata_keys():
    confidence, source = _derive_timeline_confidence({
        "confidence": 90,
        "score": 10,
    })

    assert confidence == 0.9
    assert source == "metadata.confidence"


def test_derive_timeline_confidence_reads_nested_source_evidence():
    confidence, source = _derive_timeline_confidence({
        "source_evidence": {"confidence": "0.76"},
    })

    assert confidence == 0.76
    assert source == "metadata.source_evidence.confidence"


def test_derive_timeline_confidence_exposes_unknown_state():
    assert _derive_timeline_confidence({"likes_count": 12}) == (None, None)


def test_timeline_event_payload_falls_back_to_source_link_confidence():
    payload = _timeline_event_payload({
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "source": "instagram",
        "event_type": "CONTENT_PUBLISHED",
        "source_record_id": "post-1",
        "occurred_at": datetime(2026, 7, 24, 8, 0, tzinfo=timezone.utc),
        "title": "Post",
        "metadata": {"likes_count": 12},
        "source_confidence": 75,
    })

    assert payload["confidence"] == 0.75
    assert payload["confidence_source"] == _SOURCE_LINK_CONFIDENCE_SOURCE


def test_get_entity_timeline_min_confidence_uses_source_link_fallback(monkeypatch):
    conn = _TimelineConn()
    monkeypatch.setattr(timeline, "get_analyzer_pool", lambda: _Pool(conn))

    result = asyncio.run(
        timeline.get_entity_timeline(
            ENTITY_ID,
            page=1,
            per_page=50,
            from_date=None,
            to_date=None,
            min_confidence=0.7,
        )
    )

    assert result["total"] == 1
    assert result["data"][0]["confidence"] == 0.75
    count_sql, count_args = conn.count_call
    fetch_sql, fetch_args = conn.fetch_call
    assert "entity_platform_links" in count_sql
    assert "entity_platform_links" in fetch_sql
    assert count_args == (ENTITY_ID, 0.7)
    assert fetch_args == (ENTITY_ID, 0.7, 50, 0)
