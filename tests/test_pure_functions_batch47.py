"""
Pure-function tests — batch 47.

Covers previously untested pure functions:
- api.routes.intersections: _location_item_from_point
- api.routes.media: _row_to_dict, _iso, _analysis_preview
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.intersections: _location_item_from_point
# ---------------------------------------------------------------------------

class TestLocationItemFromPoint:
    def _l(self, point):
        from src.api.routes.intersections import _location_item_from_point
        return _location_item_from_point(point)

    def _make_point(self, **kw):
        from src.api.routes.intersections import _point
        defaults = dict(
            entity_id="eid-1", source="strava", record_id="rec-1",
            occurred_at=None, lat=1.35, lng=103.82, label="Home",
        )
        defaults.update(kw)
        return _point(**defaults)

    def test_basic_shape(self):
        p = self._make_point()
        item = self._l(p)
        assert item["source"] == "strava"
        assert item["lat"] == 1.35
        assert item["lng"] == 103.82

    def test_datetime_occurred_at_isoformat(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        p = self._make_point(occurred_at=dt)
        item = self._l(p)
        assert "2026-01-15" in item["occurred_at"]

    def test_none_occurred_at_passthrough(self):
        p = self._make_point(occurred_at=None)
        item = self._l(p)
        assert item["occurred_at"] is None

    def test_evidence_type_defaults(self):
        p = self._make_point()
        item = self._l(p)
        assert item["evidence_type"] == "gps"

    def test_required_keys_present(self):
        p = self._make_point()
        item = self._l(p)
        for key in ("source", "evidence_type", "lat", "lng", "label", "confidence"):
            assert key in item

    def test_string_occurred_at_passthrough(self):
        p = self._make_point(occurred_at="2026-01-01")
        item = self._l(p)
        assert item["occurred_at"] == "2026-01-01"


# ---------------------------------------------------------------------------
# api.routes.media: _row_to_dict, _iso, _analysis_preview
# ---------------------------------------------------------------------------

class TestMediaIso:
    def _i(self, v):
        from src.api.routes.media import _iso
        return _iso(v)

    def test_datetime_isoformat(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        assert "2026-01-15" in self._i(dt)

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_falsy_zero_returns_none(self):
        assert self._i(0) is None


class TestMediaRowToDict:
    def _make_row(self, **kw):
        import json
        defaults = {
            "id": "row-1",
            "media_item_id": "mid-1",
            "parent_media_item_id": None,
            "source": "instagram",
            "content_type": "image",
            "analysis_type": "exif_gps",
            "result_json": None,
            "extracted_text": None,
            "gps_lat": None,
            "gps_lon": None,
            "taken_at": None,
            "perceptual_hash": None,
            "face_embedding": None,
            "model_version": "v1",
            "processed_at": None,
        }
        defaults.update(kw)
        return defaults

    def _r(self, row):
        from src.api.routes.media import _row_to_dict
        return _row_to_dict(row)

    def test_required_keys(self):
        result = self._r(self._make_row())
        for key in ("id", "media_item_id", "source", "content_type",
                    "analysis_type", "has_text", "has_gps", "has_face",
                    "is_derived", "thumbnail_url"):
            assert key in result

    def test_id_is_string(self):
        result = self._r(self._make_row(id="abc"))
        assert result["id"] == "abc"

    def test_has_gps_false_when_no_coords(self):
        result = self._r(self._make_row(gps_lat=None, gps_lon=None))
        assert result["has_gps"] is False

    def test_has_gps_true_when_coords(self):
        result = self._r(self._make_row(gps_lat=1.35, gps_lon=103.82))
        assert result["has_gps"] is True

    def test_has_text_false_when_no_text(self):
        result = self._r(self._make_row(extracted_text=None))
        assert result["has_text"] is False

    def test_has_text_true_when_text(self):
        result = self._r(self._make_row(extracted_text="hello world"))
        assert result["has_text"] is True

    def test_text_preview_truncated_at_280(self):
        long_text = "x" * 400
        result = self._r(self._make_row(extracted_text=long_text))
        assert len(result["text_preview"]) <= 282  # 280 + "…"

    def test_has_face_false_when_no_embedding(self):
        result = self._r(self._make_row(face_embedding=None))
        assert result["has_face"] is False

    def test_is_derived_false_when_no_parent(self):
        result = self._r(self._make_row(parent_media_item_id=None))
        assert result["is_derived"] is False

    def test_is_derived_true_when_parent(self):
        result = self._r(self._make_row(parent_media_item_id="parent-1"))
        assert result["is_derived"] is True

    def test_result_json_parsed_from_string(self):
        import json
        result = self._r(self._make_row(result_json='{"key": "val"}'))
        assert result["result_json"] == {"key": "val"}

    def test_thumbnail_url_present(self):
        result = self._r(self._make_row(id="row-42"))
        assert "row-42" in result["thumbnail_url"]


class TestAnalysisPreview:
    def _p(self, row):
        from src.api.routes.media import _analysis_preview
        return _analysis_preview(row)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_basic_shape(self):
        row = {
            "analysis_id": "a1", "analysis_type": "exif_gps",
            "content_type": "image", "source": "instagram",
            "extracted_text": None, "gps_lat": 1.35, "gps_lon": 103.82,
            "taken_at": None, "processed_at": None,
        }
        result = self._p(row)
        assert result is not None
        assert result["analysis_id"] == "a1"

    def test_has_gps_true(self):
        row = {
            "analysis_id": "a1", "analysis_type": "exif_gps",
            "content_type": "image", "source": "instagram",
            "extracted_text": None, "gps_lat": 1.35, "gps_lon": 103.82,
            "taken_at": None, "processed_at": None,
        }
        result = self._p(row)
        assert result["has_gps"] is True

    def test_text_preview_truncated(self):
        long_text = "a" * 300
        row = {
            "analysis_id": "a1", "analysis_type": "ocr_text",
            "content_type": "image", "source": "instagram",
            "extracted_text": long_text, "gps_lat": None, "gps_lon": None,
            "taken_at": None, "processed_at": None,
        }
        result = self._p(row)
        assert len(result["text_preview"]) <= 185

    def test_no_analysis_id_thumbnail_none(self):
        row = {
            "analysis_id": None, "analysis_type": "exif_gps",
            "content_type": "image", "source": "instagram",
            "extracted_text": None, "gps_lat": None, "gps_lon": None,
            "taken_at": None, "processed_at": None,
        }
        result = self._p(row)
        assert result["thumbnail_url"] is None
