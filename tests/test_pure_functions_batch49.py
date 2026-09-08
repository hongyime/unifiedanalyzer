"""
Pure-function tests — batch 49.

Covers previously untested pure functions:
- api.routes.collector_health: _targets_by_source, _collector_from_matrix_row,
  _collectors_from_source_matrix, _collector_from_live_row
- api.routes.graph: _relationship_row (pure dict builder with mock row)
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.collector_health: _targets_by_source
# ---------------------------------------------------------------------------

class TestTargetsBySource:
    def _t(self, targets):
        from src.api.routes.collector_health import _targets_by_source
        return _targets_by_source(targets)

    def _make_target(self, source, status="active", count=100, last_collection=None):
        return {
            "source": source,
            "status": status,
            "count": count,
            "last_collection": last_collection,
        }

    def test_groups_by_source(self):
        targets = [
            self._make_target("instagram"),
            self._make_target("telegram"),
            self._make_target("instagram"),
        ]
        result = self._t(targets)
        assert "instagram" in result
        assert "telegram" in result
        assert len(result["instagram"]) == 2
        assert len(result["telegram"]) == 1

    def test_each_item_has_required_keys(self):
        targets = [self._make_target("instagram")]
        result = self._t(targets)
        item = result["instagram"][0]
        assert "status" in item
        assert "count" in item
        assert "last_collection" in item

    def test_datetime_last_collection_iso(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        targets = [self._make_target("instagram", last_collection=dt)]
        result = self._t(targets)
        assert "2026-01-15" in result["instagram"][0]["last_collection"]

    def test_none_last_collection_none(self):
        targets = [self._make_target("instagram", last_collection=None)]
        result = self._t(targets)
        assert result["instagram"][0]["last_collection"] is None

    def test_empty_targets_returns_empty(self):
        assert self._t([]) == {}


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_from_matrix_row
# ---------------------------------------------------------------------------

class TestCollectorFromMatrixRow:
    def _make_row(self, **kw):
        defaults = {
            "source": "instagram",
            "display_name": "Instagram",
            "status": "active",
            "collection_mode": "continuous",
            "last_24h": {"records": 100, "media_items": 50, "messages": 0,
                         "rate_limits": 2, "access_errors": 1, "runs": 5,
                         "latest_record_at": "2026-01-15T12:00:00Z"},
            "current_hour": {},
            "blocker": {},
            "media_freshness": {},
        }
        defaults.update(kw)
        return defaults

    def _c(self, row, targets=None):
        from src.api.routes.collector_health import _collector_from_matrix_row
        return _collector_from_matrix_row(row, targets or [])

    def test_source_stored(self):
        result = self._c(self._make_row())
        assert result["source"] == "instagram"

    def test_items_24h_sum(self):
        result = self._c(self._make_row())
        # records=100 + media=50 = 150
        assert result["items_24h"] == 150

    def test_failed_24h_sum(self):
        result = self._c(self._make_row())
        # rate_limits=2 + access_errors=1 = 3
        assert result["failed_24h"] == 3

    def test_required_keys_present(self):
        result = self._c(self._make_row())
        for key in ("source", "status", "items_24h", "records_24h",
                    "failed_24h", "last_completed"):
            assert key in result

    def test_empty_last_24h(self):
        row = self._make_row(last_24h={})
        result = self._c(row)
        assert result["items_24h"] == 0
        assert result["failed_24h"] == 0


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collectors_from_source_matrix
# ---------------------------------------------------------------------------

class TestCollectorsFromSourceMatrix:
    def _c(self, matrix, targets=None):
        from src.api.routes.collector_health import _collectors_from_source_matrix
        return _collectors_from_source_matrix(matrix, targets or [])

    def test_empty_matrix_returns_empty(self):
        assert self._c({}) == []

    def test_empty_sources_returns_empty(self):
        assert self._c({"sources": []}) == []

    def test_parses_source_rows(self):
        matrix = {
            "sources": [
                {"source": "telegram", "status": "active", "last_24h": {},
                 "current_hour": {}, "blocker": {}, "media_freshness": {}},
            ]
        }
        result = self._c(matrix)
        assert len(result) == 1
        assert result[0]["source"] == "telegram"

    def test_sorted_by_source(self):
        matrix = {
            "sources": [
                {"source": "z_source", "status": "active", "last_24h": {},
                 "current_hour": {}, "blocker": {}, "media_freshness": {}},
                {"source": "a_source", "status": "active", "last_24h": {},
                 "current_hour": {}, "blocker": {}, "media_freshness": {}},
            ]
        }
        result = self._c(matrix)
        assert result[0]["source"] == "a_source"

    def test_skips_rows_without_source(self):
        matrix = {
            "sources": [
                {"status": "active"},  # no source key
                {"source": "instagram", "status": "active", "last_24h": {},
                 "current_hour": {}, "blocker": {}, "media_freshness": {}},
            ]
        }
        result = self._c(matrix)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# api.routes.graph: _relationship_row (pure dict builder with mock row)
# ---------------------------------------------------------------------------

class TestRelationshipRow:
    def _make_row(self, **kw):
        defaults = {
            "id": "rel-1",
            "entity_a_id": "eid-a",
            "entity_b_id": "eid-b",
            "relationship_type": "interaction",
            "weight": 10,
            "cross_platform": True,
            "sources": {"total": 10, "by_type": {"replied": 5}},
            "last_seen_at": None,
        }
        defaults.update(kw)
        return defaults

    def _r(self, row):
        from src.api.routes.graph import _relationship_row
        return _relationship_row(row)

    def test_required_keys(self):
        result = self._r(self._make_row())
        for key in ("id", "from_entity_id", "to_entity_id", "relationship_type",
                    "weight", "cross_platform", "confidence_bucket", "why"):
            assert key in result

    def test_confidence_bucket_present(self):
        result = self._r(self._make_row())
        assert result["confidence_bucket"] in ("hard", "strong", "weak", "context-only")

    def test_last_seen_at_none(self):
        result = self._r(self._make_row(last_seen_at=None))
        assert result["last_seen_at"] is None

    def test_last_seen_at_iso(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        result = self._r(self._make_row(last_seen_at=dt))
        assert "2026-01-15" in result["last_seen_at"]

    def test_id_is_string(self):
        result = self._r(self._make_row(id="abc-123"))
        assert result["id"] == "abc-123"
