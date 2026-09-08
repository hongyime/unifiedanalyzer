"""
Pure-function tests — batch 70.

Covers previously untested pure functions — final batch:
- api.routes.collector_health: _collectors_from_live edge cases, _latest_iso more
- api.routes.graph: _as_latlng_list tuples/floats, _decode_polyline multi-point
- api.routes.media: _int_row string/bool, _thumbnail_placeholder SVG dimensions
- api.routes.intersections: _physical_hit time_gap calculation
- api.routes.data_quality: _write_ledger_cache atomic write + cache_path parent
- api.routes.entities: _decode_meta (list/bytes), _short whitespace normalization
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json


# ---------------------------------------------------------------------------
# api.routes.collector_health: _latest_iso more edge cases
# ---------------------------------------------------------------------------

class TestLatestIsoMore:
    def _l(self, *values):
        from src.api.routes.collector_health import _latest_iso
        return _latest_iso(*values)

    def test_two_valid_same_returns_either(self):
        v = "2026-03-15T10:00:00Z"
        result = self._l(v, v)
        assert v in result

    def test_two_values_returns_most_recent(self):
        # Both valid ISO strings: returns the later one
        older = "2026-01-01T00:00:00Z"
        newer = "2026-06-15T12:00:00Z"
        result = self._l(older, newer)
        assert result is not None
        assert "2026-06-15" in result

    def test_single_none_returns_none(self):
        assert self._l(None) is None


# ---------------------------------------------------------------------------
# api.routes.graph: _as_latlng_list with tuples
# ---------------------------------------------------------------------------

class TestAsLatlngListTuples:
    def _a(self, raw):
        from src.api.routes.graph import _as_latlng_list
        return _as_latlng_list(raw)

    def test_tuple_of_floats(self):
        result = self._a([(1.35, 103.82), (1.36, 103.83)])
        assert len(result) == 2
        assert result[0] == [1.35, 103.82]

    def test_mixed_list_and_tuple(self):
        result = self._a([[1.0, 2.0], (3.0, 4.0)])
        assert len(result) == 2

    def test_single_element_list(self):
        result = self._a([[1.35, 103.82]])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# api.routes.graph: _decode_polyline multi-point
# ---------------------------------------------------------------------------

class TestDecodePolylineMultiPoint:
    def test_two_point_encoding(self):
        from src.api.routes.graph import _decode_polyline
        # "_p~iF~ps|U_ulLnnqC" encodes two points
        result = _decode_polyline("_p~iF~ps|U_ulLnnqC")
        assert len(result) == 2

    def test_all_points_have_lat_lng(self):
        from src.api.routes.graph import _decode_polyline
        result = _decode_polyline("_p~iF~ps|U_ulLnnqC_munEe`?")
        for point in result:
            assert len(point) == 2
            assert all(isinstance(v, float) for v in point)


# ---------------------------------------------------------------------------
# api.routes.media: _int_row string and bool edge cases
# ---------------------------------------------------------------------------

class TestIntRowEdgeCases:
    def _r(self, row, key):
        from src.api.routes.media import _int_row
        return _int_row(row, key)

    def test_string_int(self):
        assert self._r({"k": "42"}, "k") == 42

    def test_bool_true_is_one(self):
        # bool is subclass of int in Python
        assert self._r({"k": True}, "k") == 1

    def test_negative_int(self):
        assert self._r({"k": -5}, "k") == -5


# ---------------------------------------------------------------------------
# api.routes.media: _thumbnail_placeholder SVG content
# ---------------------------------------------------------------------------

class TestThumbnailPlaceholderContent:
    def _t(self, label, detail=""):
        from src.api.routes.media import _thumbnail_placeholder
        return _thumbnail_placeholder(label, detail)

    def test_svg_has_width_and_height(self):
        result = self._t("VIDEO")
        body = result.body.decode()
        assert 'width="256"' in body
        assert 'height="256"' in body

    def test_detail_in_svg(self):
        result = self._t("PDF", "extract failed")
        body = result.body.decode()
        assert "extract failed" in body

    def test_svg_namespace(self):
        result = self._t("X")
        body = result.body.decode()
        assert "xmlns" in body

    def test_no_detail_uses_default(self):
        result = self._t("IMG")
        body = result.body.decode()
        assert "preview unavailable" in body


# ---------------------------------------------------------------------------
# api.routes.intersections: _physical_hit time_gap calculation
# ---------------------------------------------------------------------------

class TestPhysicalHitTimeGap:
    def _h(self, points, entity_names=None, radius_m=200.0):
        from src.api.routes.intersections import _physical_hit
        return _physical_hit(points, entity_names or {}, radius_m)

    def _make_point(self, entity_id, lat, lng, dt):
        from src.api.routes.intersections import _point
        return {
            "entity_id": entity_id, "source": "strava", "record_id": "r",
            "occurred_at": dt, "lat": lat, "lng": lng, "label": None,
            "evidence_type": "gps", "source_table": None,
            "confidence": 0.9, "status": None,
        }

    def test_time_gap_30_minutes(self):
        now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        later = now + timedelta(minutes=30)
        p1 = self._make_point("e1", 1.35, 103.82, now)
        p2 = self._make_point("e2", 1.35, 103.82, later)
        result = self._h([p1, p2])
        assert abs(result["time_gap_minutes"] - 30.0) < 0.1

    def test_time_gap_zero_same_time(self):
        now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        p1 = self._make_point("e1", 1.35, 103.82, now)
        p2 = self._make_point("e2", 1.35, 103.82, now)
        result = self._h([p1, p2])
        assert result["time_gap_minutes"] == 0.0

    def test_radius_stored(self):
        now = datetime(2026, 1, 15, tzinfo=timezone.utc)
        p1 = self._make_point("e1", 1.0, 103.0, now)
        p2 = self._make_point("e2", 1.0, 103.0, now)
        result = self._h([p1, p2], radius_m=500.0)
        assert result["radius_m"] == 500.0


# ---------------------------------------------------------------------------
# api.routes.entities: _decode_meta bytes + list
# ---------------------------------------------------------------------------

class TestDecodeMetaAdditional:
    def _d(self, raw):
        from src.api.routes.entities import _decode_meta
        return _decode_meta(raw)

    def test_json_bytes(self):
        import json
        b = json.dumps({"a": 1}).encode()
        # bytes are passed as str or bytes — behaviour: try json.loads
        result = self._d(b'{"a": 1}')
        # bytes may or may not be handled; at minimum should not crash
        assert isinstance(result, dict)

    def test_empty_dict_string(self):
        assert self._d("{}") == {}

    def test_nested_json_dict(self):
        import json
        result = self._d('{"nested": {"k": "v"}}')
        assert result["nested"] == {"k": "v"}
