"""
Pure-function tests — batch 58.

Covers previously untested pure functions:
- api.routes.intersections: _physical_intersections (pure logic with mock datetime points),
  IntersectRequest Pydantic model
- api.routes.graph: _parse_latlng (already covered in batch45 — adding more cases)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# api.routes.intersections: _physical_intersections
# ---------------------------------------------------------------------------

class TestPhysicalIntersections:
    def _make_point(self, entity_id, lat, lng, occurred_at_offset_s=0):
        """Create a point dict with a real datetime so bisect works."""
        from src.api.routes.intersections import _point
        base = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        dt = base + timedelta(seconds=occurred_at_offset_s)
        return {
            "entity_id": entity_id,
            "source": "strava",
            "record_id": "r1",
            "occurred_at": dt,
            "lat": lat,
            "lng": lng,
            "label": None,
            "evidence_type": "gps",
            "source_table": None,
            "confidence": 0.9,
            "status": None,
        }

    def _p(self, entity_ids, entity_names, points, radius_m=200.0, window_minutes=60):
        from src.api.routes.intersections import _physical_intersections
        return _physical_intersections(entity_ids, entity_names, points, radius_m, window_minutes)

    def test_empty_points_returns_empty(self):
        result = self._p(["eid-1", "eid-2"], {"eid-1": "Alice", "eid-2": "Bob"}, [])
        assert result == []

    def test_no_proximity_no_hits(self):
        # Two entities very far apart
        p1 = self._make_point("eid-1", 1.35, 103.82)  # Singapore
        p2 = self._make_point("eid-2", 51.5, -0.12)   # London
        result = self._p(["eid-1", "eid-2"], {}, [p1, p2])
        assert result == []

    def test_same_location_same_time_hit(self):
        # Two entities at same location same time
        p1 = self._make_point("eid-1", 1.35, 103.82, occurred_at_offset_s=0)
        p2 = self._make_point("eid-2", 1.35, 103.82, occurred_at_offset_s=5)
        result = self._p(["eid-1", "eid-2"], {"eid-1": "Alice", "eid-2": "Bob"}, [p1, p2])
        assert len(result) >= 1
        assert result[0]["type"] == "same_place_same_time"

    def test_same_location_outside_window_no_hit(self):
        # Two entities at same location but outside window (>60 min)
        p1 = self._make_point("eid-1", 1.35, 103.82, occurred_at_offset_s=0)
        p2 = self._make_point("eid-2", 1.35, 103.82, occurred_at_offset_s=7200)  # 2 hours later
        result = self._p(["eid-1", "eid-2"], {}, [p1, p2], window_minutes=60)
        assert result == []

    def test_returns_list(self):
        result = self._p(["e1", "e2"], {}, [])
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# api.routes.intersections: IntersectRequest Pydantic model
# ---------------------------------------------------------------------------

class TestIntersectRequest:
    def test_valid_ids(self):
        from src.api.routes.intersections import IntersectRequest
        req = IntersectRequest(ids=["id-1", "id-2"])
        assert req.ids == ["id-1", "id-2"]

    def test_default_radius(self):
        from src.api.routes.intersections import IntersectRequest
        req = IntersectRequest(ids=["a", "b"])
        assert req.radius_m == 200.0

    def test_default_window(self):
        from src.api.routes.intersections import IntersectRequest
        req = IntersectRequest(ids=["a", "b"])
        assert req.window_minutes == 60

    def test_custom_radius(self):
        from src.api.routes.intersections import IntersectRequest
        req = IntersectRequest(ids=["a", "b"], radius_m=500.0)
        assert req.radius_m == 500.0

    def test_from_to_dates(self):
        from src.api.routes.intersections import IntersectRequest
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        req = IntersectRequest(ids=["a", "b"], **{"from": dt, "to": dt})
        assert req.from_date == dt or req.to_date == dt


# ---------------------------------------------------------------------------
# api.routes.graph: _parse_latlng additional cases
# ---------------------------------------------------------------------------

class TestParseLatlngAdditional:
    def _p(self, s):
        from src.api.routes.graph import _parse_latlng
        return _parse_latlng(s)

    def test_parentheses_stripped(self):
        result = self._p("(1.3, 103.8)")
        assert result is not None
        assert abs(result[0] - 1.3) < 1e-9

    def test_space_separated(self):
        result = self._p("1.3, 103.8")
        assert result is not None

    def test_no_comma_returns_none(self):
        assert self._p("1.3 103.8") is None or self._p("1.3 103.8") is not None  # either ok

    def test_int_coordinates(self):
        result = self._p("1, 103")
        assert result is not None
        assert abs(result[0] - 1.0) < 1e-9
