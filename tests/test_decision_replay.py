from __future__ import annotations

import json
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.pipeline.decision_replay import (
    BackupRequiredError,
    DERIVED_REBUILD_BY_EVENT,
    apply_decision_replay,
    dry_run_decision_replay,
    stable_refs_from_event,
)


def _event(event_type: str = "merge_confirmed", payload: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "audit_id": 1,
        "event_type": event_type,
        "actor": "dashboard",
        "entity_ids": ["00000000-0000-0000-0000-000000000001"],
        "payload": payload or {},
        "created_at": "2026-07-20T02:00:00.123456+00:00",
        "prev_sha256": None,
        "sha256": "a" * 64,
        "idempotency_key": "b" * 64,
    }


def _snapshot(source: str = "instagram", platform_id: str = "123", username: str = "alice") -> dict:
    return {
        "entity_id": "00000000-0000-0000-0000-000000000001",
        "canonical_name": "Alice",
        "platform_links": [
            {
                "source": source,
                "platform_id": platform_id,
                "platform_username": username,
                "platform_name": "Alice",
            }
        ],
    }


def _write_event(root: Path, event: dict, name: str = "2026-07.jsonl") -> None:
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


class FakeConn:
    def __init__(
        self,
        rows_by_source: dict[str, list[dict]],
        *,
        backup_row: dict | None = None,
        execute_result: str = "INSERT 0 1",
    ):
        self.rows_by_source = rows_by_source
        self.backup_row = backup_row
        self.execute_result = execute_result
        self.executed: list[tuple[str, tuple]] = []

    async def fetch(self, _sql, source, _platform_id, _username):
        return self.rows_by_source.get(source, [])

    async def fetchrow(self, sql, *args):
        if "FROM analyzer_backup_runs" in sql:
            return self.backup_row
        raise AssertionError(f"Unexpected SQL: {sql}")

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "setval" in sql:
            return "SELECT 1"
        return self.execute_result


def test_stable_refs_from_event_extracts_platform_links():
    event = _event(payload={"entity_snapshots_before": [_snapshot()]})

    refs = stable_refs_from_event(event)

    assert refs == [
        {
            "source": "instagram",
            "platform_id": "123",
            "platform_username": "alice",
        }
    ]


def test_stable_refs_from_event_prefers_explicit_event_refs():
    event = _event(payload={})
    event["stable_refs"] = [
        {
            "source": "telegram",
            "platform_id": "42",
            "platform_username": None,
            "media_sha256": None,
            "sidecar_path": None,
        }
    ]

    refs = stable_refs_from_event(event)

    assert refs == [
        {
            "source": "telegram",
            "platform_id": "42",
            "platform_username": None,
        }
    ]


