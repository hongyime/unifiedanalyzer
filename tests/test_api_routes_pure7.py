"""
QA-lane tests for pure helper functions in:
- src/api/routes/eval.py: _row
- src/api/face_lookup.py: face_crop_url
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.api.routes.eval import _row as eval_row
from src.api.face_lookup import face_crop_url

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# eval._row
# ---------------------------------------------------------------------------

class TestEvalRow:
    def _make(self, **overrides):
        base = {
            "id": "eval-1",
            "name": "Test Run",
            "task_type": "entity_resolution",
            "model_or_rule_version": "v1.0",
            "status": "completed",
            "metrics_json": {"precision": 0.9},
            "started_at": _NOW,
            "finished_at": _NOW,
        }
        base.update(overrides)
        return base

    def test_basic_fields_mapped(self):
        result = eval_row(self._make())
        assert result["id"] == "eval-1"
        assert result["name"] == "Test Run"
        assert result["task_type"] == "entity_resolution"
        assert result["status"] == "completed"

    def test_datetime_fields_isoformatted(self):
        result = eval_row(self._make())
        assert "2024-06-01" in result["started_at"]
        assert "2024-06-01" in result["finished_at"]

    def test_none_datetimes_become_none(self):
        result = eval_row(self._make(started_at=None, finished_at=None))
        assert result["started_at"] is None
        assert result["finished_at"] is None

    def test_metrics_mapped_from_metrics_json(self):
        result = eval_row(self._make(metrics_json={"f1": 0.85}))
        assert result["metrics"] == {"f1": 0.85}


# ---------------------------------------------------------------------------
# face_lookup.face_crop_url
# ---------------------------------------------------------------------------

class TestFaceCropUrl:
    def test_valid_face_id_returns_url(self):
        result = face_crop_url("abc-123")
        assert result == "/api/face/gallery/faces/abc-123/crop"

    def test_none_face_id_returns_none(self):
        assert face_crop_url(None) is None

    def test_integer_face_id_returns_url(self):
        result = face_crop_url(42)
        assert "42" in result

    def test_url_format_correct(self):
        result = face_crop_url("face-xyz")
        assert result.startswith("/api/face/gallery/faces/")
        assert result.endswith("/crop")
