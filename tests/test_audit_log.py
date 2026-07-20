from __future__ import annotations

import asyncio
import json
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
    assert event["payload"] == {"confidence": "X"}
    assert event["sha256"] == conn.insert_args[1]
    assert len(event["idempotency_key"]) == 64
