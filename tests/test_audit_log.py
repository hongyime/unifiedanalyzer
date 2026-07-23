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
            self.executed = []

        async def fetchrow(self, sql, *args):
            if "SELECT sha256 FROM audit_log" in sql:
                return None
            if "SELECT NOW() AS ts" in sql:
                return {"ts": now}
            if "INSERT INTO audit_log" in sql:
                self.insert_args = args
                return {
                    "id": 42,
                    "created_at": now,
                    "prev_sha256": args[0],
                    "sha256": args[1],
                    "decision_jsonl_written_at": None,
                }
            raise AssertionError(f"Unexpected SQL: {sql}")

        async def execute(self, sql, *args):
            self.executed.append((sql, args))
            return "UPDATE 1"

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
    assert any("decision_jsonl_written_at = NOW()" in sql for sql, _ in conn.executed)


def test_append_audit_writes_stable_refs_and_evidence_snapshot(tmp_path, monkeypatch):
    import src.util.audit_log as audit_log

    monkeypatch.setattr(audit_log, "DECISION_LOG_DIR", tmp_path)
    now = datetime(2026, 7, 20, 2, 0, 0, 123456, tzinfo=timezone.utc)

    class Conn:
        async def fetchrow(self, sql, *args):
            if "SELECT sha256 FROM audit_log" in sql:
                return None
            if "SELECT NOW() AS ts" in sql:
                return {"ts": now}
            if "INSERT INTO audit_log" in sql:
                return {
                    "id": 46,
                    "created_at": now,
                    "prev_sha256": args[0],
                    "sha256": args[1],
                    "decision_jsonl_written_at": None,
                }
            raise AssertionError(f"Unexpected SQL: {sql}")

        async def execute(self, _sql, *_args):
            return "UPDATE 1"

    asyncio.run(
        audit_log.append_audit(
            Conn(),
            action="confirm_relationship",
            actor="dashboard",
            entity_ids=["00000000-0000-0000-0000-000000000001"],
            payload={
                "confidence": 96,
                "evidence_refs": {"signal_id": "sig-1"},
                "entity_snapshot": [{
                    "entity_id": "00000000-0000-0000-0000-000000000001",
                    "platform_links": [{
                        "source": "instagram",
                        "platform_id": "123",
                        "platform_username": "alice",
                    }],
                }],
            },
        )
    )

    event = json.loads((tmp_path / "2026-07.jsonl").read_text(encoding="utf-8"))
    assert event["stable_refs"] == [{
        "source": "instagram",
        "platform_id": "123",
        "platform_username": "alice",
        "media_sha256": None,
        "sidecar_path": None,
    }]
    assert event["evidence_snapshot"] == {
        "confidence": 96,
        "evidence_refs": {"signal_id": "sig-1"},
    }


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
        def __init__(self):
            self.executed = []

        async def fetchrow(self, sql, *args):
            if "SELECT sha256 FROM audit_log" in sql:
                return None
            if "SELECT NOW() AS ts" in sql:
                return {"ts": now}
            if "INSERT INTO audit_log" in sql:
                return {
                    "id": 43,
                    "created_at": now,
                    "prev_sha256": args[0],
                    "sha256": args[1],
                    "decision_jsonl_written_at": None,
                }
            raise AssertionError(f"Unexpected SQL: {sql}")

        async def execute(self, sql, *args):
            self.executed.append((sql, args))
            return "UPDATE 1"

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
    assert any("decision_jsonl_error" in sql for sql, _ in conn.executed)


def test_retry_pending_decision_jsonl_writes_and_clears_error(tmp_path, monkeypatch):
    import src.util.audit_log as audit_log

    monkeypatch.setattr(audit_log, "DECISION_LOG_DIR", tmp_path)
    now = datetime(2026, 7, 20, 2, 0, 0, 123456, tzinfo=timezone.utc)
    idem = "a" * 64

    class Conn:
        def __init__(self):
            self.executed = []

        async def fetch(self, sql, *args):
            assert "decision_jsonl_written_at IS NULL" in sql
            return [{
                "id": 44,
                "prev_sha256": None,
                "sha256": "b" * 64,
                "action": "add_note",
                "actor": "dashboard",
                "entity_ids": ["00000000-0000-0000-0000-000000000001"],
                "payload": {"notes": "keep"},
                "created_at": now,
                "idempotency_key": idem,
            }]

        async def execute(self, sql, *args):
            self.executed.append((sql, args))
            return "UPDATE 1"

    conn = Conn()
    stats = asyncio.run(audit_log.retry_pending_decision_jsonl(conn))

    assert stats == {"pending": 1, "already_present": 0, "written": 1, "failed": 0}
    event = json.loads((tmp_path / "2026-07.jsonl").read_text(encoding="utf-8"))
    assert event["audit_id"] == 44
    assert event["idempotency_key"] == idem
    assert any("decision_jsonl_written_at = NOW()" in sql for sql, _ in conn.executed)


def test_retry_pending_decision_jsonl_marks_existing_event_without_duplicate(tmp_path, monkeypatch):
    import src.util.audit_log as audit_log

    monkeypatch.setattr(audit_log, "DECISION_LOG_DIR", tmp_path)
    now = datetime(2026, 7, 20, 2, 0, 0, 123456, tzinfo=timezone.utc)
    existing = {
        "schema_version": 1,
        "audit_id": 45,
        "event_type": "add_note",
        "actor": "dashboard",
        "entity_ids": ["00000000-0000-0000-0000-000000000001"],
        "payload": {"notes": "keep"},
        "created_at": now.isoformat(timespec="microseconds"),
        "prev_sha256": None,
        "sha256": "c" * 64,
        "idempotency_key": "d" * 64,
    }
    path = tmp_path / "2026-07.jsonl"
    path.write_text(json.dumps(existing) + "\n", encoding="utf-8")

    class Conn:
        def __init__(self):
            self.executed = []

        async def fetch(self, sql, *args):
            return [{
                "id": 45,
                "prev_sha256": None,
                "sha256": "c" * 64,
                "action": "add_note",
                "actor": "dashboard",
                "entity_ids": ["00000000-0000-0000-0000-000000000001"],
                "payload": {"notes": "keep"},
                "created_at": now,
                "idempotency_key": "d" * 64,
            }]

        async def execute(self, sql, *args):
            self.executed.append((sql, args))
            return "UPDATE 1"

    conn = Conn()
    stats = asyncio.run(audit_log.retry_pending_decision_jsonl(conn))

    assert stats == {"pending": 1, "already_present": 1, "written": 0, "failed": 0}
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert any("decision_jsonl_written_at = NOW()" in sql for sql, _ in conn.executed)
