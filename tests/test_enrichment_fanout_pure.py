"""
QA-lane tests for pure functions in:
- src/pipeline/entity_enrichment.py: _is_enabled, _max_chars, _batch_size,
  _model_name, _sort_bucket
- src/pipeline/handle_fanout.py: _is_enabled
- src/pipeline/graph_analytics.py: _decode_meta
"""
from __future__ import annotations

import pytest

from src.pipeline.entity_enrichment import (
    _batch_size,
    _is_enabled as enrichment_is_enabled,
    _max_chars,
    _model_name,
    _sort_bucket,
)
from src.pipeline.handle_fanout import _is_enabled as fanout_is_enabled
from src.pipeline.graph_analytics import _decode_meta


# ---------------------------------------------------------------------------
# entity_enrichment._is_enabled
# ---------------------------------------------------------------------------

class TestEnrichmentIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("ENTITY_ENRICHMENT_ENABLED", raising=False)
        assert enrichment_is_enabled() is False

    def test_enabled_via_1(self, monkeypatch):
        monkeypatch.setenv("ENTITY_ENRICHMENT_ENABLED", "1")
        assert enrichment_is_enabled() is True

    def test_enabled_via_true(self, monkeypatch):
        monkeypatch.setenv("ENTITY_ENRICHMENT_ENABLED", "true")
        assert enrichment_is_enabled() is True

    def test_enabled_via_yes(self, monkeypatch):
        monkeypatch.setenv("ENTITY_ENRICHMENT_ENABLED", "yes")
        assert enrichment_is_enabled() is True

    def test_disabled_via_0(self, monkeypatch):
        monkeypatch.setenv("ENTITY_ENRICHMENT_ENABLED", "0")
        assert enrichment_is_enabled() is False


# ---------------------------------------------------------------------------
# entity_enrichment._max_chars / _batch_size / _model_name
# ---------------------------------------------------------------------------

class TestEnrichmentConfig:
    def test_max_chars_default(self, monkeypatch):
        monkeypatch.delenv("NER_MAX_CHARS_PER_ENTITY", raising=False)
        assert _max_chars() == 20000

    def test_max_chars_custom(self, monkeypatch):
        monkeypatch.setenv("NER_MAX_CHARS_PER_ENTITY", "5000")
        assert _max_chars() == 5000

    def test_batch_size_default(self, monkeypatch):
        monkeypatch.delenv("NER_ENTITY_BATCH_PER_RUN", raising=False)
        assert _batch_size() == 100

    def test_batch_size_custom(self, monkeypatch):
        monkeypatch.setenv("NER_ENTITY_BATCH_PER_RUN", "50")
        assert _batch_size() == 50

    def test_model_name_default(self, monkeypatch):
        monkeypatch.delenv("NER_MODEL", raising=False)
        assert _model_name() == "en_core_web_trf"

    def test_model_name_custom(self, monkeypatch):
        monkeypatch.setenv("NER_MODEL", "en_core_web_sm")
        assert _model_name() == "en_core_web_sm"


# ---------------------------------------------------------------------------
# entity_enrichment._sort_bucket
# ---------------------------------------------------------------------------

class TestSortBucket:
    def test_sorted_by_frequency_descending(self):
        counts = {"google": 5, "apple": 10, "microsoft": 3}
        result = _sort_bucket(counts)
        texts = [r["text"] for r in result]
        assert texts[0] == "apple"
        assert texts[1] == "google"
        assert texts[2] == "microsoft"

    def test_ties_broken_alphabetically(self):
        counts = {"zebra": 5, "apple": 5}
        result = _sort_bucket(counts)
        texts = [r["text"] for r in result]
        assert texts[0] == "apple"

    def test_capped_at_top_n(self):
        counts = {f"item{i}": i for i in range(100)}
        result = _sort_bucket(counts, top_n=10)
        assert len(result) == 10

    def test_result_has_text_and_count_keys(self):
        result = _sort_bucket({"google": 3})
        assert result[0]["text"] == "google"
        assert result[0]["count"] == 3

    def test_empty_dict_returns_empty(self):
        assert _sort_bucket({}) == []

    def test_single_item(self):
        result = _sort_bucket({"nus": 7})
        assert len(result) == 1
        assert result[0] == {"text": "nus", "count": 7}


# ---------------------------------------------------------------------------
# handle_fanout._is_enabled
# ---------------------------------------------------------------------------

class TestHandleFanoutIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HANDLE_FANOUT_ENABLED", raising=False)
        assert fanout_is_enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("HANDLE_FANOUT_ENABLED", "0")
        assert fanout_is_enabled() is False

    def test_enabled_when_1(self, monkeypatch):
        monkeypatch.setenv("HANDLE_FANOUT_ENABLED", "1")
        assert fanout_is_enabled() is True


# ---------------------------------------------------------------------------
# graph_analytics._decode_meta
# ---------------------------------------------------------------------------

class TestGraphAnalyticsDecodeMeta:
    def test_dict_passthrough(self):
        assert _decode_meta({"a": 1}) == {"a": 1}

    def test_valid_json_string(self):
        assert _decode_meta('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert _decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert _decode_meta(None) == {}

    def test_json_array_returns_empty(self):
        assert _decode_meta("[1,2]") == {}
