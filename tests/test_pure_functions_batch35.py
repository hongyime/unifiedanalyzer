"""
Pure-function tests — batch 35.

Covers previously untested pure functions:
- pipeline.face_bridge_audit: _face_sample, _cluster_sample, _UNSAFE_CLUSTER_METHODS
- pipeline.incremental_runner: _run_skipped_due_lock (already tested), _RUN_CLAIM_LOCK_KEY,
  _env_int, _env_bool, _translation_phase_enabled (already tested) — adding
  _COVERAGE_SOURCE_BUCKETS constant + production run type constants
- pipeline.identity_calibration: _jsonload, _rows_to_xy (pure with mock data)
"""
from __future__ import annotations

from types import SimpleNamespace


# ---------------------------------------------------------------------------
# face_bridge_audit: _face_sample, _cluster_sample, _UNSAFE_CLUSTER_METHODS
# ---------------------------------------------------------------------------

class TestFaceSample:
    def _make_row(self, **kw):
        defaults = {
            "face_id": 42, "entity_count": 2,
            "entity_ids": ["eid-1", "eid-2"],
            "entity_names": ["Alice", "Bob"],
            "methods": ["media_attribution", "cluster_propagation"],
            "latest_created_at": None,
        }
        defaults.update(kw)
        return defaults

    def test_basic_shape(self):
        from src.pipeline.face_bridge_audit import _face_sample
        row = self._make_row()
        result = _face_sample(row)
        assert result["face_id"] == 42
        assert result["entity_count"] == 2
        assert isinstance(result["entity_ids"], list)
        assert isinstance(result["methods"], list)
        assert result["latest_created_at"] is None

    def test_none_entity_count_defaults_zero(self):
        from src.pipeline.face_bridge_audit import _face_sample
        row = self._make_row(entity_count=None)
        result = _face_sample(row)
        assert result["entity_count"] == 0

    def test_iso_datetime_converted(self):
        from src.pipeline.face_bridge_audit import _face_sample
        from datetime import datetime, timezone
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        row = self._make_row(latest_created_at=dt)
        result = _face_sample(row)
        assert "2026-01-15" in result["latest_created_at"]


class TestClusterSample:
    def _make_row(self, **kw):
        defaults = {
            "cluster_id": 7, "entity_count": 2, "face_count": 10,
            "entity_ids": ["eid-1"], "entity_names": ["Alice"],
            "methods": ["cluster_propagation"], "latest_created_at": None,
        }
        defaults.update(kw)
        return defaults

    def test_basic_shape(self):
        from src.pipeline.face_bridge_audit import _cluster_sample
        result = _cluster_sample(self._make_row())
        assert result["cluster_id"] == 7
        assert result["face_count"] == 10
        assert result["entity_count"] == 2

    def test_none_face_count_defaults_zero(self):
        from src.pipeline.face_bridge_audit import _cluster_sample
        result = _cluster_sample(self._make_row(face_count=None))
        assert result["face_count"] == 0


class TestUnsafeClusterMethods:
    def test_non_empty(self):
        from src.pipeline.face_bridge_audit import _UNSAFE_CLUSTER_METHODS
        assert len(_UNSAFE_CLUSTER_METHODS) > 0

    def test_cluster_propagation_present(self):
        from src.pipeline.face_bridge_audit import _UNSAFE_CLUSTER_METHODS
        assert "cluster_propagation" in _UNSAFE_CLUSTER_METHODS

    def test_knn_propagation_present(self):
        from src.pipeline.face_bridge_audit import _UNSAFE_CLUSTER_METHODS
        assert "knn_propagation" in _UNSAFE_CLUSTER_METHODS

    def test_all_strings(self):
        from src.pipeline.face_bridge_audit import _UNSAFE_CLUSTER_METHODS
        assert all(isinstance(m, str) for m in _UNSAFE_CLUSTER_METHODS)


# ---------------------------------------------------------------------------
# incremental_runner: additional constants
# ---------------------------------------------------------------------------

