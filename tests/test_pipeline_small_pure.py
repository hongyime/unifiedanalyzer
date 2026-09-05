"""
QA-lane tests for pure functions in:
- src/pipeline/shared_life_context.py: _is_enabled, _rarity_threshold,
  _base_confidence, _step_confidence, _decode_enrichment, _normalize_item
- src/pipeline/strava_patterns.py: _decode_meta
- src/pipeline/graph_overlap.py: _norm
- src/pipeline/route_similarity.py: _cluster
"""
from __future__ import annotations

import pytest

from src.pipeline.shared_life_context import (
    _base_confidence,
    _decode_enrichment,
    _is_enabled,
    _normalize_item,
    _rarity_threshold,
    _step_confidence,
)
from src.pipeline.strava_patterns import _decode_meta as strava_decode_meta
from src.pipeline.graph_overlap import _norm
from src.pipeline.route_similarity import _cluster


# ---------------------------------------------------------------------------
# shared_life_context._is_enabled
# ---------------------------------------------------------------------------

class TestSharedLifeContextIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("SHARED_LIFE_CONTEXT_ENABLED", raising=False)
        assert _is_enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("SHARED_LIFE_CONTEXT_ENABLED", "0")
        assert _is_enabled() is False

    def test_enabled_when_1(self, monkeypatch):
        monkeypatch.setenv("SHARED_LIFE_CONTEXT_ENABLED", "1")
        assert _is_enabled() is True


# ---------------------------------------------------------------------------
# shared_life_context._rarity_threshold
# ---------------------------------------------------------------------------

class TestRarityThreshold:
    def test_default_is_0_05(self, monkeypatch):
        monkeypatch.delenv("SHARED_LIFE_CONTEXT_RARITY", raising=False)
        assert abs(_rarity_threshold() - 0.05) < 1e-9

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("SHARED_LIFE_CONTEXT_RARITY", "0.10")
        assert abs(_rarity_threshold() - 0.10) < 1e-9

    def test_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SHARED_LIFE_CONTEXT_RARITY", "bad")
        assert abs(_rarity_threshold() - 0.05) < 1e-9


# ---------------------------------------------------------------------------
# shared_life_context._base_confidence / _step_confidence
# ---------------------------------------------------------------------------

class TestConfidenceValues:
    def test_base_confidence_positive(self):
        assert _base_confidence() > 0.0

    def test_step_confidence_positive(self):
        assert _step_confidence() > 0.0

    def test_base_greater_than_step(self):
        # base anchors a single-item match; step is the increment per extra
        assert _base_confidence() >= _step_confidence()


# ---------------------------------------------------------------------------
# shared_life_context._decode_enrichment
# ---------------------------------------------------------------------------

class TestDecodeEnrichment:
    def test_dict_passthrough(self):
        assert _decode_enrichment({"k": "v"}) == {"k": "v"}

    def test_valid_json_string(self):
        assert _decode_enrichment('{"x": 1}') == {"x": 1}

    def test_json_array_returns_empty(self):
        assert _decode_enrichment("[1,2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _decode_enrichment("bad{") == {}

    def test_none_returns_empty(self):
        assert _decode_enrichment(None) == {}

    def test_bytes_parsed(self):
        assert _decode_enrichment(b'{"k":"v"}') == {"k": "v"}


# ---------------------------------------------------------------------------
# shared_life_context._normalize_item
# ---------------------------------------------------------------------------

class TestNormalizeItem:
    def test_lowercases_and_trims(self):
        assert _normalize_item("  Google  ") == "google"

    def test_collapses_whitespace(self):
        assert _normalize_item("National  University") == "national university"

    def test_too_short_returns_none(self):
        assert _normalize_item("ab") is None
        assert _normalize_item("a") is None

    def test_empty_returns_none(self):
        assert _normalize_item("") is None

    def test_none_returns_none(self):
        assert _normalize_item(None) is None

    def test_exactly_3_chars_passes(self):
        assert _normalize_item("NUS") == "nus"


# ---------------------------------------------------------------------------
# strava_patterns._decode_meta
# ---------------------------------------------------------------------------

class TestStravaDecodeMeta:
    def test_dict_passthrough(self):
        assert strava_decode_meta({"a": 1}) == {"a": 1}

    def test_valid_json_string(self):
        assert strava_decode_meta('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert strava_decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert strava_decode_meta(None) == {}


# ---------------------------------------------------------------------------
# graph_overlap._norm
# ---------------------------------------------------------------------------

class TestNorm:
    def test_lowercases_and_strips(self):
        assert _norm("  Alice  ") == "alice"

    def test_none_returns_none(self):
        assert _norm(None) is None

    def test_empty_string_returns_none(self):
        assert _norm("") is None

    def test_already_lower(self):
        assert _norm("alice") == "alice"


# ---------------------------------------------------------------------------
# route_similarity._cluster
# ---------------------------------------------------------------------------

class TestCluster:
    def test_valid_latlng_string(self):
        result = _cluster("1.3521,103.8198")
        assert result is not None
        assert len(result) == 2

    def test_rounds_to_cluster_precision(self):
        # _CLUSTER_PRECISION = 3 → round to 3 dp
        result = _cluster("1.35214,103.81984")
        assert result is not None
        # both components should be rounded
        assert result == (round(1.35214, 3), round(103.81984, 3))

    def test_none_returns_none(self):
        assert _cluster(None) is None

    def test_empty_returns_none(self):
        assert _cluster("") is None

    def test_invalid_returns_none(self):
        assert _cluster("notlatlng") is None

    def test_missing_lng_returns_none(self):
        assert _cluster("1.3521") is None
