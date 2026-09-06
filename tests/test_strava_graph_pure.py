"""
QA-lane tests for remaining small pure functions:
- src/pipeline/strava_patterns.py: _is_default_name
- src/pipeline/graph_analytics.py: _decode_meta (same pattern as others)
"""
from __future__ import annotations

import pytest

from src.pipeline.strava_patterns import _is_default_name
from src.pipeline.graph_analytics import _decode_meta as graph_decode_meta


class TestIsDefaultName:
    def test_morning_run_is_default(self):
        assert _is_default_name("Morning Run") is True

    def test_evening_ride_is_default(self):
        assert _is_default_name("Evening Ride") is True

    def test_custom_name_is_not_default(self):
        assert _is_default_name("Sunday long run with friends") is False

    def test_case_insensitive(self):
        assert _is_default_name("MORNING RUN") is True

    def test_strips_whitespace(self):
        assert _is_default_name("  morning walk  ") is True

    def test_empty_is_not_default(self):
        assert _is_default_name("") is False


class TestGraphAnalyticsDecodeMeta:
    def test_dict_passthrough(self):
        assert graph_decode_meta({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert graph_decode_meta('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert graph_decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert graph_decode_meta(None) == {}
