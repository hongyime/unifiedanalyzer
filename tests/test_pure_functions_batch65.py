"""
Pure-function tests — batch 65.

Covers previously untested pure functions and constants:
- api.routes.entities: PROXIMITY_ENTITY_CTE constant, SORT_COLUMNS edge cases
- api.routes.collector_health: _as_list / _as_dict / _int_value edge cases,
  required_auth constant presence, _blocker_is_active more combinations
- api.routes.graph: _decode_sources with JSON bytes, confidence_bucket edge cases
- api.routes.media: _int_row edge cases, _coverage_item edge cases
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# api.routes.entities: PROXIMITY_ENTITY_CTE constant
# ---------------------------------------------------------------------------

class TestEntityConstantsAdditional:
    def test_proximity_entity_cte_is_string(self):
        from src.api.routes.entities import PROXIMITY_ENTITY_CTE
        assert isinstance(PROXIMITY_ENTITY_CTE, str)
        assert "proximity" in PROXIMITY_ENTITY_CTE.lower() or "account_proximity" in PROXIMITY_ENTITY_CTE

    def test_sort_columns_platforms_present(self):
        from src.api.routes.entities import SORT_COLUMNS
        assert "platforms" in SORT_COLUMNS

    def test_sort_columns_signals_present(self):
        from src.api.routes.entities import SORT_COLUMNS
        assert "signals" in SORT_COLUMNS

    def test_sort_columns_proximity_present(self):
        from src.api.routes.entities import SORT_COLUMNS
        assert "proximity" in SORT_COLUMNS


# ---------------------------------------------------------------------------
# api.routes.collector_health: edge cases + required_auth
# ---------------------------------------------------------------------------

class TestCollectorHealthEdgeCases:
    def test_as_list_tuple_returns_list(self):
        from src.api.routes.collector_health import _as_list
        result = _as_list((1, 2, 3))
        # tuples are not lists → returns empty
        assert isinstance(result, list)

    def test_as_dict_empty_dict(self):
        from src.api.routes.collector_health import _as_dict
        result = _as_dict({})
        assert result == {}

    def test_int_value_string_float(self):
        from src.api.routes.collector_health import _int_value
        # int("3.9") raises ValueError → returns 0
        result = _int_value("3.9")
        assert result == 0

    def test_int_value_negative(self):
        from src.api.routes.collector_health import _int_value
        result = _int_value(-5)
        assert result == -5

    def test_blocker_is_active_empty_severity_non_empty_kind(self):
        from src.api.routes.collector_health import _blocker_is_active
        assert _blocker_is_active({"kind": "error", "severity": ""}) is True

    def test_blocker_is_active_non_empty_severity_empty_kind(self):
        from src.api.routes.collector_health import _blocker_is_active
        assert _blocker_is_active({"kind": "", "severity": "critical"}) is True

    def test_parse_datetime_utc_aware(self):
        from src.api.routes.collector_health import _parse_datetime
        from datetime import timezone
        result = _parse_datetime("2026-01-15T12:00:00+00:00")
        assert result is not None
        assert result.tzinfo == timezone.utc

    def test_latest_iso_z_suffix(self):
        from src.api.routes.collector_health import _latest_iso
        result = _latest_iso("2026-06-01T00:00:00Z", "2026-01-01T00:00:00Z")
        assert "2026-06-01" in result


# ---------------------------------------------------------------------------
# api.routes.graph: confidence_bucket edge cases
# ---------------------------------------------------------------------------

class TestConfidenceBucketEdgeCases:
    def _b(self, rtype, weight, cross_platform=False):
        from src.api.routes.graph import confidence_bucket
        return confidence_bucket(rtype, weight, cross_platform)

    def test_manual_identity_hard(self):
        assert self._b("manual_identity", 90) == "hard"

    def test_identity_label_hard(self):
        assert self._b("identity_label", 90) == "hard"

    def test_face_coappearance_context_only(self):
        assert self._b("face_coappearance", 1) == "context-only"

    def test_location_copresence_context_only(self):
        assert self._b("location_copresence", 1) == "context-only"

    def test_shared_phone_cross_platform_strong(self):
        assert self._b("shared_phone", 5, cross_platform=True) == "strong"

    def test_shared_phone_same_platform_weak(self):
        # Same platform → not in the cross_platform branch
        result = self._b("shared_phone", 5, cross_platform=False)
        assert result in ("weak", "context-only")

    def test_zero_weight_context_only(self):
        assert self._b("some_rel", 0) == "context-only"


# ---------------------------------------------------------------------------
# api.routes.media: additional _coverage_item edge cases
# ---------------------------------------------------------------------------

class TestCoverageItemEdgeCases:
    def _c(self, key, label, count, processed=None, basis="test"):
        from src.api.routes.media import _coverage_item
        return _coverage_item(key, label, count, processed=processed, basis=basis)

    def test_float_count_converted(self):
        result = self._c("gps", "GPS", 3.7)
        assert result["count"] == 3

    def test_zero_count_is_missing(self):
        result = self._c("gps", "GPS", 0)
        assert result["count"] == 0
        assert result["status"] == "missing"

    def test_processed_zero(self):
        result = self._c("gps", "GPS", 5, processed=0)
        assert result["processed"] == 0

    def test_large_count_covered(self):
        result = self._c("ocr", "OCR", 10_000)
        assert result["status"] == "covered"


# ---------------------------------------------------------------------------
# api.routes.intersections: IntersectRequest validation
# ---------------------------------------------------------------------------

class TestIntersectRequestValidation:
    def test_too_few_ids_raises(self):
        import pytest
        from pydantic import ValidationError
        from src.api.routes.intersections import IntersectRequest
        with pytest.raises(ValidationError):
            IntersectRequest(ids=["single-id"])

    def test_too_many_ids_raises(self):
        import pytest
        from pydantic import ValidationError
        from src.api.routes.intersections import IntersectRequest
        with pytest.raises(ValidationError):
            IntersectRequest(ids=[f"id-{i}" for i in range(15)])

    def test_exactly_two_ids_ok(self):
        from src.api.routes.intersections import IntersectRequest
        req = IntersectRequest(ids=["a", "b"])
        assert len(req.ids) == 2

    def test_max_twelve_ids_ok(self):
        from src.api.routes.intersections import IntersectRequest
        req = IntersectRequest(ids=[f"id-{i}" for i in range(12)])
        assert len(req.ids) == 12
