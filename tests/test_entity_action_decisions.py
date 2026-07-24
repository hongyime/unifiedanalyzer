import asyncio

import pytest
from fastapi import HTTPException

from src.api.routes import entity_actions


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
    async def fetchval(self, sql, *args):
        if "COUNT(*) FROM entities" in sql:
            return len(set(args[0]))
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetchrow(self, sql, *args):
        if "FROM entity_relationships" in sql:
            return {
                "relationship_type": "same_person_probability",
                "weight": 87.5,
                "cross_platform": True,
                "sources": ["phone_match"],
            }
        if "FROM entity_platform_links" in sql:
            return None
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *args):
        if "FROM entities e" in sql:
            return []
        raise AssertionError(f"unexpected fetch: {sql}")


def test_relationship_decision_records_audit_and_label(monkeypatch):
    audits = []
    labels = []
    monkeypatch.setattr(entity_actions, "get_analyzer_pool", lambda: _Pool(_Conn()))

    async def fake_append_audit(conn, **kwargs):
        audits.append(kwargs)
        return 1

    async def fake_record_label(conn, entity_a, entity_b, label, source):
        labels.append((entity_a, entity_b, label, source))

    monkeypatch.setattr(entity_actions, "append_audit", fake_append_audit)
    monkeypatch.setattr(entity_actions, "record_label", fake_record_label)

    result = asyncio.run(
        entity_actions.decide_relationship(
            entity_actions.RelationshipDecisionRequest(
                entity_a="00000000-0000-0000-0000-000000000001",
                entity_b="00000000-0000-0000-0000-000000000002",
                relationship_type="same_person_probability",
                is_real=False,
                evidence_refs={"signal_id": "sig-1"},
            )
        )
    )

    assert result == {"ok": True, "action": "reject_relationship"}
    assert labels == [
        (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            0,
            "dashboard_relationship_decision",
        )
    ]
    assert audits[0]["action"] == "reject_relationship"
    assert audits[0]["payload"]["confidence"] is None
    assert audits[0]["payload"]["evidence_refs"] == {"signal_id": "sig-1"}
    assert audits[0]["payload"]["relationship_snapshot"]["weight"] == 87.5


def test_media_person_decision_rejects_unknown_role(monkeypatch):
    monkeypatch.setattr(entity_actions, "get_analyzer_pool", lambda: _Pool(_Conn()))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            entity_actions.decide_media_person(
                "00000000-0000-0000-0000-000000000001",
                entity_actions.MediaPersonDecisionRequest(
                    role="unknown",
                    media_ref={"source": "instagram", "content_id": "post_1"},
                ),
            )
        )

    assert exc.value.status_code == 400


def test_location_decision_records_audit_and_updates_registry(monkeypatch):
    audits = []
    updates = []
    monkeypatch.setattr(entity_actions, "get_analyzer_pool", lambda: _Pool(_Conn()))

    async def fake_append_audit(conn, **kwargs):
        audits.append(kwargs)
        return 44

    async def fake_apply_location_decision(conn, **kwargs):
        updates.append(kwargs)
        return {
            "updated": 1,
            "evidence_key": "a" * 64,
            "status": "confirmed",
        }

    monkeypatch.setattr(entity_actions, "append_audit", fake_append_audit)
    monkeypatch.setattr(entity_actions, "apply_location_decision", fake_apply_location_decision)

    result = asyncio.run(
        entity_actions.decide_location(
            "00000000-0000-0000-0000-000000000001",
            entity_actions.LocationDecisionRequest(
                is_correct=True,
                location_ref={
                    "source": "strava",
                    "evidence_type": "route_polyline",
                    "source_table": "strava_activities",
                    "source_record_id": "activity-1",
                },
            ),
        )
    )

    assert result["action"] == "confirm_location"
    assert result["status"] == "confirmed"
    assert audits[0]["action"] == "confirm_location"
    assert updates[0]["audit_id"] == 44
    assert updates[0]["is_correct"] is True
