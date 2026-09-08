"""
Pure-function tests — batch 45.

Covers previously untested pure functions:
- api.routes.timeline: _optional_row_value, _partition_names_desc,
  _timeline_confidence_expr (returns str), _source_link_confidence_expr,
  _effective_timeline_confidence_expr
- api.routes.intersections: _point, _serialise_point, _physical_hit, _dedupe_ids
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# api.routes.timeline: _optional_row_value, _partition_names_desc
# ---------------------------------------------------------------------------

class TestOptionalRowValue:
    def _r(self, row, key):
        from src.api.routes.timeline import _optional_row_value
        return _optional_row_value(row, key)

    def test_dict_present(self):
        assert self._r({"k": "v"}, "k") == "v"

    def test_dict_missing_returns_none(self):
        assert self._r({"a": 1}, "b") is None

    def test_index_error_returns_none(self):
        assert self._r([], 0) is None

    def test_none_dict_value_returned(self):
        assert self._r({"k": None}, "k") is None


class TestPartitionNamesDesc:
    def _p(self, start=None, floor_year=2010):
        from src.api.routes.timeline import _partition_names_desc
        return _partition_names_desc(start, floor_year)

    def test_returns_list(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        result = self._p(start)
        assert isinstance(result, list)

    def test_starts_with_current_month(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        result = self._p(start)
        assert result[0] == "timeline_events_2026_03"

    def test_descending_order(self):
        start = datetime(2026, 3, 1, tzinfo=timezone.utc)
        result = self._p(start)
        # Second entry should be Feb 2026
        assert result[1] == "timeline_events_2026_02"

    def test_rolls_back_year(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = self._p(start)
        # After Jan should be Dec of prior year
        assert "timeline_events_2025_12" in result

    def test_stops_at_floor_year(self):
        start = datetime(2012, 1, 1, tzinfo=timezone.utc)
        result = self._p(start, floor_year=2011)
        # Should not go below floor_year
        names_years = {int(n.split("_")[2]) for n in result}
        assert min(names_years) >= 2011

    def test_format_correct(self):
        start = datetime(2026, 9, 1, tzinfo=timezone.utc)
        result = self._p(start)
        assert result[0] == "timeline_events_2026_09"


class TestTimelineExprFunctions:
    def test_timeline_confidence_expr_returns_string(self):
        from src.api.routes.timeline import _timeline_confidence_expr
        expr = _timeline_confidence_expr()
        assert isinstance(expr, str)
        assert len(expr) > 0

    def test_source_link_confidence_expr_returns_string(self):
        from src.api.routes.timeline import _source_link_confidence_expr
        expr = _source_link_confidence_expr()
        assert isinstance(expr, str)
        assert "confidence" in expr.lower()

    def test_effective_timeline_confidence_expr_returns_string(self):
        from src.api.routes.timeline import _effective_timeline_confidence_expr
        expr = _effective_timeline_confidence_expr()
        assert isinstance(expr, str)
        assert "COALESCE" in expr

    def test_expressions_contain_alias(self):
        from src.api.routes.timeline import _timeline_confidence_expr
        expr = _timeline_confidence_expr(alias="ev")
        assert "ev" in expr

    def test_source_link_expr_with_alias(self):
        from src.api.routes.timeline import _source_link_confidence_expr
        expr = _source_link_confidence_expr(alias="lnk")
        assert "lnk" in expr


# ---------------------------------------------------------------------------
# api.routes.intersections: _point, _serialise_point, _physical_hit, _dedupe_ids
# ---------------------------------------------------------------------------

class TestPointHelper:
    def _p(self, **kw):
        from src.api.routes.intersections import _point
        defaults = dict(
            entity_id="eid-1",
            source="instagram",
            record_id="rec-1",
            occurred_at=None,
            lat=1.35,
            lng=103.82,
            label="Home",
        )
        defaults.update(kw)
        return _point(**defaults)

    def test_basic_shape(self):
        p = self._p()
        assert p["entity_id"] == "eid-1"
        assert p["source"] == "instagram"
        assert abs(p["lat"] - 1.35) < 1e-9
        assert p["label"] == "Home"

    def test_default_evidence_type(self):
        p = self._p()
        assert p["evidence_type"] == "gps"

    def test_custom_evidence_type(self):
        p = self._p(evidence_type="strava")
        assert p["evidence_type"] == "strava"

    def test_evidence_key_present_when_provided(self):
        p = self._p(evidence_key="abc123")
        assert p["evidence_key"] == "abc123"

    def test_evidence_key_absent_when_not_provided(self):
        p = self._p()
        assert "evidence_key" not in p


class TestSerialisePoint:
    def _s(self, point, entity_names=None):
        from src.api.routes.intersections import _serialise_point
        return _serialise_point(point, entity_names or {})

    def _make_point(self, entity_id="eid-1", lat=1.35, lng=103.82):
        from src.api.routes.intersections import _point
        return _point(entity_id=entity_id, source="instagram", record_id="r1",
                      occurred_at=None, lat=lat, lng=lng, label=None)

    def test_entity_name_resolved(self):
        p = self._make_point()
        result = self._s(p, {"eid-1": "Alice"})
        assert result["entity_name"] == "Alice"

    def test_entity_name_none_when_missing(self):
        p = self._make_point()
        result = self._s(p, {})
        assert result["entity_name"] is None

    def test_coords_present(self):
        p = self._make_point(lat=1.35, lng=103.82)
        result = self._s(p)
        assert abs(result["lat"] - 1.35) < 1e-9

    def test_required_keys_present(self):
        p = self._make_point()
        result = self._s(p)
        for key in ("entity_id", "source", "record_id", "lat", "lng",
                    "occurred_at", "label", "evidence_type"):
            assert key in result


class TestPhysicalHit:
    def _h(self, points, entity_names=None, radius_m=200.0):
        from src.api.routes.intersections import _physical_hit
        return _physical_hit(points, entity_names or {}, radius_m)

    def _make_point(self, entity_id, lat, lng, occurred_at=None):
        from src.api.routes.intersections import _point
        return _point(entity_id=entity_id, source="instagram", record_id="r",
                      occurred_at=occurred_at, lat=lat, lng=lng, label=None)

    def test_type_field(self):
        p1 = self._make_point("eid-1", 1.35, 103.82)
        p2 = self._make_point("eid-2", 1.36, 103.83)
        result = self._h([p1, p2])
        assert result["type"] == "same_place_same_time"

    def test_locus_present(self):
        p1 = self._make_point("e1", 1.0, 103.0)
        p2 = self._make_point("e2", 1.0, 103.0)
        result = self._h([p1, p2])
        assert "lat" in result["locus"]
        assert "lng" in result["locus"]

    def test_sources_sorted(self):
        p1 = self._make_point("e1", 1.0, 103.0)
        p2 = self._make_point("e2", 1.0, 103.0)
        p1["source"] = "z_source"
        p2["source"] = "a_source"
        result = self._h([p1, p2])
        assert result["sources"] == sorted(result["sources"])

    def test_time_gap_zero_when_no_timestamps(self):
        p1 = self._make_point("e1", 1.0, 103.0)
        p2 = self._make_point("e2", 1.0, 103.0)
        result = self._h([p1, p2])
        assert result["time_gap_minutes"] == 0.0


class TestDedupeIds:
    def _d(self, ids):
        from src.api.routes.intersections import _dedupe_ids
        return _dedupe_ids(ids)

    def test_no_dupes(self):
        assert self._d(["a", "b", "c"]) == ["a", "b", "c"]

    def test_removes_dupes_preserves_order(self):
        assert self._d(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_empty(self):
        assert self._d([]) == []

    def test_all_dupes(self):
        assert self._d(["x", "x", "x"]) == ["x"]
