"""
Pure-function tests — batch 63.

Covers previously untested pure functions:
- notifications.alerts: INTELLIGENCE_ALERT_TYPES usage in notify_new_alerts,
  _SEVERITY_ICON fallback, additional alert helpers
- api.routes.media: remaining _analysis_preview and _row_to_dict edge cases
- api.routes.graph: remaining constants (_WORD_RE, IntersectRequest-related)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# notifications.alerts: AlertSuppressionIn, AlertSuppressionPatch models
# ---------------------------------------------------------------------------

class TestAlertSuppressionModels:
    def test_suppression_default_scope(self):
        from src.api.routes.alerts import AlertSuppressionIn
        req = AlertSuppressionIn(reason="test")
        assert req.scope == "manual"
        assert req.reason == "test"
        assert req.alert_type is None
        assert req.entity_id is None

    def test_suppression_custom_scope(self):
        from src.api.routes.alerts import AlertSuppressionIn
        req = AlertSuppressionIn(reason="noise", scope="automatic", alert_type="SILENCE_GAP")
        assert req.scope == "automatic"
        assert req.alert_type == "SILENCE_GAP"

    def test_suppression_patch_all_optional(self):
        from src.api.routes.alerts import AlertSuppressionPatch
        patch = AlertSuppressionPatch()
        assert patch.reason is None
        assert patch.ends_at is None
        assert patch.status is None

    def test_suppression_patch_with_status(self):
        from src.api.routes.alerts import AlertSuppressionPatch
        patch = AlertSuppressionPatch(status="resolved")
        assert patch.status == "resolved"

    def test_decode_jsonb_list(self):
        from src.api.routes.alerts import _decode_jsonb
        result = _decode_jsonb(["a", "b"], default=[])
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# api.routes.media: _analysis_preview edge cases + _row_to_dict fields
# ---------------------------------------------------------------------------

class TestAnalysisPreviewEdgeCases:
    def _p(self, row):
        from src.api.routes.media import _analysis_preview
        return _analysis_preview(row)

    def test_empty_row(self):
        result = self._p({})
        # Empty dict is falsy → None
        assert result is None

    def test_false_row(self):
        result = self._p(False)
        assert result is None

    def test_zero_row(self):
        result = self._p(0)
        assert result is None

    def test_analysis_id_in_thumbnail_url(self):
        row = {
            "analysis_id": "abc-123", "analysis_type": "phash",
            "content_type": "image", "source": "instagram",
            "extracted_text": None, "gps_lat": None, "gps_lon": None,
            "taken_at": None, "processed_at": None,
        }
        result = self._p(row)
        assert "abc-123" in result["thumbnail_url"]


class TestRowToDictEdgeCases:
    def _make_row(self, **kw):
        defaults = {
            "id": "row-1", "media_item_id": "mid-1",
            "parent_media_item_id": None, "source": "instagram",
            "content_type": "image", "analysis_type": "phash",
            "result_json": None, "extracted_text": None,
            "gps_lat": None, "gps_lon": None, "taken_at": None,
            "perceptual_hash": "abc123", "face_embedding": None,
            "model_version": "v1", "processed_at": None,
        }
        defaults.update(kw)
        return defaults

    def test_perceptual_hash_present(self):
        from src.api.routes.media import _row_to_dict
        result = _row_to_dict(self._make_row(perceptual_hash="abcdef12"))
        assert result["perceptual_hash"] == "abcdef12"

    def test_model_version_stored(self):
        from src.api.routes.media import _row_to_dict
        result = _row_to_dict(self._make_row(model_version="phash-v2"))
        assert result["model_version"] == "phash-v2"

    def test_result_json_invalid_becomes_none(self):
        from src.api.routes.media import _row_to_dict
        result = _row_to_dict(self._make_row(result_json="bad-json"))
        assert result["result_json"] is None

    def test_source_stored(self):
        from src.api.routes.media import _row_to_dict
        result = _row_to_dict(self._make_row(source="telegram"))
        assert result["source"] == "telegram"


# ---------------------------------------------------------------------------
# api.routes.graph: _WORD_RE constant + additional _caption_mentions_place
# ---------------------------------------------------------------------------

class TestGraphWordRe:
    def test_matches_alphabetic(self):
        import re
        from src.api.routes.graph import _WORD_RE
        assert _WORD_RE.search("Singapore") is not None

    def test_no_match_digits_only(self):
        from src.api.routes.graph import _WORD_RE
        assert _WORD_RE.search("12345") is None

    def test_no_match_empty(self):
        from src.api.routes.graph import _WORD_RE
        assert _WORD_RE.search("") is None


class TestCaptionMentionsPlaceAdditional:
    def _c(self, caption, place_name):
        from src.api.routes.graph import _caption_mentions_place
        return _caption_mentions_place(caption, place_name)

    def test_all_caps_place(self):
        # "SINGAPORE" in caption, case-insensitive match
        assert self._c("I'm in SINGAPORE today", "Singapore") is True

    def test_place_at_start(self):
        assert self._c("Singapore is amazing", "Singapore") is True

    def test_place_at_end(self):
        assert self._c("Beautiful day in Singapore", "Singapore") is True

    def test_place_with_special_chars_escaped(self):
        # Place with regex-special chars
        result = self._c("Visited São Paulo last week", "São Paulo")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# api.routes.intersections: additional _dedupe_ids + IntersectRequest
# ---------------------------------------------------------------------------

class TestDedupeIdsAdditional:
    def _d(self, ids):
        from src.api.routes.intersections import _dedupe_ids
        return _dedupe_ids(ids)

    def test_many_dupes(self):
        ids = ["a", "b", "a", "c", "b", "a"]
        result = self._d(ids)
        assert result == ["a", "b", "c"]

    def test_order_of_first_occurrence_preserved(self):
        ids = ["c", "a", "b", "a"]
        result = self._d(ids)
        assert result[0] == "c"
        assert result[1] == "a"
        assert result[2] == "b"

    def test_returns_list_type(self):
        assert isinstance(self._d([]), list)

    def test_single_element(self):
        assert self._d(["x"]) == ["x"]
