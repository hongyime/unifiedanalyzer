"""
Pure-function tests — batch 57.

Covers previously untested pure functions:
- api.routes.graph: _downsample, _geo_event, _geo_events
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.graph: _downsample
# ---------------------------------------------------------------------------

class TestDownsample:
    def _d(self, pts, target=120):
        from src.api.routes.graph import _downsample
        return _downsample(pts, target)

    def test_short_list_unchanged(self):
        pts = [[1.0, 2.0], [3.0, 4.0]]
        assert self._d(pts, target=120) == pts

    def test_exact_target_unchanged(self):
        pts = [[float(i), 0.0] for i in range(5)]
        assert self._d(pts, target=5) == pts

    def test_large_list_thinned(self):
        pts = [[float(i), 0.0] for i in range(1000)]
        result = self._d(pts, target=100)
        # Should be target + 1 (last point appended)
        assert len(result) <= 101

    def test_last_point_preserved(self):
        pts = [[float(i), 0.0] for i in range(500)]
        result = self._d(pts, target=50)
        assert result[-1] == pts[-1]

    def test_empty_list(self):
        assert self._d([], target=120) == []

    def test_single_point(self):
        pts = [[1.0, 2.0]]
        assert self._d(pts, target=120) == pts


# ---------------------------------------------------------------------------
# api.routes.graph: _geo_event
# ---------------------------------------------------------------------------

class TestGeoEvent:
    def _e(self, item, kind):
        from src.api.routes.graph import _geo_event
        return _geo_event(item, kind)

    def test_point_event_lat_lng_direct(self):
        item = {"lat": 1.35, "lng": 103.82, "source": "instagram", "evidence_type": "gps"}
        result = self._e(item, "point")
        assert result["kind"] == "point"
        assert abs(result["lat"] - 1.35) < 1e-9
        assert abs(result["lng"] - 103.82) < 1e-9

    def test_route_event_lat_from_points(self):
        item = {
            "points": [[1.35, 103.82], [1.36, 103.83]],
            "source": "strava",
            "evidence_type": "route",
        }
        result = self._e(item, "route")
        assert result["kind"] == "route"
        assert abs(result["lat"] - 1.35) < 1e-9
        assert abs(result["end_lat"] - 1.36) < 1e-9

    def test_required_keys_present(self):
        item = {"source": "strava", "evidence_type": "gps", "lat": 1.0, "lng": 103.0}
        result = self._e(item, "point")
        for key in ("kind", "source", "evidence_type", "lat", "lng",
                    "evidence_key", "confidence", "status"):
            assert key in result

    def test_label_from_name(self):
        item = {"name": "My home", "lat": 1.0, "lng": 103.0}
        result = self._e(item, "point")
        assert result["label"] == "My home"

    def test_occurred_at_from_date(self):
        item = {"date": "2026-01-15", "lat": 1.0, "lng": 103.0}
        result = self._e(item, "point")
        assert result["occurred_at"] == "2026-01-15"

    def test_empty_points_gives_none_lat(self):
        item = {"points": [], "source": "strava"}
        result = self._e(item, "route")
        assert result["lat"] is None
        assert result["end_lat"] is None


# ---------------------------------------------------------------------------
# api.routes.graph: _geo_events
# ---------------------------------------------------------------------------

class TestGeoEvents:
    def _e(self, routes, points, limit=250):
        from src.api.routes.graph import _geo_events
        return _geo_events(routes, points, limit)

    def _route(self, lat=1.35, lng=103.82, occurred_at="2026-01-15"):
        return {"points": [[lat, lng]], "source": "strava",
                "evidence_type": "route", "occurred_at": occurred_at}

    def _point(self, lat=1.35, lng=103.82, occurred_at="2026-01-10"):
        return {"lat": lat, "lng": lng, "source": "instagram",
                "evidence_type": "gps", "occurred_at": occurred_at}

    def test_combines_routes_and_points(self):
        result = self._e([self._route()], [self._point()])
        assert len(result) == 2

    def test_sorted_by_occurred_at_desc(self):
        r = self._route(occurred_at="2026-06-01")
        p = self._point(occurred_at="2026-01-01")
        result = self._e([r], [p])
        # Route (later) should come first
        assert result[0]["kind"] == "route"

    def test_empty_inputs(self):
        assert self._e([], []) == []

    def test_limit_applied(self):
        routes = [self._route(lat=float(i)) for i in range(200)]
        result = self._e(routes, [], limit=100)
        assert len(result) == 100

    def test_route_kind_correct(self):
        result = self._e([self._route()], [])
        assert result[0]["kind"] == "route"

    def test_point_kind_correct(self):
        result = self._e([], [self._point()])
        assert result[0]["kind"] == "point"
