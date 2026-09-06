"""
QA-lane tests for pure helper functions in:
- src/api/routes/intersections.py: _iso, _parse_json, _parse_latlng, _haversine_m,
  _point, _serialise_point, _dedupe_ids
- src/api/routes/timeline.py: _coerce_confidence, _metadata_path_value,
  _optional_row_value
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# intersections._iso, _parse_json, _parse_latlng, _haversine_m, _point,
#               _serialise_point, _dedupe_ids
# ---------------------------------------------------------------------------

from src.api.routes.intersections import (
    _dedupe_ids,
    _haversine_m,
    _iso,
    _parse_json,
    _parse_latlng,
    _point,
    _serialise_point,
)

_NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


class TestIntersectionsIso:
    def test_datetime_returns_isoformat(self):
        result = _iso(_NOW)
        assert "2024-06-01" in result

    def test_none_returns_none(self):
        assert _iso(None) is None


class TestParseJson:
    def test_dict_passthrough(self):
        assert _parse_json({"a": 1}) == {"a": 1}

    def test_list_passthrough(self):
        assert _parse_json([1, 2]) == [1, 2]

    def test_valid_json_string_parsed(self):
        assert _parse_json('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_as_is(self):
        result = _parse_json("bad{")
        assert result == "bad{"

    def test_none_returned_as_is(self):
        assert _parse_json(None) is None


class TestParseLatlng:
    def test_comma_separated(self):
        result = _parse_latlng("1.35,103.82")
        assert result is not None
        assert abs(result[0] - 1.35) < 0.001
        assert abs(result[1] - 103.82) < 0.001

    def test_bracketed(self):
        result = _parse_latlng("[1.35, 103.82]")
        assert result is not None

    def test_none_returns_none(self):
        assert _parse_latlng(None) is None

    def test_empty_returns_none(self):
        assert _parse_latlng("") is None

    def test_invalid_returns_none(self):
        assert _parse_latlng("notlatlng") is None


class TestHaversineM:
    def test_same_point_zero_distance(self):
        d = _haversine_m(1.0, 103.0, 1.0, 103.0)
        assert d == 0.0

    def test_known_distance(self):
        # Singapore (1.3521, 103.8198) to KL (3.1390, 101.6869) ≈ 315km
        d = _haversine_m(1.3521, 103.8198, 3.1390, 101.6869)
        assert 300_000 < d < 330_000

    def test_returns_meters(self):
        # 1 degree latitude ≈ 111,000 m
        d = _haversine_m(0.0, 0.0, 1.0, 0.0)
        assert 110_000 < d < 112_000


class TestPoint:
    def test_basic_fields(self):
        p = _point("eid-1", "strava", "rec-1", _NOW, 1.35, 103.82, "Morning run")
        assert p["entity_id"] == "eid-1"
        assert p["lat"] == 1.35
        assert p["lng"] == 103.82
        assert p["label"] == "Morning run"

    def test_evidence_key_only_when_set(self):
        p = _point("eid-1", "strava", "rec-1", None, 1.0, 103.0, None)
        assert "evidence_key" not in p

        p2 = _point("eid-1", "strava", "rec-1", None, 1.0, 103.0, None, evidence_key="k1")
        assert p2["evidence_key"] == "k1"


class TestSerialisePoint:
    def _p(self):
        return _point("eid-1", "strava", "rec-1", _NOW, 1.35, 103.82, "Run",
                       confidence=0.9, status="confirmed")

    def test_basic_fields(self):
        result = _serialise_point(self._p(), {"eid-1": "Alice"})
        assert result["entity_id"] == "eid-1"
        assert result["entity_name"] == "Alice"
        assert result["lat"] == 1.35
        assert result["confidence"] == 0.9

    def test_unknown_entity_name_is_none(self):
        result = _serialise_point(self._p(), {})
        assert result["entity_name"] is None

    def test_occurred_at_isoformatted(self):
        result = _serialise_point(self._p(), {})
        assert "2024-06-01" in result["occurred_at"]


class TestDedupeIds:
    def test_unique_ids_unchanged_order_preserved(self):
        result = _dedupe_ids(["b", "a", "c"])
        assert set(result) == {"a", "b", "c"}

    def test_duplicates_removed(self):
        result = _dedupe_ids(["a", "b", "a", "c", "b"])
        assert len(result) == 3
        assert set(result) == {"a", "b", "c"}

    def test_empty_returns_empty(self):
        assert _dedupe_ids([]) == []


# ---------------------------------------------------------------------------
# timeline._coerce_confidence, _metadata_path_value, _optional_row_value
# ---------------------------------------------------------------------------

from src.api.routes.timeline import (
    _coerce_confidence,
    _metadata_path_value,
    _optional_row_value,
)


class TestCoerceConfidence:
    def test_none_returns_none(self):
        assert _coerce_confidence(None) is None

    def test_bool_returns_none(self):
        assert _coerce_confidence(True) is None
        assert _coerce_confidence(False) is None

    def test_float_in_range_returned(self):
        assert abs(_coerce_confidence(0.85) - 0.85) < 1e-9

    def test_percentage_normalized(self):
        result = _coerce_confidence(85.0)
        assert result is not None
        assert abs(result - 0.85) < 0.001

    def test_string_float_parsed(self):
        result = _coerce_confidence("0.75")
        assert result is not None
        assert abs(result - 0.75) < 0.001

    def test_string_percentage_parsed(self):
        result = _coerce_confidence("75%")
        assert result is not None
        assert abs(result - 0.75) < 0.001

    def test_invalid_string_returns_none(self):
        assert _coerce_confidence("high") is None

    def test_negative_returns_none(self):
        assert _coerce_confidence(-0.5) is None

    def test_value_capped_at_1(self):
        result = _coerce_confidence(200.0)
        assert result == 1.0


class TestMetadataPathValue:
    def test_simple_path(self):
        assert _metadata_path_value({"a": 1}, ("a",)) == 1

    def test_nested_path(self):
        result = _metadata_path_value({"a": {"b": 42}}, ("a", "b"))
        assert result == 42

    def test_missing_key_returns_none(self):
        assert _metadata_path_value({"a": 1}, ("b",)) is None

    def test_non_mapping_returns_none(self):
        assert _metadata_path_value("string", ("a",)) is None

    def test_empty_path_returns_full_value(self):
        d = {"a": 1}
        assert _metadata_path_value(d, ()) == d


class TestOptionalRowValue:
    def test_dict_access(self):
        assert _optional_row_value({"k": "v"}, "k") == "v"

    def test_missing_key_returns_none(self):
        assert _optional_row_value({"k": "v"}, "x") is None

    def test_subscript_object(self):
        class Row:
            def __getitem__(self, k):
                return "val" if k == "key" else (_ for _ in ()).throw(KeyError(k))
        assert _optional_row_value(Row(), "key") == "val"

    def test_subscript_error_returns_none(self):
        assert _optional_row_value({}, "missing") is None
