"""
Pure-function tests — batch 69.

Covers previously untested pure functions:
- api.routes.media: coverage item keys for pdf/ocr/video/faces/exif/contact
- api.routes.intersections: _location_item_from_point edge cases, _point edge cases
- api.routes.collector_health: _collector_from_live_row latest_iso selection
- api.routes.graph: _geo_event with occurred_at and label variants
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.media: coverage item construction
# ---------------------------------------------------------------------------

class TestMediaCoverageItems:
    def _c(self, key, label, count, processed=None, basis="test"):
        from src.api.routes.media import _coverage_item
        return _coverage_item(key, label, count, processed=processed, basis=basis)

    def test_pdf_text_keys(self):
        result = self._c("pdf_text", "PDF text", 5, processed=10, basis="pdf")
        assert result["key"] == "pdf_text"
        assert result["label"] == "PDF text"
        assert result["basis"] == "pdf"
        assert result["processed"] == 10

    def test_faces_covered(self):
        result = self._c("faces", "Faces", 100, basis="face_embedding")
        assert result["status"] == "covered"
        assert result["count"] == 100

    def test_contact_signals_key(self):
        result = self._c("contact_signals", "Contact signals", 50, basis="media_items")
        assert result["key"] == "contact_signals"
        assert result["status"] == "covered"

    def test_zero_count_ocr(self):
        result = self._c("ocr_text", "OCR text", 0, processed=100, basis="ocr")
        assert result["status"] == "missing"
        assert result["processed"] == 100

    def test_video_frames_missing(self):
        result = self._c("video_frames", "Video frames", 0, processed=0, basis="video")
        assert result["status"] == "missing"


# ---------------------------------------------------------------------------
# api.routes.intersections: _location_item_from_point edge cases
# ---------------------------------------------------------------------------

class TestLocationItemEdgeCases:
    def _l(self, point):
        from src.api.routes.intersections import _location_item_from_point
        return _location_item_from_point(point)

    def _make_point(self, **kw):
        from src.api.routes.intersections import _point
        defaults = dict(entity_id="eid-1", source="strava", record_id="r1",
                        occurred_at=None, lat=1.35, lng=103.82, label=None)
        defaults.update(kw)
        return _point(**defaults)

    def test_confidence_none_when_not_set(self):
        p = self._make_point()
        item = self._l(p)
        assert item["confidence"] is None

    def test_confidence_stored_when_set(self):
        p = self._make_point(confidence=0.85)
        item = self._l(p)
        assert abs(item["confidence"] - 0.85) < 1e-9

    def test_source_table_none_when_not_set(self):
        p = self._make_point()
        item = self._l(p)
        assert item["source_table"] is None

    def test_all_required_keys(self):
        p = self._make_point()
        item = self._l(p)
        for key in ("source", "evidence_type", "lat", "lng", "label",
                    "occurred_at", "confidence", "evidence_key"):
            assert key in item


# ---------------------------------------------------------------------------
# api.routes.intersections: _point edge cases
# ---------------------------------------------------------------------------

class TestPointEdgeCases:
    def _p(self, **kw):
        from src.api.routes.intersections import _point
        defaults = dict(entity_id="eid-1", source="instagram", record_id="r1",
                        occurred_at=None, lat=1.35, lng=103.82, label=None)
        defaults.update(kw)
        return _point(**defaults)

    def test_none_label_stored(self):
        p = self._p(label=None)
        assert p["label"] is None

    def test_string_label(self):
        p = self._p(label="My location")
        assert p["label"] == "My location"

    def test_status_none_by_default(self):
        p = self._p()
        assert p["status"] is None

    def test_custom_status(self):
        p = self._p(status="confirmed")
        assert p["status"] == "confirmed"

    def test_entity_id_stored(self):
        p = self._p(entity_id="test-eid")
        assert p["entity_id"] == "test-eid"


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_from_live_row latest_iso
# ---------------------------------------------------------------------------

class TestCollectorFromLiveRowLatestIso:
    def _c(self, row, targets=None):
        from src.api.routes.collector_health import _collector_from_live_row
        return _collector_from_live_row(row, targets or [])

    def test_all_none_timestamps_last_completed_none(self):
        row = {
            "source": "telegram", "status": "active",
            "collection_mode": "continuous",
            "source_health_last_success_at": None,
            "source_health_updated_at": None,
            "browser_heartbeat_at": None,
            "bridge_status": None, "bridge_detail": None, "detail": None,
        }
        result = self._c(row)
        assert result["last_completed"] is None

    def test_updated_at_used_when_last_success_none(self):
        row = {
            "source": "instagram", "status": "active",
            "collection_mode": "continuous",
            "source_health_last_success_at": None,
            "source_health_updated_at": "2026-02-15T10:00:00Z",
            "browser_heartbeat_at": None,
            "bridge_status": None, "bridge_detail": None, "detail": None,
        }
        result = self._c(row)
        assert result["last_completed"] is not None
        assert "2026-02-15" in result["last_completed"]


# ---------------------------------------------------------------------------
# api.routes.graph: _geo_event with various label configurations
# ---------------------------------------------------------------------------

class TestGeoEventLabels:
    def _e(self, item, kind):
        from src.api.routes.graph import _geo_event
        return _geo_event(item, kind)

    def test_label_from_label_field(self):
        item = {"label": "my_label", "lat": 1.0, "lng": 103.0, "evidence_type": "gps"}
        result = self._e(item, "point")
        assert result["label"] == "my_label"

    def test_label_falls_back_to_name(self):
        item = {"name": "fallback_name", "lat": 1.0, "lng": 103.0}
        result = self._e(item, "point")
        assert result["label"] == "fallback_name"

    def test_label_from_label_wins_over_name(self):
        item = {"label": "primary", "name": "secondary", "lat": 1.0, "lng": 103.0}
        result = self._e(item, "point")
        assert result["label"] == "primary"

    def test_both_none_label_is_none(self):
        item = {"lat": 1.0, "lng": 103.0}
        result = self._e(item, "point")
        assert result["label"] is None

    def test_source_record_id_stored(self):
        item = {
            "lat": 1.0, "lng": 103.0,
            "source_record_id": "act-123",
            "source": "strava",
        }
        result = self._e(item, "point")
        assert result["source_record_id"] == "act-123"
