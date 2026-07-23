import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from src.api.routes import entities


ENTITY_ID = "00000000-0000-0000-0000-000000000001"
OTHER_ID = "00000000-0000-0000-0000-000000000002"


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
    def __init__(self, *, exists=True):
        self.exists = exists
        self.fetch_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql, *args):
        if "SELECT 1 FROM entities" in sql:
            return 1 if self.exists else None
        raise AssertionError(f"unexpected fetchval: {sql}")

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        if "FROM audit_log" in sql:
            return [
                {
                    "id": 12,
                    "action": "dismiss_match",
                    "actor": "dashboard",
                    "entity_ids": [ENTITY_ID, OTHER_ID],
                    "payload": {"entity_a": ENTITY_ID, "entity_b": OTHER_ID, "confidence": "X"},
                    "created_at": datetime(2026, 7, 23, 10, 1, tzinfo=timezone.utc),
                    "decision_jsonl_path": "Z:/unifiedanalyzer/decisions/2026-07-23.jsonl",
                    "decision_jsonl_written_at": datetime(2026, 7, 23, 10, 2, tzinfo=timezone.utc),
                    "decision_jsonl_error": None,
                }
            ]
        if "canonical_name" in sql and "FROM entities" in sql:
            return [
                {"id": ENTITY_ID, "canonical_name": "Subject"},
                {"id": OTHER_ID, "canonical_name": "Other"},
            ]
        raise AssertionError(f"unexpected fetch: {sql}")


def test_entity_decisions_returns_decision_history(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(entities, "get_analyzer_pool", lambda: _Pool(conn))

    result = asyncio.run(entities.entity_decisions(ENTITY_ID, limit=25))

    assert result["entity_id"] == ENTITY_ID
    assert result["total"] == 1
    decision = result["decisions"][0]
    assert decision["action"] == "dismiss_match"
    assert decision["action_label"] == "Dismissed identity candidate"
    assert decision["summary"] == "Marked candidate as not same (00000000)"
    assert decision["entity_names"][OTHER_ID] == "Other"
    assert decision["durable"] is True
    assert decision["decision_jsonl_error"] is None
    audit_sql, audit_args = conn.fetch_calls[0]
    assert "FROM audit_log" in audit_sql
    assert audit_args == (ENTITY_ID, 25)


def test_entity_decisions_requires_existing_entity(monkeypatch):
    monkeypatch.setattr(entities, "get_analyzer_pool", lambda: _Pool(_Conn(exists=False)))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(entities.entity_decisions(ENTITY_ID))

    assert exc.value.status_code == 404
