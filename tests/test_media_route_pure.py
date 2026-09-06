"""
QA-lane tests for pure helper functions in src/api/routes/media.py:
- _parse_pg_array_text: PostgreSQL text array parsing
- _estimated_rollup: frequency-weighted name list
- _int_row: int extraction from dict
- _coverage_item: coverage status dict builder
- _iso: datetime iso format
- _analysis_preview: analysis row → preview dict
- _is_real_media_uuid: UUID vs derived ID detection
- _derived_path_from_json: derived_path extraction from JSON
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.api.routes.media import (
    _analysis_preview,
    _coverage_item,
    _derived_path_from_json,
    _estimated_rollup,
    _int_row,
    _is_real_media_uuid,
    _iso,
    _parse_pg_array_text,
)

_NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _parse_pg_array_text
# ---------------------------------------------------------------------------

class TestParsePgArrayText:
    def test_empty_string_returns_empty(self):
        assert _parse_pg_array_text("") == []

    def test_empty_braces_returns_empty(self):
        assert _parse_pg_array_text("{}") == []

    def test_none_returns_empty(self):
        assert _parse_pg_array_text(None) == []

    def test_single_value(self):
        assert _parse_pg_array_text("{hello}") == ["hello"]

    def test_multiple_values(self):
        result = _parse_pg_array_text("{a,b,c}")
        assert result == ["a", "b", "c"]

    def test_quoted_values_unquoted(self):
        result = _parse_pg_array_text('{"image/jpeg","video/mp4"}')
        assert "image/jpeg" in result
        assert "video/mp4" in result


# ---------------------------------------------------------------------------
# _estimated_rollup
# ---------------------------------------------------------------------------

class TestEstimatedRollup:
    def test_empty_inputs_returns_empty(self):
        assert _estimated_rollup(0, None, None, "type") == []

    def test_names_and_weights(self):
        result = _estimated_rollup(100, "{photo,video}", "{0.7,0.3}", "type")
        assert len(result) == 2
        assert result[0]["type"] == "photo"
        assert result[0]["n"] == 70

    def test_sorted_descending_by_count(self):
        result = _estimated_rollup(100, "{a,b}", "{0.2,0.8}", "kind")
        assert result[0]["n"] >= result[1]["n"]

    def test_mismatched_length_uses_zip(self):
        # zip stops at shorter: 2 names, 3 weights → 2 pairs
        result = _estimated_rollup(100, "{a,b}", "{0.5,0.3,0.2}", "k")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _int_row
# ---------------------------------------------------------------------------

class TestIntRow:
    def test_returns_int(self):
        assert _int_row({"k": 5}, "k") == 5

    def test_none_returns_zero(self):
        assert _int_row({"k": None}, "k") == 0

    def test_missing_key_returns_zero(self):
        assert _int_row({}, "k") == 0


# ---------------------------------------------------------------------------
# _coverage_item
# ---------------------------------------------------------------------------

class TestCoverageItem:
    def test_covered_when_count_positive(self):
        result = _coverage_item("gps", "GPS", 5, basis="exact")
        assert result["status"] == "covered"
        assert result["count"] == 5

    def test_missing_when_count_zero(self):
        result = _coverage_item("ocr", "OCR", 0, basis="exact")
        assert result["status"] == "missing"

    def test_processed_defaults_to_count(self):
        result = _coverage_item("face", "Face", 10, basis="estimated")
        assert result["processed"] == 10

    def test_custom_processed(self):
        result = _coverage_item("face", "Face", 10, processed=8, basis="exact")
        assert result["processed"] == 8

    def test_keys_present(self):
        result = _coverage_item("k", "Label", 3, basis="exact")
        for key in ("key", "label", "status", "count", "processed", "basis"):
            assert key in result


# ---------------------------------------------------------------------------
# _iso
# ---------------------------------------------------------------------------

class TestMediaIso:
    def test_datetime_isoformat(self):
        result = _iso(_NOW)
        assert "2024-06-01" in result

    def test_none_returns_none(self):
        assert _iso(None) is None

    def test_falsy_zero_returns_none(self):
        assert _iso(0) is None


# ---------------------------------------------------------------------------
# _analysis_preview
# ---------------------------------------------------------------------------

class TestAnalysisPreview:
    def _row(self, **overrides):
        base = {
            "analysis_id": "aid-1",
            "analysis_type": "ocr",
            "content_type": "image/jpeg",
            "source": "instagram",
            "extracted_text": "Hello world",
            "gps_lat": None,
            "gps_lon": None,
            "taken_at": _NOW,
            "processed_at": _NOW,
        }
        base.update(overrides)
        return base

    def test_none_row_returns_none(self):
        assert _analysis_preview(None) is None

    def test_basic_fields(self):
        result = _analysis_preview(self._row())
        assert result["analysis_id"] == "aid-1"
        assert result["has_text"] is True

    def test_text_truncated_at_180(self):
        long_text = "x" * 200
        result = _analysis_preview(self._row(extracted_text=long_text))
        assert result["text_preview"].endswith("...")
        assert len(result["text_preview"]) <= 184

    def test_short_text_not_truncated(self):
        result = _analysis_preview(self._row(extracted_text="short"))
        assert result["text_preview"] == "short"

    def test_has_gps_true_when_coords_set(self):
        result = _analysis_preview(self._row(gps_lat=1.35, gps_lon=103.82))
        assert result["has_gps"] is True

    def test_has_gps_false_when_none(self):
        result = _analysis_preview(self._row(gps_lat=None, gps_lon=None))
        assert result["has_gps"] is False

    def test_thumbnail_url_present(self):
        result = _analysis_preview(self._row())
        assert "/api/media/aid-1/thumbnail" in result["thumbnail_url"]


# ---------------------------------------------------------------------------
# _is_real_media_uuid
# ---------------------------------------------------------------------------

class TestIsRealMediaUuid:
    def test_valid_uuid_with_dashes(self):
        assert _is_real_media_uuid("00000000-0000-0000-0000-000000000001") is True

    def test_valid_uuid_without_dashes(self):
        assert _is_real_media_uuid("00000000000000000000000000000001") is True

    def test_derived_id_with_colon_returns_false(self):
        assert _is_real_media_uuid("00000000-0000-0000-0000-000000000001:pdf_img:1:12") is False

    def test_frame_id_returns_false(self):
        assert _is_real_media_uuid("00000000-0000-0000-0000-000000000001:frame:0") is False

    def test_wrong_length_returns_false(self):
        assert _is_real_media_uuid("short") is False


# ---------------------------------------------------------------------------
# _derived_path_from_json
# ---------------------------------------------------------------------------

class TestDerivedPathFromJson:
    def test_dict_with_derived_path(self):
        assert _derived_path_from_json({"derived_path": "/path/to/file"}) == "/path/to/file"

    def test_json_string_parsed(self):
        import json
        raw = json.dumps({"derived_path": "/some/path"})
        assert _derived_path_from_json(raw) == "/some/path"

    def test_missing_key_returns_none(self):
        assert _derived_path_from_json({"other": "value"}) is None

    def test_none_returns_none(self):
        assert _derived_path_from_json(None) is None

    def test_invalid_json_returns_none(self):
        assert _derived_path_from_json("bad{json") is None