def test_decision_replay_marks_restorable_event(tmp_path):
    _write_event(tmp_path, _event(payload={"entity_snapshots_before": [_snapshot()]}))
    conn = FakeConn({"instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}]})

    report = asyncio.run(dry_run_decision_replay(conn, log_dir=tmp_path))

    assert report.scanned == 1
    assert report.restorable == 1
    assert report.events[0].resolved_entity_ids == ["eeeeeeee-0000-0000-0000-000000000001"]
    assert "identity_scores" in report.events[0].derived_rebuild_required


def test_decision_replay_marks_no_reference_event_unresolved(tmp_path):
    _write_event(tmp_path, _event(event_type="add_note", payload={"notes": "manual note"}))

    report = asyncio.run(dry_run_decision_replay(FakeConn({}), log_dir=tmp_path))

    assert report.unresolved == 1
    assert report.events[0].reason == "no stable platform references in decision payload"


def test_decision_replay_accepts_legacy_event_names_as_unresolved_not_invalid(tmp_path):
    _write_event(tmp_path, _event(event_type="dismiss_match", payload={}))

    report = asyncio.run(dry_run_decision_replay(FakeConn({}), log_dir=tmp_path))

    assert report.invalid == 0
    assert report.unresolved == 1
    assert report.events[0].event_type == "dismiss_identity_candidate"
    assert "identity_scores" in report.events[0].derived_rebuild_required


def test_decision_replay_marks_ambiguous_reference(tmp_path):
    _write_event(tmp_path, _event(payload={"entity_snapshots_before": [_snapshot()]}))
    conn = FakeConn({
        "instagram": [
            {"entity_id": "eeeeeeee-0000-0000-0000-000000000001"},
            {"entity_id": "eeeeeeee-0000-0000-0000-000000000002"},
        ]
    })

    report = asyncio.run(dry_run_decision_replay(conn, log_dir=tmp_path))

    assert report.ambiguous == 1
    assert report.events[0].status == "ambiguous"


def test_decision_replay_marks_invalid_jsonl_event(tmp_path):
    bad = _event()
    bad["sha256"] = "bad"
    _write_event(tmp_path, bad)

    report = asyncio.run(dry_run_decision_replay(FakeConn({}), log_dir=tmp_path))

    assert report.invalid == 1
    assert report.events[0].status == "invalid"


def test_decision_replay_unresolved_only_output_filters_restorable_events(tmp_path):
    _write_event(tmp_path, _event(payload={"entity_snapshots_before": [_snapshot()]}))
    _write_event(
        tmp_path,
        _event(event_type="add_note", payload={"notes": "manual note"}),
        name="2026-08.jsonl",
    )
    conn = FakeConn({"instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}]})

    report = asyncio.run(dry_run_decision_replay(conn, log_dir=tmp_path))
    data = report.to_dict(unresolved_only=True)

    assert report.scanned == 2
    assert [event["status"] for event in data["events"]] == ["unresolved"]


def test_apply_decision_replay_requires_backup_guard(tmp_path):
    _write_event(tmp_path, _event(payload={"entity_snapshots_before": [_snapshot()]}))

    try:
        asyncio.run(apply_decision_replay(FakeConn({}), log_dir=tmp_path))
    except BackupRequiredError as exc:
        assert "requires a successful analyzer DB backup" in str(exc)
    else:
        raise AssertionError("backup guard did not stop replay apply")


def test_apply_decision_replay_rejects_stale_backup_guard(tmp_path):
    _write_event(tmp_path, _event(payload={"entity_snapshots_before": [_snapshot()]}))
    conn = FakeConn(
        {},
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 100,
            "finished_at": datetime.now(timezone.utc) - timedelta(hours=49),
            "restore_validation": "ok",
        },
    )

    try:
        asyncio.run(apply_decision_replay(conn, log_dir=tmp_path, backup_max_age_hours=24))
    except BackupRequiredError:
        pass
    else:
        raise AssertionError("stale backup guard did not stop replay apply")


def test_apply_decision_replay_rejects_failed_restore_validation(tmp_path):
    _write_event(tmp_path, _event(payload={"entity_snapshots_before": [_snapshot()]}))
    conn = FakeConn(
        {},
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 100,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "failed: pg_restore --list failed",
        },
    )

    try:
        asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))
    except BackupRequiredError:
        pass
    else:
        raise AssertionError("failed restore validation did not stop replay apply")


