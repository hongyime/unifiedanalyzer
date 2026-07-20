from __future__ import annotations

import asyncio
import json

from src.pipeline.collector_priority_hints import (
    HINT_TYPE_SAME_PERSON_95_99,
    build_collector_priority_hints,
    export_collector_priority_hints,
)

ENTITY_A = "00000000-0000-0000-0000-000000000001"
ENTITY_B = "00000000-0000-0000-0000-000000000002"
ENTITY_C = "00000000-0000-0000-0000-000000000003"


class Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeConn:
    def __init__(self, relationships: list[dict], links: list[dict]):
        self.relationships = relationships
        self.links = links
        self.executemany_calls: list[tuple[str, list[tuple]]] = []

    async def fetch(self, sql, *args):
        if "FROM entity_relationships" in sql:
            return self.relationships
        if "FROM entity_platform_links" in sql:
            return self.links
        raise AssertionError(f"Unexpected fetch SQL: {sql}")

    async def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))

    def transaction(self):
        return Tx()


def _relationship(
    *,
    relationship_id: str = "11111111-1111-1111-1111-111111111111",
    entity_a_id: str = ENTITY_A,
    entity_b_id: str = ENTITY_B,
    weight: float | int = 96,
    confidence: float | None = 0.96,
) -> dict:
    return {
        "relationship_id": relationship_id,
        "entity_a_id": entity_a_id,
        "entity_b_id": entity_b_id,
        "weight": weight,
        "confidence": confidence,
        "sources": {
            "score": confidence,
            "method": "identity_scorer",
            "contributing_signals": {"phone_match": 1.0},
        },
    }


def _link(
    entity_id: str,
    source: str,
    platform_id: str,
    username: str | None = None,
) -> dict:
    return {
        "entity_id": entity_id,
        "source": source,
        "platform_id": platform_id,
        "platform_username": username,
        "platform_name": username,
        "link_confidence": 0.9,
        "link_method": "test",
        "is_confirmed": False,
    }


def test_priority_hints_dry_run_builds_bidirectional_hints_without_writing():
    conn = FakeConn(
        relationships=[_relationship()],
        links=[
            _link(ENTITY_A, "telegram", "tg-1", "base"),
            _link(ENTITY_B, "instagram", "ig-2", "candidate"),
            _link(ENTITY_B, "website", "https://example.test", None),
        ],
    )

    report = asyncio.run(export_collector_priority_hints(conn, write=False))

    assert report.dry_run is True
    assert report.planned == 2
    assert report.written == 0
    assert conn.executemany_calls == []

    by_target = {(hint.source, hint.target_id): hint for hint in report.hints}
    assert ("instagram", "ig-2") in by_target
    assert ("telegram", "tg-1") in by_target
    assert ("website", "https://example.test") not in by_target

    ig_hint = by_target[("instagram", "ig-2")]
    assert ig_hint.hint_type == HINT_TYPE_SAME_PERSON_95_99
    assert ig_hint.confidence == 0.96
    assert ig_hint.entity_id == ENTITY_A
    assert ig_hint.candidate_entity_id == ENTITY_B
    assert ig_hint.evidence["policy"] == "same_person_probability_95_99_no_auto_merge"
    assert ig_hint.evidence["relationship_sources"]["contributing_signals"] == {"phone_match": 1.0}


def test_priority_hints_write_upserts_into_analyzer_table():
    conn = FakeConn(
        relationships=[_relationship()],
        links=[
            _link(ENTITY_A, "telegram", "tg-1", "base"),
            _link(ENTITY_B, "instagram", "ig-2", "candidate"),
        ],
    )

    report = asyncio.run(export_collector_priority_hints(conn, write=True))

    assert report.dry_run is False
    assert report.planned == 2
    assert report.written == 2
    assert len(conn.executemany_calls) == 1

    sql, rows = conn.executemany_calls[0]
    assert "INSERT INTO collector_priority_hints" in sql
    assert len(rows) == 2
    first = rows[0]
    assert first[5] == HINT_TYPE_SAME_PERSON_95_99
    assert first[10] == "active"
    assert json.loads(first[9])["policy"] == "same_person_probability_95_99_no_auto_merge"


def test_priority_hints_filters_low_confidence_and_exact_matches():
    conn = FakeConn(
        relationships=[
            _relationship(weight=94, confidence=0.94),
            _relationship(
                relationship_id="22222222-2222-2222-2222-222222222222",
                entity_a_id=ENTITY_A,
                entity_b_id=ENTITY_C,
                weight=100,
                confidence=1.0,
            ),
            _relationship(
                relationship_id="33333333-3333-3333-3333-333333333333",
                entity_a_id=ENTITY_B,
                entity_b_id=ENTITY_C,
                weight=0.955,
                confidence=0.955,
            ),
        ],
        links=[
            _link(ENTITY_A, "telegram", "tg-1", "base"),
            _link(ENTITY_B, "instagram", "ig-2", "candidate"),
            _link(ENTITY_C, "tiktok", "tt-3", "third"),
        ],
    )

    hints, skipped = asyncio.run(build_collector_priority_hints(conn))

    assert {(hint.entity_id, hint.candidate_entity_id) for hint in hints} == {
        (ENTITY_B, ENTITY_C),
        (ENTITY_C, ENTITY_B),
    }
    assert {hint.confidence for hint in hints} == {0.955}
    assert skipped["confidence_outside_95_99"] == 2
