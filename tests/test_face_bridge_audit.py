from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.pipeline.face_bridge_audit import audit_face_bridge_collisions


REPO_ROOT = Path(__file__).resolve().parent.parent


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
    cluster_row = {
        "cluster_id": 1092,
        "entity_count": 2,
        "face_count": 7,
        "entity_ids": ["entity-a", "entity-b"],
        "entity_names": ["Alice", "Bob"],
        "methods": ["cluster_propagation"],
        "latest_created_at": ts,
    }
    conn = FakeConn(
        fetchvals=[1, 1, 1],
        fetches=[
            [{
                "face_id": 42,
                "entity_count": 2,
                "entity_ids": ["entity-a", "entity-b"],
                "entity_names": ["Alice", "Bob"],
                "methods": ["cluster_propagation", "media_attribution"],
                "latest_created_at": ts,
            }],
            [cluster_row],
            [cluster_row],
        ],
    )

    report = await audit_face_bridge_collisions(conn, sample_limit=5)

    assert report["available"] is True
    assert report["ok"] is False
    assert report["face_entity_collisions"] == 1
    assert report["cluster_entity_collisions"] == 1
    assert report["contested_cluster_count"] == 1
    assert report["samples"]["faces"][0]["face_id"] == 42
    assert report["samples"]["clusters"][0]["cluster_id"] == 1092
    assert report["samples"]["clusters"][0]["face_count"] == 7
    assert report["samples"]["clusters"][0]["latest_created_at"] == ts.isoformat()
    assert report["samples"]["contested_clusters"][0]["cluster_id"] == 1092


@pytest.mark.asyncio
async def test_face_bridge_audit_reports_direct_anchor_contested_clusters_without_degrading():
    ts = datetime(2026, 7, 24, 1, 2, 3, tzinfo=timezone.utc)
    conn = FakeConn(
        fetchvals=[0, 1, 0],
        fetches=[
            [],
            [],
            [{
                "cluster_id": 1109,
                "entity_count": 5,
                "face_count": 6,
                "entity_ids": ["entity-a", "entity-b"],
                "entity_names": ["Alice", "Bob"],
                "methods": ["media_attribution", "profile_photo"],
                "latest_created_at": ts,
            }],
        ],
    )

    report = await audit_face_bridge_collisions(conn, sample_limit=5)

    assert report["available"] is True
    assert report["ok"] is True
    assert report["face_entity_collisions"] == 0
    assert report["cluster_entity_collisions"] == 0
    assert report["contested_cluster_count"] == 1
    assert report["samples"]["clusters"] == []
    assert report["samples"]["contested_clusters"][0]["cluster_id"] == 1109


@pytest.mark.asyncio
async def test_face_bridge_audit_ok_when_no_collisions():
    conn = FakeConn(fetchvals=[0, 0, 0], fetches=[[], [], []])

    report = await audit_face_bridge_collisions(conn, sample_limit=5)

    assert report["available"] is True
    assert report["ok"] is True
    assert report["face_entity_collisions"] == 0
    assert report["cluster_entity_collisions"] == 0
    assert report["contested_cluster_count"] == 0
    assert report["samples"] == {"faces": [], "clusters": [], "contested_clusters": []}


@pytest.mark.asyncio
async def test_face_bridge_audit_degrades_when_unavailable():
    report = await audit_face_bridge_collisions(FakeConn(fail=True), sample_limit=5)

    assert report["available"] is False
    assert report["ok"] is None
    assert report["face_entity_collisions"] is None
    assert report["cluster_entity_collisions"] is None
    assert report["contested_cluster_count"] is None
    assert "facetracker schema missing" in report["error"]


def test_face_owner_attribution_is_single_face_gated():
    source = (REPO_ROOT / "src" / "face_worker.py").read_text(encoding="utf-8")

    assert "FACE_WORKER_OWNER_ATTRIBUTION_MAX_FACES" in source
    assert "can_attribute_owner = bool(eid) and len(faces) <= _OWNER_ATTRIBUTION_MAX_FACES" in source
    assert "can_attribute_owner = bool(eid) and total_faces <= _OWNER_ATTRIBUTION_MAX_FACES" in source
    assert "COALESCE(i.face_count, :unknown_face_count) <= :max_faces" in source


def test_face_worker_dedupes_collector_media_by_stable_media_id():
    source = (REPO_ROOT / "src" / "face_worker.py").read_text(encoding="utf-8")

    assert "done_media_ids" in source
    assert "SELECT file_hash FROM images" in source
    assert "if r.file_path in done or r.id in done_media_ids:" in source
    assert source.count("done_media_ids.add(r.id)") >= 2


def test_face_worker_relink_purges_junk_entity_faces():
    source = (REPO_ROOT / "src" / "face_worker.py").read_text(encoding="utf-8")

    assert "\"junk_entity_faces_purged\"" in source
    assert "DELETE FROM public.entity_faces ef" in source
    assert "COALESCE(f.is_junk, false)" in source


def test_face_signals_ignore_contested_clusters():
    tier1 = (REPO_ROOT / "src" / "pipeline" / "media_analysis_tier1.py").read_text(encoding="utf-8")
    pair = (REPO_ROOT / "src" / "pipeline" / "face_pair_signals.py").read_text(encoding="utf-8")
    exif = (REPO_ROOT / "src" / "pipeline" / "media_analysis.py").read_text(encoding="utf-8")

    for source in (tier1, pair, exif):
        assert "contested_clusters AS" in source
        assert "HAVING count(DISTINCT ef.entity_id) > 1" in source
        assert "NOT EXISTS (" in source


def test_drive_xref_is_drive_only_and_purges_unsafe_rows():
    source = (REPO_ROOT / "src" / "pipeline" / "face_clustering.py").read_text(encoding="utf-8")

    assert "i.file_path LIKE '/mnt/%'" in source
    assert "i.file_hash !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'" in source
    assert "purge_unsafe_entity_face_links" in source
    assert "owner_group_rows" in source
    assert "non_drive_xref_rows" in source
    assert "contested_propagation_rows" in source
