"""
Pure-function tests — batch 106.

Covers:
- pipeline.location_evidence: _geometry_from_item, _first_point_coord
- pipeline.identity_calibration: _rows_to_xy
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _geometry_from_item
# ---------------------------------------------------------------------------

class TestGeometryFromItem:
    def _g(self, item):
        from src.pipeline.location_evidence import _geometry_from_item
        return _geometry_from_item(item)

    def test_empty_item_returns_empty(self):
        assert self._g({}) == {}

    def test_no_points_returns_empty(self):
        assert self._g({"source": "strava"}) == {}

    def test_empty_points_returns_empty(self):
        assert self._g({"points": []}) == {}

    def test_non_list_points_returns_empty(self):
        assert self._g({"points": "not a list"}) == {}

    def test_valid_points_returns_linestring(self):
        result = self._g({"points": [[1.35, 103.82], [1.36, 103.83]]})
        assert result["type"] == "LineString"
        assert result["points"] == [[1.35, 103.82], [1.36, 103.83]]

    def test_single_point_returns_linestring(self):
        result = self._g({"points": [[1.35, 103.82]]})
        assert result["type"] == "LineString"


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _first_point_coord
# ---------------------------------------------------------------------------

class TestFirstPointCoord:
    def _c(self, points, idx):
        from src.pipeline.location_evidence import _first_point_coord
        return _first_point_coord(points, idx)

    def test_none_returns_none(self):
        assert self._c(None, 0) is None

    def test_empty_list_returns_none(self):
        assert self._c([], 0) is None

    def test_non_list_returns_none(self):
        assert self._c("not a list", 0) is None

    def test_first_point_lat(self):
        result = self._c([[1.35, 103.82]], 0)
        assert result is not None
        assert abs(result - 1.35) < 1e-9

    def test_first_point_lng(self):
        result = self._c([[1.35, 103.82], [1.36, 103.83]], 1)
        assert result is not None
        assert abs(result - 103.82) < 1e-9

    def test_index_out_of_range_returns_none(self):
        assert self._c([[1.35]], 1) is None


# ---------------------------------------------------------------------------
# pipeline.identity_calibration: _rows_to_xy
# ---------------------------------------------------------------------------

class TestRowsToXy:
    def _r(self, rows):
        from src.pipeline.identity_calibration import _rows_to_xy
        return _rows_to_xy(rows)

    def _make_row(self, label, source="manual", features=None):
        import json
        return {
            "features": json.dumps(features or {}),
            "label": label,
            "source": source,
        }

    def test_empty_rows_returns_empty(self):
        X, y = self._r([])
        assert X == []
        assert y == []

    def test_single_row_no_expansion(self):
        row = self._make_row(1, "human")
        X, y = self._r([row])
        assert len(X) == 1
        assert len(y) == 1

    def test_auto_positive_loso_expanded(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        # auto_ + label=1 + non-zero feature → gets LOSO expansion
        features = {FEATURE_ORDER[0]: 0.9} if FEATURE_ORDER else {}
        row = self._make_row(1, "auto_merge", features)
        X, y = self._r([row])
        # Should have at least 2 rows (base + one LOSO variant)
        assert len(X) >= 1
        assert all(label == 1 for label in y)

    def test_auto_negative_not_expanded(self):
        row = self._make_row(0, "auto_negative")
        X, y = self._r([row])
        assert len(X) == 1
        assert y[0] == 0

    def test_labels_preserved(self):
        rows = [self._make_row(1, "human"), self._make_row(0, "human")]
        X, y = self._r(rows)
        assert 1 in y
        assert 0 in y
