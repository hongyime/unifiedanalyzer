from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone


def test_append_audit_writes_decision_jsonl(tmp_path, monkeypatch):
    import src.util.audit_log as audit_log

    monkeypatch.setattr(audit_log, "DECISION_LOG_DIR", tmp_path)
    now = datetime(2026, 7, 20, 2, 0, 0, 123456, tzinfo=timezone.utc)

    class Conn:
        def __init__(self):
            self.insert_args = None

        async def fetchrow(self, sql, *args):
            if "SELECT sha256 FROM audit_log" in sql:
                return None
            if "SELECT NOW() AS ts" in sql:
                return {"ts": now}
            if "INSERT INTO audit_log" in sql:
                self.insert_args = args
                return {"id": 42, "created_at": now}
            raise AssertionError(f"Unexpected SQL: {sql}")

    conn = Conn()
    audit_id = asyncio.run(
        audit_log.append_audit(
            conn,
            action="dismiss_identity_candidate",
            actor="dashboard",
            entity_ids=[
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ],
            payload={"confidence": "X"},
        )
    )

    assert audit_id == 42
    log_path = tmp_path / "2026-07.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["schema_version"] == 1
    assert event["audit_id"] == 42
    assert event["event_type"] == "dismiss_identity_candidate"
    assert event["actor"] == "dashboard"
    assert event["entity_ids"] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert event["payload"] == {"confidence": "X"}
    assert event["created_at"] == "2026-07-20T02:00:00.123456+00:00"
    assert event["prev_sha256"] is None
    assert event["sha256"] == conn.insert_args[1]
    assert len(event["idempotency_key"]) == 64


def test_append_audit_invalid_decision_event_is_nonfatal_and_not_written(
    tmp_path,
    monkeypatch,
    caplog,
):
    import src.util.audit_log as audit_log

    monkeypatch.setattr(audit_log, "DECISION_LOG_DIR", tmp_path)
    monkeypatch.setattr(audit_log, "_hash", lambda *args: "not-a-sha256")
    now = datetime(2026, 7, 20, 2, 0, 0, 123456, tzinfo=timezone.utc)

    class Conn:
        async def fetchrow(self, sql, *args):
            if "SELECT sha256 FROM audit_log" in sql:
                return None
            if "SELECT NOW() AS ts" in sql:
                return {"ts": now}
            if "INSERT INTO audit_log" in sql:
                return {"id": 43, "created_at": now}
            raise AssertionError(f"Unexpected SQL: {sql}")

    conn = Conn()
    with caplog.at_level(logging.ERROR, logger=audit_log.logger.name):
        audit_id = asyncio.run(
            audit_log.append_audit(
                conn,
                action="dismiss_identity_candidate",
                actor="dashboard",
                entity_ids=["00000000-0000-0000-0000-000000000001"],
                payload={"confidence": "X"},
            )
        )

    assert audit_id == 43
    assert not (tmp_path / "2026-07.jsonl").exists()
    assert "decision JSONL append failed (non-fatal)" in caplog.text
