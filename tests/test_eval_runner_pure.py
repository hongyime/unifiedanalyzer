"""
QA-lane tests for pure helper functions in src/eval/runner.py:
- _json_obj: dict/str/invalid coercion
- _factory_items: synthetic item generation for sentiment/search/alerts factories
- materialize_seed_items: combines inline items + factory items
"""
from __future__ import annotations

import pytest

from src.eval.runner import _factory_items, _json_obj, materialize_seed_items


# ---------------------------------------------------------------------------
# _json_obj
# ---------------------------------------------------------------------------

class TestJsonObj:
    def test_dict_passthrough(self):
        assert _json_obj({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert _json_obj('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert _json_obj("[1, 2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _json_obj("bad{") == {}

    def test_none_returns_empty(self):
        assert _json_obj(None) == {}

    def test_integer_returns_empty(self):
        assert _json_obj(42) == {}


# ---------------------------------------------------------------------------
# _factory_items
# ---------------------------------------------------------------------------

class TestFactoryItems:
    def test_no_factory_returns_empty(self):
        assert _factory_items({}) == []

    def test_unknown_factory_name_returns_empty(self):
        assert _factory_items({"factory": {"name": "unknown_factory"}}) == []

    def test_sentiment_factory_generates_items(self):
        item_set = {"factory": {"name": "sentiment_examples", "count": 20}}
        result = _factory_items(item_set)
        assert len(result) == 20

    def test_sentiment_item_structure(self):
        item_set = {"factory": {"name": "sentiment_examples", "count": 5}}
        result = _factory_items(item_set)
        for item in result:
            assert "input_json" in item
            assert "expected_json" in item
            assert "source_ref" in item
            assert "label" in item["expected_json"]

    def test_sentiment_labels_are_valid(self):
        item_set = {"factory": {"name": "sentiment_examples", "count": 100}}
        result = _factory_items(item_set)
        valid_labels = {"positive", "negative", "neutral", "unsupported"}
        for item in result:
            assert item["expected_json"]["label"] in valid_labels

    def test_search_factory_generates_items(self):
        item_set = {"factory": {"name": "search_queries", "count": 10}}
        result = _factory_items(item_set)
        assert len(result) == 10

    def test_search_item_structure(self):
        item_set = {"factory": {"name": "search_queries", "count": 3}}
        result = _factory_items(item_set)
        for item in result:
            assert "query" in item["input_json"]
            assert "ranked_event_ids" in item["input_json"]
            assert "event_ids" in item["expected_json"]

    def test_alert_fixtures_generates_items(self):
        item_set = {"factory": {"name": "alert_fixtures", "count": 5}}
        result = _factory_items(item_set)
        assert len(result) == 5

    def test_alert_item_structure(self):
        item_set = {"factory": {"name": "alert_fixtures", "count": 3}}
        result = _factory_items(item_set)
        for item in result:
            assert "fingerprint" in item["input_json"]
            assert item["expected_json"]["label"] == "fired"

    def test_default_count_used_when_absent(self):
        # sentiment default is 100
        item_set = {"factory": {"name": "sentiment_examples"}}
        result = _factory_items(item_set)
        assert len(result) == 100


# ---------------------------------------------------------------------------
# materialize_seed_items
# ---------------------------------------------------------------------------

class TestMaterializeSeedItems:
    def test_inline_items_returned(self):
        item_set = {"items": [{"a": 1}, {"b": 2}]}
        result = materialize_seed_items(item_set)
        assert len(result) == 2

    def test_factory_items_appended(self):
        item_set = {
            "items": [{"a": 1}],
            "factory": {"name": "alert_fixtures", "count": 3},
        }
        result = materialize_seed_items(item_set)
        assert len(result) == 4  # 1 inline + 3 factory

    def test_empty_item_set_returns_empty(self):
        assert materialize_seed_items({}) == []

    def test_none_items_treated_as_empty(self):
        item_set = {"items": None, "factory": {"name": "alert_fixtures", "count": 2}}
        result = materialize_seed_items(item_set)
        assert len(result) == 2