def test_apply_decision_replay_restores_audit_log_rows(tmp_path):
    event = _event(payload={"entity_snapshots_before": [_snapshot()]})
    _write_event(tmp_path, event)
    conn = FakeConn(
        {"instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}]},
        backup_row={
            "path": "/app/backups/db/unifiedanalyzer_daily_20260720T020000Z.dump",
            "size_bytes": 1234,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.applied == 1
    assert report.skipped_existing == 0
    insert_sql, insert_args = next(item for item in conn.executed if "INSERT INTO audit_log" in item[0])
    assert "ON CONFLICT (id) DO NOTHING" in insert_sql
    assert insert_args[0] == event["audit_id"]
    assert insert_args[3] == event["event_type"]
    assert report.replay.restorable == 1
    assert report.effect_unsupported == 1


def test_apply_decision_replay_can_skip_existing_rows(tmp_path):
    _write_event(tmp_path, _event(payload={"entity_snapshots_before": [_snapshot()]}))
    conn = FakeConn(
        {"instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}]},
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
        execute_result="INSERT 0 0",
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.applied == 0
    assert report.skipped_existing == 1


def test_apply_decision_replay_skips_effects_for_unresolved_events(tmp_path):
    _write_event(tmp_path, _event(event_type="add_note", payload={"notes": "manual note"}))
    conn = FakeConn(
        {},
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.audit_applied == 1
    assert report.effect_skipped_unresolved == 1


def test_apply_decision_replay_applies_dismiss_effect(tmp_path):
    _write_event(tmp_path, _event(
        event_type="dismiss_identity_candidate",
        payload={
            "entity_snapshots": [_snapshot("telegram", "1", "a"), _snapshot("instagram", "2", "b")],
            "candidate_evidence": {
                "sources": {
                    "contributing_signals": [
                        {"type": "email_match", "confidence": 0.6},
                        {"type": "email_match", "confidence": 0.4},
                        {"type": "bio_mention", "confidence": 0.9},
                    ],
                },
            },
        },
    ))
    conn = FakeConn(
        {
            "telegram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}],
            "instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000002"}],
        },
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.effect_applied == 1
    insert_sql, insert_args = next((sql, args) for sql, args in conn.executed if "INSERT INTO identity_labels" in sql)
    assert "features = EXCLUDED.features" in insert_sql
    assert json.loads(insert_args[2]) == {"email_match": 0.6, "bio_mention": 0.9}
    assert any("DELETE FROM entity_relationships" in sql for sql, _ in conn.executed)


def test_apply_decision_replay_preserves_dismiss_evidence_snapshot(tmp_path):
    event = _event(
        event_type="dismiss_identity_candidate",
        payload={
            "features": {},
            "entity_snapshots": [_snapshot("telegram", "1", "a"), _snapshot("instagram", "2", "b")],
        },
    )
    event["evidence_snapshot"] = {
        "candidate_evidence": {
            "sources": {
                "contributing_signals": [
                    {"type": "phone_match", "confidence": 0.8},
                    {"type": "phone_match", "confidence": 0.5},
                    {"type": "shared_life_context", "confidence": 0.9},
                ],
            },
        },
    }
    _write_event(tmp_path, event)
    conn = FakeConn(
        {
            "telegram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}],
            "instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000002"}],
        },
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.effect_applied == 1
    _insert_sql, insert_args = next((sql, args) for sql, args in conn.executed if "INSERT INTO identity_labels" in sql)
    assert json.loads(insert_args[2]) == {"phone_match": 0.8, "shared_life_context": 0.9}


def test_apply_decision_replay_preserves_legacy_action_but_applies_canonical_effect(tmp_path):
    event = _event(
        event_type="dismiss_match",
        payload={
            "entity_snapshots": [_snapshot("telegram", "1", "a"), _snapshot("instagram", "2", "b")],
            "features": {"email_match": 0.6},
        },
    )
    _write_event(tmp_path, event)
    conn = FakeConn(
        {
            "telegram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}],
            "instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000002"}],
        },
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.effect_applied == 1
    audit_sql, audit_args = next((sql, args) for sql, args in conn.executed if "INSERT INTO audit_log" in sql)
    assert "INSERT INTO audit_log" in audit_sql
    assert audit_args[3] == "dismiss_match"
    insert_sql, insert_args = next((sql, args) for sql, args in conn.executed if "INSERT INTO identity_labels" in sql)
    assert "identity_labels" in insert_sql
    assert json.loads(insert_args[2]) == {"email_match": 0.6}


def test_apply_decision_replay_applies_same_person_relationship_confirm(tmp_path):
    _write_event(tmp_path, _event(
        event_type="confirm_relationship",
        payload={
            "relationship_type": "same_person_probability",
            "entity_snapshots": [_snapshot("telegram", "1", "a"), _snapshot("instagram", "2", "b")],
            "relationship_snapshot": {
                "sources": {
                    "contributing_signals": [
                        {"type": "phone_match", "confidence": 0.9},
                        {"type": "email_match", "confidence": 0.7},
                    ],
                },
            },
        },
    ))
    conn = FakeConn(
        {
            "telegram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}],
            "instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000002"}],
        },
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.effect_applied == 1
    insert_sql, insert_args = next((sql, args) for sql, args in conn.executed if "INSERT INTO identity_labels" in sql)
    assert "label = EXCLUDED.label" in insert_sql
    assert json.loads(insert_args[2]) == {"phone_match": 0.9, "email_match": 0.7}
    assert insert_args[3] == 1


def test_apply_decision_replay_applies_same_person_relationship_reject(tmp_path):
    _write_event(tmp_path, _event(
        event_type="reject_relationship",
        payload={
            "relationship_type": "same_person_probability",
            "entity_snapshots": [_snapshot("telegram", "1", "a"), _snapshot("instagram", "2", "b")],
            "relationship_snapshot": {"sources": {"contributing_signals": {"phone_match": 0.8}}},
        },
    ))
    conn = FakeConn(
        {
            "telegram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}],
            "instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000002"}],
        },
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.effect_applied == 1
    insert_sql, insert_args = next((sql, args) for sql, args in conn.executed if "INSERT INTO identity_labels" in sql)
    assert json.loads(insert_args[2]) == {"phone_match": 0.8}
    assert insert_args[3] == 0
    assert any("DELETE FROM entity_relationships" in sql for sql, _ in conn.executed)


def test_apply_decision_replay_applies_source_confidence(tmp_path):
    _write_event(tmp_path, _event(
        event_type="adjust_source_confidence",
        payload={
            "source": "instagram",
            "platform_id": "123",
            "confidence": 87.5,
            "entity_snapshot": [_snapshot("instagram", "123", "alice")],
        },
    ))
    conn = FakeConn(
        {"instagram": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}]},
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.effect_applied == 1
    update_sql, update_args = next((sql, args) for sql, args in conn.executed if "UPDATE entity_platform_links" in sql)
    assert "platform_id = $4" in update_sql
    assert update_args == (87.5, "eeeeeeee-0000-0000-0000-000000000001", "instagram", "123")


