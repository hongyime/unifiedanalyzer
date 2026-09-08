"""
Pure-function tests — batch 73.

Covers remaining edge cases in the final modules.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json
import pytest


# ---------------------------------------------------------------------------
# api.routes.graph: confidence_bucket with 0 weight
# ---------------------------------------------------------------------------

class TestConfidenceBucketZeroWeight:
    def _b(self, rtype, weight, cross=False):
        from src.api.routes.graph import confidence_bucket
        return confidence_bucket(rtype, weight, cross)

    def test_all_zero_context_only(self):
        for rtype in ("some_rel", "interaction", "social_graph_overlap", "temporal_co"):
            result = self._b(rtype, 0)
            assert result in ("context-only", "weak")

    def test_none_weight_treated_as_zero(self):
        result = self._b("some_rel", None)
        assert result in ("context-only", "weak")

    def test_same_person_with_none_weight(self):
        result = self._b("same_person_probability", None)
        assert result == "strong"  # weight=None → float(0) < 80 → "strong"


# ---------------------------------------------------------------------------
# api.routes.intersections: _point with all optional fields
# ---------------------------------------------------------------------------

class TestPointWithAllOptionals:
    def _p(self, **kw):
        from src.api.routes.intersections import _point
        defaults = dict(entity_id="eid-1", source="strava", record_id="r1",
                        occurred_at=None, lat=1.35, lng=103.82, label=None)
        defaults.update(kw)
        return _point(**defaults)

    def test_source_record_id_matches_record_id(self):
        p = self._p(record_id="activity-123")
        assert p["source_record_id"] == "activity-123"

    def test_evidence_type_custom(self):
        p = self._p(evidence_type="route_polyline")
        assert p["evidence_type"] == "route_polyline"

    def test_source_table_stored(self):
        p = self._p(source_table="strava_activities")
        assert p["source_table"] == "strava_activities"

    def test_evidence_key_none_by_default(self):
        p = self._p()
        assert "evidence_key" not in p


# ---------------------------------------------------------------------------
# api.routes.graph: _decode_sources non-string non-dict
# ---------------------------------------------------------------------------

class TestDecodeSourcesFinal:
    def _d(self, v):
        from src.api.routes.graph import _decode_sources
        return _decode_sources(v)

    def test_float_returns_empty(self):
        assert self._d(3.14) == {}

    def test_true_returns_empty(self):
        assert self._d(True) == {}

    def test_json_with_array_value(self):
        result = self._d(json.dumps({"sources": ["a", "b"]}))
        assert result["sources"] == ["a", "b"]


# ---------------------------------------------------------------------------
# api.routes.timeline: _metadata_path_value nested 3 levels
# ---------------------------------------------------------------------------

class TestMetadataPathValueDeep:
    def _m(self, metadata, path):
        from src.api.routes.timeline import _metadata_path_value
        return _metadata_path_value(metadata, path)

    def test_three_level_path(self):
        d = {"a": {"b": {"c": 0.75}}}
        assert abs(self._m(d, ("a", "b", "c")) - 0.75) < 1e-9

    def test_missing_intermediate_returns_none(self):
        d = {"a": {"x": 1}}
        assert self._m(d, ("a", "b", "c")) is None

    def test_empty_path_returns_metadata(self):
        d = {"k": 1}
        assert self._m(d, ()) == d


# ---------------------------------------------------------------------------
# api.routes.intersections: IntersectRequest from/to dates
# ---------------------------------------------------------------------------

class TestIntersectRequestDates:
    def test_from_to_aliases(self):
        from src.api.routes.intersections import IntersectRequest
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Pydantic uses populate_by_name=True — try alias
        req = IntersectRequest(
            ids=["a", "b"],
            **{"from": dt, "to": dt}
        )
        assert req.from_date == dt
        assert req.to_date == dt

    def test_default_from_to_none(self):
        from src.api.routes.intersections import IntersectRequest
        req = IntersectRequest(ids=["a", "b"])
        assert req.from_date is None
        assert req.to_date is None


# ---------------------------------------------------------------------------
# api.routes.collector_health: _targets_by_source isoformat datetime
# ---------------------------------------------------------------------------

class TestTargetsBySourceIso:
    def test_datetime_isoformat_in_result(self):
        from src.api.routes.collector_health import _targets_by_source
        dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
        targets = [{"source": "strava", "status": "active", "count": 10, "last_collection": dt}]
        result = _targets_by_source(targets)
        assert "2026-03-15" in result["strava"][0]["last_collection"]

    def test_none_collection_stored_as_none(self):
        from src.api.routes.collector_health import _targets_by_source
        targets = [{"source": "strava", "status": "active", "count": 0, "last_collection": None}]
        result = _targets_by_source(targets)
        assert result["strava"][0]["last_collection"] is None


# ---------------------------------------------------------------------------
# api.routes.media: _analysis_preview all keys
# ---------------------------------------------------------------------------

class TestAnalysisPreviewAllKeys:
    def _p(self, row):
        from src.api.routes.media import _analysis_preview
        return _analysis_preview(row)

    def test_all_expected_keys(self):
        row = {
            "analysis_id": "a1", "analysis_type": "exif_gps",
            "content_type": "image", "source": "instagram",
            "extracted_text": "hello", "gps_lat": 1.35, "gps_lon": 103.82,
            "taken_at": None, "processed_at": None,
        }
        result = self._p(row)
        for key in ("analysis_id", "analysis_type", "content_type", "source",
                    "text_preview", "has_text", "has_gps", "gps_lat", "gps_lon",
                    "taken_at", "processed_at", "thumbnail_url"):
            assert key in result

    def test_has_text_true(self):
        row = {
            "analysis_id": "a1", "analysis_type": "ocr_text",
            "content_type": "image", "source": "telegram",
            "extracted_text": "hello world", "gps_lat": None, "gps_lon": None,
            "taken_at": None, "processed_at": None,
        }
        result = self._p(row)
        assert result["has_text"] is True
        assert result["text_preview"] == "hello world"
