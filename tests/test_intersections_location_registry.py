import asyncio
from datetime import datetime, timezone

from src.api.routes import intersections
from src.pipeline.location_evidence import attach_location_evidence_key


ENTITY_A = "00000000-0000-0000-0000-000000000001"
ENTITY_B = "00000000-0000-0000-0000-000000000002"


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


class _Conn:
    def __init__(self, rejected_key: str):
        self.rejected_key = rejected_key
        self.executemany_rows = []

    async def executemany(self, _sql, rows):
        self.executemany_rows.extend(list(rows))

    async def fetch(self, _sql, _keys):
        return [
            {
                "evidence_key": self.rejected_key,
                "status": "rejected",
                "decision_notes": "bad GPS",
                "decided_at": None,
            }
        ]


def test_apply_location_registry_filters_rejected_points(monkeypatch):
    kept = intersections._point(
        ENTITY_A,
        "telegram",
        "msg-1",
        datetime(2026, 7, 24, 1, 0, tzinfo=timezone.utc),
        1.3001,
        103.8001,
        "meetup",
        evidence_type="message_location",
        source_table="telegram_message_locations",
        confidence=0.9,
    )
    rejected = intersections._point(
        ENTITY_B,
        "telegram",
        "msg-2",
        datetime(2026, 7, 24, 1, 1, tzinfo=timezone.utc),
        1.3002,
        103.8002,
        "meetup",
        evidence_type="message_location",
        source_table="telegram_message_locations",
        confidence=0.9,
    )
    rejected_key = attach_location_evidence_key(
        ENTITY_B,
        intersections._location_item_from_point(rejected),
    )["evidence_key"]
    conn = _Conn(rejected_key)
    monkeypatch.setattr(intersections, "get_analyzer_pool", lambda: _Pool(conn))

    visible, suppressed, materialized = asyncio.run(
        intersections._apply_location_registry([kept, rejected])
    )

    assert suppressed == 1
    assert materialized == 2
    assert len(visible) == 1
    assert visible[0]["record_id"] == "msg-1"
    assert visible[0]["evidence_key"]
    assert len(conn.executemany_rows) == 2