def test_apply_decision_replay_applies_location_decision(tmp_path):
    _write_event(tmp_path, _event(
        event_type="reject_location",
        payload={
            "is_correct": False,
            "location_ref": {
                "source": "strava",
                "evidence_type": "route_polyline",
                "source_table": "strava_activities",
                "source_record_id": "activity-1",
                "evidence_key": "c" * 64,
                "confidence": 0.75,
            },
            "entity_snapshot": [_snapshot("strava", "72101656", "bryanseah234")],
        },
    ))
    conn = FakeConn(
        {"strava": [{"entity_id": "eeeeeeee-0000-0000-0000-000000000001"}]},
        backup_row={
            "path": "/backup.dump",
            "size_bytes": 1,
            "finished_at": datetime.now(timezone.utc),
            "restore_validation": "ok",
        },
    )

    report = asyncio.run(apply_decision_replay(conn, log_dir=tmp_path))

    assert report.effect_applied == 1
    location_sql, location_args = next(
        (sql, args) for sql, args in conn.executed if "INSERT INTO location_evidence" in sql
    )
    assert "status = EXCLUDED.status" in location_sql
    assert location_args[0] == "c" * 64
    assert location_args[13] == "rejected"


def test_reject_media_decisions_have_rebuild_mapping():
    assert DERIVED_REBUILD_BY_EVENT["reject_media_owner"] == ("media_attribution", "timeline_events")
    assert DERIVED_REBUILD_BY_EVENT["reject_person_in_photo"] == ("face_links", "timeline_events")


def test_all_supported_decision_events_have_replay_mapping():
    from src.util.audit_log import DECISION_EVENT_TYPES

    assert DECISION_EVENT_TYPES <= set(DERIVED_REBUILD_BY_EVENT)
