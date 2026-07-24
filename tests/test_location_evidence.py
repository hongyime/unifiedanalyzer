import asyncio
from datetime import datetime, timezone

from src.pipeline.location_evidence import (
    apply_location_decision,
    attach_location_evidence_key,
    evidence_key_from_location_ref,
    location_evidence_key,
    upsert_location_evidence_batch,
)


ENTITY_ID = "00000000-0000-0000-0000-000000000001"


class FakeConn:
    def __init__(self):
        self.executed = []
        self.executemany_calls = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "INSERT 0 1"

    async def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))


def test_location_key_is_stable_for_source_record():
    first = location_evidence_key(
        entity_id=ENTITY_ID,
        source="strava",
        evidence_type="route_polyline",
        source_table="strava_activities",
        source_record_id="123",
        label="Morning run",
    )
    second = location_evidence_key(
        entity_id=ENTITY_ID,
        source="strava",
        evidence_type="route_polyline",
        source_table="strava_activities",
        source_record_id="123",
        label="Renamed run",
    )

    assert first == second
    assert len(first) == 64


def test_attach_location_evidence_key_adds_route_key():
    item = attach_location_evidence_key(
        ENTITY_ID,
        {
            "source": "strava",
            "evidence_type": "route_polyline",
            "source_table": "strava_activities",
            "source_record_id": "activity-1",
            "points": [[1.3, 103.8], [1.31, 103.81]],
        },
    )

    assert item["evidence_key"] == evidence_key_from_location_ref(ENTITY_ID, item)


def test_upsert_location_evidence_batch_materializes_rows():
    conn = FakeConn()
    count = asyncio.run(
        upsert_location_evidence_batch(
            conn,
            ENTITY_ID,
            [
                attach_location_evidence_key(
                    ENTITY_ID,
                    {
                        "source": "telegram",
                        "evidence_type": "message_location",
                        "source_table": "telegram_message_locations",
                        "source_record_id": "msg-1",
                        "occurred_at": datetime(2026, 7, 24, tzinfo=timezone.utc).isoformat(),
                        "lat": 1.3,
                        "lng": 103.8,
                        "confidence": 0.9,
                    },
                )
            ],
        )
    )

    assert count == 1
    assert len(conn.executemany_calls) == 1
    row = conn.executemany_calls[0][1][0]
    assert row[1] == ENTITY_ID
    assert row[2] == "telegram"
    assert row[10] == 0.9


def test_upsert_location_evidence_preserves_payload_details():
    conn = FakeConn()
    asyncio.run(
        upsert_location_evidence_batch(
            conn,
            ENTITY_ID,
            [
                attach_location_evidence_key(
                    ENTITY_ID,
                    {
                        "source": "instagram",
                        "evidence_type": "caption_derived",
                        "source_table": "instagram_posts",
                        "source_record_id": "post-1",
                        "lat": 1.283,
                        "lng": 103.86,
                        "confidence": 0.35,
                        "payload": {
                            "derivation": "caption_exact_geocache_match",
                            "matched_place": "Marina Bay Sands",
                        },
                    },
                )
            ],
        )
    )

    row = conn.executemany_calls[0][1][0]
    assert '"derivation": "caption_exact_geocache_match"' in row[12]
    assert '"matched_place": "Marina Bay Sands"' in row[12]


def test_apply_location_decision_marks_rejected_by_key():
    conn = FakeConn()
    ref = attach_location_evidence_key(
        ENTITY_ID,
        {
            "source": "drive",
            "evidence_type": "exif_gps",
            "source_table": "media_analysis",
            "source_record_id": "sha256",
            "lat": 1.3,
            "lng": 103.8,
            "confidence": 0.85,
        },
    )

    result = asyncio.run(
        apply_location_decision(
            conn,
            entity_id=ENTITY_ID,
            location_ref=ref,
            is_correct=False,
            confidence=55,
            notes="wrong place",
            audit_id=123,
            actor="dashboard",
        )
    )

    assert result["updated"] == 1
    assert result["status"] == "rejected"
    args = conn.executed[0][1]
    assert args[0] == ref["evidence_key"]
    assert args[10] == 0.55
    assert args[13] == "rejected"
    assert args[14] == 123
