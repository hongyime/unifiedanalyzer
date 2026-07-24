from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.pipeline.face_bridge_audit import audit_face_bridge_collisions


class FakeConn:
    def __init__(self, *, fetchvals=None, fetches=None, fail: bool = False):
        self.fetchvals = list(fetchvals or [])
        self.fetches = list(fetches or [])
        self.fail = fail

    async def fetchval(self, *_args):
        if self.fail:
            raise RuntimeError("facetracker schema missing")
        return self.fetchvals.pop(0)

    async def fetch(self, *_args):
        if self.fail:
            raise RuntimeError("facetracker schema missing")
        return self.fetches.pop(0)


@pytest.mark.asyncio
async def test_face_bridge_audit_reports_face_and_cluster_collisions():
    ts = datetime(2026, 7, 24, 1, 2, 3, tzinfo=timezone.utc)
    conn = FakeConn(
        fetchvals=[1, 1],
        fetches=[
            [{
                "face_id": 42,
                "entity_count": 2,
                "entity_ids": ["entity-a", "entity-b"],
                "entity_names": ["Alice", "Bob"],
                "methods": ["cluster_propagation", "media_attribution"],
                "latest_created_at": ts,
            }],
            [{
                "cluster_id": 1092,
                "entity_count": 2,
                "face_count": 7,
                "entity_ids": ["entity-a", "entity-b"],
                "entity_names": ["Alice", "Bob"],
                "methods": ["cluster_propagation"],
                "latest_created_at": ts,
            }],
        ],
    )

    report = await audit_face_bridge_collisions(conn, sample_limit=5)

    assert report["available"] is True
    assert report["ok"] is False
    assert report["face_entity_collisions"] == 1
    assert report["cluster_entity_collisions"] == 1
    assert report["samples"]["faces"][0]["face_id"] == 42
    assert report["samples"]["clusters"][0]["cluster_id"] == 1092
    assert report["samples"]["clusters"][0]["face_count"] == 7
    assert report["samples"]["clusters"][0]["latest_created_at"] == ts.isoformat()


@pytest.mark.asyncio
async def test_face_bridge_audit_ok_when_no_collisions():
    conn = FakeConn(fetchvals=[0, 0], fetches=[[], []])

    report = await audit_face_bridge_collisions(conn, sample_limit=5)

    assert report["available"] is True
    assert report["ok"] is True
    assert report["face_entity_collisions"] == 0
    assert report["cluster_entity_collisions"] == 0
    assert report["samples"] == {"faces": [], "clusters": []}


@pytest.mark.asyncio
async def test_face_bridge_audit_degrades_when_unavailable():
    report = await audit_face_bridge_collisions(FakeConn(fail=True), sample_limit=5)

    assert report["available"] is False
    assert report["ok"] is None
    assert report["face_entity_collisions"] is None
    assert report["cluster_entity_collisions"] is None
    assert "facetracker schema missing" in report["error"]
