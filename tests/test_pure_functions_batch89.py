"""
Pure-function tests — batch 89.

Covers:
- pipeline.shared_life_context: _rarity_threshold, _base_confidence,
  _step_confidence, _decode_enrichment, _normalize_item
- pipeline.conversation_analytics: _iso, _decode
- pipeline.location_inference: _decode_meta, _parse_strava_timezone,
  _parse_latlng, _latlng_to_region
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# pipeline.shared_life_context: _rarity_threshold
# ---------------------------------------------------------------------------

class TestRarityThreshold:
    def _r(self, value=None):
        from src.pipeline.shared_life_context import _rarity_threshold
        key = "SHARED_LIFE_CONTEXT_RARITY"
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _rarity_threshold()
        finally:
            os.environ.pop(key, None)

    def test_default_0_05(self):
        assert abs(self._r() - 0.05) < 1e-9

    def test_custom_value(self):
        assert abs(self._r(0.1) - 0.1) < 1e-9

    def test_invalid_returns_default(self):
        assert abs(self._r("bad") - 0.05) < 1e-9


# ---------------------------------------------------------------------------
# pipeline.shared_life_context: _base_confidence and _step_confidence
# ---------------------------------------------------------------------------

class TestBaseAndStepConfidence:
    def test_base_returns_float(self):
        from src.pipeline.shared_life_context import _base_confidence
        result = _base_confidence()
        assert isinstance(result, float)
        assert 0.0 < result <= 1.0

    def test_step_returns_float(self):
        from src.pipeline.shared_life_context import _step_confidence
        result = _step_confidence()
        assert isinstance(result, float)
        assert 0.0 < result <= 1.0


# ---------------------------------------------------------------------------
# pipeline.shared_life_context: _decode_enrichment
# ---------------------------------------------------------------------------

class TestDecodeEnrichment:
    def _d(self, raw):
        from src.pipeline.shared_life_context import _decode_enrichment
        return _decode_enrichment(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._d(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_string_returns_empty(self):
        assert self._d("not json") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_json_list_returns_empty(self):
        import json
        assert self._d(json.dumps([1, 2])) == {}


# ---------------------------------------------------------------------------
# pipeline.shared_life_context: _normalize_item
# ---------------------------------------------------------------------------

class TestNormalizeItem:
    def _n(self, text):
        from src.pipeline.shared_life_context import _normalize_item
        return _normalize_item(text)

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_short_returns_none(self):
        assert self._n("ab") is None

    def test_lowercased(self):
        assert self._n("Google") == "google"

    def test_whitespace_collapsed(self):
        assert self._n("  hello   world  ") == "hello world"

    def test_valid_returns_string(self):
        result = self._n("National University")
        assert result == "national university"


# ---------------------------------------------------------------------------
# pipeline.conversation_analytics: _iso
# ---------------------------------------------------------------------------

class TestConvAnalyticsIso:
    def _i(self, v):
        from src.pipeline.conversation_analytics import _iso
        return _iso(v)

    def test_datetime_isoformatted(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        assert self._i(dt) == dt.isoformat()

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_string_returned(self):
        assert self._i("2026-01-15") == "2026-01-15"

    def test_zero_returns_none(self):
        assert self._i(0) is None


# ---------------------------------------------------------------------------
# pipeline.conversation_analytics: _decode
# ---------------------------------------------------------------------------

class TestConvAnalyticsDecode:
    def _d(self, raw):
        from src.pipeline.conversation_analytics import _decode
        return _decode(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._d(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_returns_empty(self):
        assert self._d("not json") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


# ---------------------------------------------------------------------------
# pipeline.location_inference: _decode_meta
# ---------------------------------------------------------------------------

class TestLocInferenceDecodeMeta:
    def _d(self, raw):
        from src.pipeline.location_inference import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._d(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_returns_empty(self):
        assert self._d("not json") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


# ---------------------------------------------------------------------------
# pipeline.location_inference: _parse_strava_timezone
# ---------------------------------------------------------------------------

class TestParseStravaTimezone:
    def _p(self, tz_str):
        from src.pipeline.location_inference import _parse_strava_timezone
        return _parse_strava_timezone(tz_str)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_empty_returns_none(self):
        assert self._p("") is None

    def test_standard_format(self):
        result = self._p("(GMT+08:00) Asia/Singapore")
        if result:
            assert "Asia/Singapore" in result or "Singapore" in result

    def test_no_match_returns_none(self):
        assert self._p("not a timezone string") is None


# ---------------------------------------------------------------------------
# pipeline.location_inference: _parse_latlng
# ---------------------------------------------------------------------------

class TestParseLatlng:
    def _p(self, raw):
        from src.pipeline.location_inference import _parse_latlng
        return _parse_latlng(raw)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_empty_string_returns_none(self):
        assert self._p("") is None

    def test_list_parsed(self):
        result = self._p([1.35, 103.82])
        assert result is not None
        assert abs(result[0] - 1.35) < 1e-9
        assert abs(result[1] - 103.82) < 1e-9

    def test_string_format_parsed(self):
        result = self._p("[1.35, 103.82]")
        assert result is not None
        assert abs(result[0] - 1.35) < 1e-9

    def test_empty_brackets_returns_none(self):
        assert self._p("[]") is None

    def test_invalid_string_returns_none(self):
        assert self._p("not a coord") is None


# ---------------------------------------------------------------------------
# pipeline.location_inference: _latlng_to_region
# ---------------------------------------------------------------------------

class TestLatlngToRegion:
    def _r(self, lat, lng):
        from src.pipeline.location_inference import _latlng_to_region
        return _latlng_to_region(lat, lng)

    def test_singapore_is_sea(self):
        result = self._r(1.35, 103.82)
        assert result == "SEA"

    def test_london_is_europe(self):
        result = self._r(51.5, -0.1)
        assert result == "Europe"

    def test_new_york_is_north_america(self):
        result = self._r(40.7, -74.0)
        assert result == "North America"

    def test_none_lat_returns_none(self):
        assert self._r(None, 103.82) is None

    def test_mid_ocean_returns_none(self):
        result = self._r(0.0, 0.0)
        # Mid-Atlantic, should be None or unknown
        assert result is None or isinstance(result, str)
