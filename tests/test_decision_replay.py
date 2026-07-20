from __future__ import annotations

import json
import asyncio
from pathlib import Path

from src.pipeline.decision_replay import dry_run_decision_replay, stable_refs_from_event


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
    def __init__(self, rows_by_source: dict[str, list[dict]]):
        self.rows_by_source = rows_by_source

    async def fetch(self, _sql, source, _platform_id, _username):
        return self.rows_by_source.get(source, [])


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
