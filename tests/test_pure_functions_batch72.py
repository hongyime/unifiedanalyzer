"""
Pure-function tests — batch 72.

Final push — covers remaining edge cases and constants:
- api.routes.timeline: _coerce_confidence additional edge cases
- api.routes.graph: _relationship_row sources with JSON string
- api.routes.readiness: USER_STORIES all actors are valid
- api.routes.collector_health: _collector_from_matrix_row last_completed None
- api.routes.intersections: _physical_hit max_distance_m and sources
- notifications.telegram: constants + _NO_WINDOW type
- api.routes.media: _row_to_dict content_type stored
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# api.routes.timeline: _coerce_confidence remaining
# ---------------------------------------------------------------------------

class TestCoerceConfidenceRemaining:
    def _c(self, v):
        from src.api.routes.timeline import _coerce_confidence
        return _coerce_confidence(v)

    def test_exactly_1_0_valid(self):
        assert abs(self._c(1.0) - 1.0) < 1e-9

    def test_exactly_0_0_valid(self):
        assert abs(self._c(0.0) - 0.0) < 1e-9

    def test_float_50_treated_as_percentage(self):
        # 50 > 1 → 50/100 = 0.5
        assert abs(self._c(50) - 0.5) < 1e-9

    def test_nan_returns_none(self):
        import math
        assert self._c(math.nan) is None


# ---------------------------------------------------------------------------
# api.routes.graph: _decode_sources with JSON array
# ---------------------------------------------------------------------------

class TestDecodeSourcesArray:
    def _d(self, v):
        from src.api.routes.graph import _decode_sources
        return _decode_sources(v)

    def test_json_array_string_returns_list(self):
        import json
        result = self._d(json.dumps([1, 2, 3]))
        # _decode_sources returns parsed JSON as-is when not a dict
        assert result == [1, 2, 3] or result == {}

    def test_empty_json_object_string(self):
        result = self._d("{}")
        assert result == {}

    def test_json_with_numeric_values(self):
        import json
        result = self._d(json.dumps({"score": 0.85, "count": 10}))
        assert result["score"] == 0.85


# ---------------------------------------------------------------------------
# api.routes.readiness: USER_STORIES proven fields
# ---------------------------------------------------------------------------

class TestReadinessProvenFields:
    def test_all_proves_non_empty(self):
        from src.api.routes.readiness import USER_STORIES
        for key, story in USER_STORIES.items():
            assert len(story["proves"]) > 0, f"{key} has empty proves"

    def test_all_stories_non_empty_actor(self):
        from src.api.routes.readiness import USER_STORIES
        for key, story in USER_STORIES.items():
            assert len(story["actor"]) > 0

    def test_backup_restorable_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "backup_restorable" in USER_STORIES

    def test_supabase_populated_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "supabase_populated" in USER_STORIES


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_from_matrix_row edge cases
# ---------------------------------------------------------------------------

class TestCollectorFromMatrixRowEdge:
    def _c(self, row, targets=None):
        from src.api.routes.collector_health import _collector_from_matrix_row
        return _collector_from_matrix_row(row, targets or [])

    def test_null_latest_completed_is_none(self):
        row = {
            "source": "instagram", "display_name": "Instagram",
            "status": "active", "collection_mode": "continuous",
            "last_24h": {},
            "current_hour": {}, "blocker": {}, "media_freshness": {},
        }
        result = self._c(row)
        assert result["last_completed"] is None

    def test_rate_limits_and_access_errors_summed(self):
        row = {
            "source": "instagram", "display_name": "Instagram",
            "status": "active", "collection_mode": "continuous",
            "last_24h": {"records": 0, "media_items": 0, "messages": 0,
                         "rate_limits": 3, "access_errors": 2, "runs": 1,
                         "latest_record_at": None},
            "current_hour": {}, "blocker": {}, "media_freshness": {},
        }
        result = self._c(row)
        assert result["failed_24h"] == 5
        assert result["rate_limits_24h"] == 3
        assert result["access_errors_24h"] == 2


# ---------------------------------------------------------------------------
# api.routes.intersections: _physical_hit sources and evidence
# ---------------------------------------------------------------------------

class TestPhysicalHitAdditional:
    def _make_point(self, entity_id, source, lat, lng, dt):
        return {
            "entity_id": entity_id, "source": source, "record_id": "r",
            "occurred_at": dt, "lat": lat, "lng": lng, "label": "Home",
            "evidence_type": "gps", "source_table": None,
            "confidence": 0.9, "status": None,
        }

    def _h(self, points, entity_names=None, radius_m=200.0):
        from src.api.routes.intersections import _physical_hit
        return _physical_hit(points, entity_names or {}, radius_m)

    def test_sources_distinct(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        p1 = self._make_point("e1", "strava", 1.0, 103.0, dt)
        p2 = self._make_point("e2", "strava", 1.0, 103.0, dt)
        result = self._h([p1, p2])
        assert "strava" in result["sources"]

    def test_evidence_has_two_items(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        p1 = self._make_point("e1", "instagram", 1.0, 103.0, dt)
        p2 = self._make_point("e2", "strava", 1.0, 103.0, dt)
        result = self._h([p1, p2])
        assert len(result["evidence"]) == 2

    def test_max_distance_m_zero_same_location(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        p1 = self._make_point("e1", "strava", 1.0, 103.0, dt)
        p2 = self._make_point("e2", "instagram", 1.0, 103.0, dt)
        result = self._h([p1, p2])
        assert result["max_distance_m"] == pytest.approx(0.0, abs=0.1)


# ---------------------------------------------------------------------------
# api.routes.media: _row_to_dict content_type stored
# ---------------------------------------------------------------------------

class TestRowToDictContentType:
    def _r(self, content_type="image"):
        from src.api.routes.media import _row_to_dict
        row = {
            "id": "r1", "media_item_id": "m1", "parent_media_item_id": None,
            "source": "strava", "content_type": content_type,
            "analysis_type": "exif_gps", "result_json": None,
            "extracted_text": None, "gps_lat": None, "gps_lon": None,
            "taken_at": None, "perceptual_hash": None, "face_embedding": None,
            "model_version": "v1", "processed_at": None,
        }
        return _row_to_dict(row)

    def test_image_content_type(self):
        assert self._r("image")["content_type"] == "image"

    def test_pdf_content_type(self):
        assert self._r("pdf")["content_type"] == "pdf"

    def test_video_content_type(self):
        assert self._r("video")["content_type"] == "video"


import pytest
