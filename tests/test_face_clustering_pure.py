"""
QA-lane tests for pure functions in:
- src/pipeline/face_clustering.py: _uuid_media_ids, _chunks
- src/pipeline/face_associations.py: (no easily isolated pure functions —
  _normalize_embedding requires numpy, _compute_associates_for_image requires
  a running pool; skip)
- Note: backfill_derived._video_files_present / _pdf_files_present are
  filesystem-dependent with no injectable path — skip in unit tests.
"""
from __future__ import annotations

import pytest

from src.pipeline.face_clustering import _chunks, _uuid_media_ids


# ---------------------------------------------------------------------------
# face_clustering._uuid_media_ids
# ---------------------------------------------------------------------------

class TestUuidMediaIds:
    def test_valid_uuids_returned(self):
        ids = [
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ]
        result = _uuid_media_ids(ids)
        assert len(result) == 2
        assert "00000000-0000-0000-0000-000000000001" in result

    def test_invalid_ids_skipped(self):
        ids = [
            "00000000-0000-0000-0000-000000000001",
            "not-a-uuid",
            "also-bad",
        ]
        result = _uuid_media_ids(ids)
        assert len(result) == 1
        assert result[0] == "00000000-0000-0000-0000-000000000001"

    def test_empty_list_returns_empty(self):
        assert _uuid_media_ids([]) == []

    def test_all_invalid_returns_empty(self):
        assert _uuid_media_ids(["bad", "also-bad", ""]) == []

    def test_normalizes_uuid_format(self):
        # UUID() → str normalizes to standard 8-4-4-4-12 dashes-lowercase
        raw = "00000000000000000000000000000001"
        result = _uuid_media_ids([raw])
        assert len(result) == 1
        assert "-" in result[0]  # normalized

    def test_none_in_list_skipped(self):
        ids = ["00000000-0000-0000-0000-000000000001", None]
        result = _uuid_media_ids(ids)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# face_clustering._chunks
# ---------------------------------------------------------------------------

class TestChunks:
    def test_single_chunk_when_size_exceeds_list(self):
        items = ["a", "b", "c"]
        chunks = list(_chunks(items, size=10))
        assert len(chunks) == 1
        assert chunks[0] == ["a", "b", "c"]

    def test_splits_evenly(self):
        items = list(range(10))
        chunks = list(_chunks(items, size=5))
        assert len(chunks) == 2
        assert chunks[0] == [0, 1, 2, 3, 4]
        assert chunks[1] == [5, 6, 7, 8, 9]

    def test_uneven_split(self):
        items = list(range(7))
        chunks = list(_chunks(items, size=3))
        assert len(chunks) == 3
        assert chunks[0] == [0, 1, 2]
        assert chunks[1] == [3, 4, 5]
        assert chunks[2] == [6]

    def test_empty_list_yields_nothing(self):
        assert list(_chunks([], size=5)) == []

    def test_default_size_5000(self):
        items = list(range(5001))
        chunks = list(_chunks(items))
        assert len(chunks) == 2
        assert len(chunks[0]) == 5000
        assert len(chunks[1]) == 1
