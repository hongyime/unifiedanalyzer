"""
QA-lane tests for pure helper functions in:
- src/api/routes/graph.py: _decode_polyline, _parse_latlng, _caption_mentions_place,
  _as_latlng_list, _downsample, _geo_event
- src/api/routes/triage.py: _decode
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# graph.py geo helpers
# ---------------------------------------------------------------------------

from src.api.routes.graph import (
    _as_latlng_list,
    _caption_mentions_place,
    _decode_polyline,
    _downsample,
    _geo_event,
    _parse_latlng,
)


class TestDecodePolyline:
    def test_empty_string_returns_empty(self):
        assert _decode_polyline("") == []

    def test_known_encoded_polyline(self):
        # "_p~iF~ps|U_ulLnnqC_mqNvxq`@" encodes [(38.5,-120.2),(40.7,-120.95),(43.252,-126.453)]
        result = _decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        assert len(result) == 3
        assert abs(result[0][0] - 38.5) < 0.01
        assert abs(result[0][1] - (-120.2)) < 0.01

    def test_returns_list_of_latlng_pairs(self):
        result = _decode_polyline("_p~iF~ps|U")
        assert len(result) == 1
        assert len(result[0]) == 2


class TestParseLatLng:
    def test_comma_separated_string(self):
        result = _parse_latlng("1.35,103.82")
        assert result == [1.35, 103.82]

    def test_bracketed_string(self):
        result = _parse_latlng("[1.35, 103.82]")
        assert result is not None
        assert abs(result[0] - 1.35) < 0.001

    def test_none_returns_none(self):
        assert _parse_latlng(None) is None

    def test_empty_returns_none(self):
        assert _parse_latlng("") is None

    def test_invalid_returns_none(self):
        assert _parse_latlng("notlatlng") is None

    def test_single_value_returns_none(self):
        assert _parse_latlng("1.35") is None


class TestCaptionMentionsPlace:
    def test_place_in_caption(self):
        assert _caption_mentions_place("Visited Marina Bay Sands today", "Marina Bay") is True

    def test_place_not_in_caption(self):
        assert _caption_mentions_place("Great day at the beach", "Marina Bay") is False

    def test_none_caption_returns_false(self):
        assert _caption_mentions_place(None, "Marina Bay") is False

    def test_none_place_returns_false(self):
        assert _caption_mentions_place("Great day", None) is False

    def test_short_place_name_returns_false(self):
        # place_name < 4 chars → always False
        assert _caption_mentions_place("I am at SG", "SG") is False

    def test_place_as_substring_of_word_not_matched(self):
        # "Singapore" contains "Sin" but word-boundary check prevents partial match
        assert _caption_mentions_place("Singapore is great", "Sin") is False


class TestAsLatlngList:
    def test_valid_list_of_pairs(self):
        result = _as_latlng_list([[1.3, 103.8], [1.4, 103.9]])
        assert result == [[1.3, 103.8], [1.4, 103.9]]

    def test_json_string_parsed(self):
        import json
        raw = json.dumps([[1.3, 103.8], [1.4, 103.9]])
        result = _as_latlng_list(raw)
        assert len(result) == 2

    def test_none_returns_empty(self):
        assert _as_latlng_list(None) == []

    def test_invalid_json_returns_empty(self):
        assert _as_latlng_list("bad{json") == []

    def test_non_list_json_returns_empty(self):
        assert _as_latlng_list('{"key": "val"}') == []

    def test_skips_string_entries(self):
        # string entries like "bad" don't have __len__ >= 2 so they're skipped
        result = _as_latlng_list([[1.3, 103.8], "bad", [1.4, 103.9]])
        assert len(result) == 2


class TestDownsample:
    def test_small_list_unchanged(self):
        pts = [[i, i] for i in range(10)]
        assert _downsample(pts, target=20) == pts

    def test_large_list_downsampled(self):
        pts = [[i, i] for i in range(1000)]
        result = _downsample(pts, target=120)
        assert len(result) <= 121  # 120 + last point

    def test_empty_list(self):
        assert _downsample([], target=120) == []

    def test_last_point_included(self):
        pts = [[i, i] for i in range(500)]
        result = _downsample(pts, target=10)
        assert result[-1] == pts[-1]


class TestGeoEvent:
    def test_point_event_has_lat_lng(self):
        item = {"lat": 1.35, "lng": 103.82, "kind": "point", "occurred_at": "2024-01-01"}
        result = _geo_event(item, "point")
        assert result["kind"] == "point"
        assert result["lat"] == 1.35
        assert result["lng"] == 103.82

    def test_route_event_uses_first_point(self):
        item = {"points": [[1.35, 103.82], [1.36, 103.83]], "occurred_at": "2024-01-01"}
        result = _geo_event(item, "route")
        assert result["kind"] == "route"
        assert result["lat"] == 1.35
        assert result["end_lat"] == 1.36

    def test_empty_item_has_none_coords(self):
        result = _geo_event({}, "point")
        assert result["lat"] is None
        assert result["lng"] is None

    def test_label_from_label_field(self):
        item = {"label": "Morning run"}
        result = _geo_event(item, "route")
        assert result["label"] == "Morning run"

    def test_label_falls_back_to_name(self):
        item = {"name": "Evening walk"}
        result = _geo_event(item, "route")
        assert result["label"] == "Evening walk"


# ---------------------------------------------------------------------------
# triage._decode
# ---------------------------------------------------------------------------

from src.api.routes.triage import _decode as triage_decode


class TestTriageDecode:
    def test_dict_passthrough(self):
        assert triage_decode({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert triage_decode('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert triage_decode("[1, 2]") == {}

    def test_invalid_json_returns_empty(self):
        assert triage_decode("bad{") == {}

    def test_none_returns_empty(self):
        assert triage_decode(None) == {}

    def test_integer_returns_empty(self):
        assert triage_decode(42) == {}