class TestIncrementalRunnerAdditionalConstants:
    def test_run_claim_lock_key_is_int(self):
        from src.pipeline.incremental_runner import _RUN_CLAIM_LOCK_KEY
        assert isinstance(_RUN_CLAIM_LOCK_KEY, int)

    def test_coverage_source_buckets_non_empty(self):
        from src.pipeline.incremental_runner import _COVERAGE_SOURCE_BUCKETS
        assert len(_COVERAGE_SOURCE_BUCKETS) > 0
        assert "by_source" in _COVERAGE_SOURCE_BUCKETS

    def test_phase_resource_classes_has_many_phases(self):
        from src.pipeline.incremental_runner import _PHASE_RESOURCE_CLASSES
        assert len(_PHASE_RESOURCE_CLASSES) >= 10

    def test_stale_heartbeat_minutes_reasonable(self):
        from src.pipeline.incremental_runner import _STALE_HEARTBEAT_MINUTES
        # Should be between 5 and 120 minutes
        assert 5 <= _STALE_HEARTBEAT_MINUTES <= 120

    def test_heartbeat_interval_less_than_stale_window(self):
        from src.pipeline.incremental_runner import (
            _HEARTBEAT_INTERVAL_SECONDS, _STALE_HEARTBEAT_MINUTES
        )
        # Heartbeat should fire more frequently than stale window
        assert _HEARTBEAT_INTERVAL_SECONDS < _STALE_HEARTBEAT_MINUTES * 60


# ---------------------------------------------------------------------------
# identity_calibration: _jsonload, _rows_to_xy
# ---------------------------------------------------------------------------

class TestJsonloadCalibration:
    def _j(self, raw):
        from src.pipeline.identity_calibration import _jsonload
        return _jsonload(raw)

    def test_dict_passthrough(self):
        assert self._j({"email_match": 0.9}) == {"email_match": 0.9}

    def test_json_string(self):
        assert self._j('{"email_match": 0.8}') == {"email_match": 0.8}

    def test_invalid_returns_empty(self):
        assert self._j("bad") == {}

    def test_none_returns_empty(self):
        assert self._j(None) == {}

    def test_array_json_returns_empty(self):
        assert self._j("[1, 2]") == {}


class TestRowsToXy:
    def _make_row(self, features, label, source="human"):
        import json
        # Use a dict — _rows_to_xy accesses r["features"] and r.keys()
        return {
            "features": json.dumps(features),
            "label": label,
            "source": source,
        }

    def test_basic_row(self):
        from src.pipeline.identity_calibration import _rows_to_xy, FEATURE_ORDER
        row = self._make_row({"email_match": 0.9}, 1)
        X, y = _rows_to_xy([row])
        assert len(X) >= 1
        assert len(y) >= 1
        assert y[0] == 1
        assert len(X[0]) == len(FEATURE_ORDER)

    def test_label_zero_preserved(self):
        from src.pipeline.identity_calibration import _rows_to_xy
        row = self._make_row({"email_match": 0.5}, 0, "human")
        X, y = _rows_to_xy([row])
        # No LOSO expansion for label=0
        assert len(y) == 1
        assert y[0] == 0

    def test_auto_positive_loso_expanded(self):
        from src.pipeline.identity_calibration import _rows_to_xy
        # Auto-positive with 2 non-zero features → base + 2 LOSO variants = 3 rows
        row = self._make_row({"email_match": 0.9, "phone_match": 0.7}, 1, "auto_positive_v1")
        X, y = _rows_to_xy([row])
        assert len(X) >= 3  # base + at least 2 variants
        assert all(v == 1 for v in y)

    def test_human_positive_not_loso_expanded(self):
        from src.pipeline.identity_calibration import _rows_to_xy
        row = self._make_row({"email_match": 0.9, "phone_match": 0.7}, 1, "human")
        X, y = _rows_to_xy([row])
        # Human positive: only base row, no expansion
        assert len(X) == 1

    def test_empty_rows(self):
        from src.pipeline.identity_calibration import _rows_to_xy
        X, y = _rows_to_xy([])
        assert X == []
        assert y == []
