"""
QA-lane tests for pure functions in src/pipeline/relationship_intelligence.py.

Covers:
- _sorted_pair: deterministic pair ordering
- _jsonb_param: JSON serialization with str fallback
- _decode_meta: dict/str/invalid coercion
- _jaccard: set similarity
- _scaled_similarity: numeric closeness with floor
- _cosine_sim: dict-based cosine similarity
"""
from __future__ import annotations

import json

import pytest

from src.pipeline.relationship_intelligence import (
    _cosine_sim,
    _decode_meta,
    _jaccard,
    _jsonb_param,
    _scaled_similarity,
    _sorted_pair,
)


# ---------------------------------------------------------------------------
# _sorted_pair
# ---------------------------------------------------------------------------

class TestSortedPair:
    def test_smaller_first(self):
        assert _sorted_pair("b", "a") == ("a", "b")
        assert _sorted_pair("a", "b") == ("a", "b")

    def test_equal_inputs(self):
        assert _sorted_pair("x", "x") == ("x", "x")

    def test_uuid_like_strings(self):
        a = "00000000-0000-0000-0000-000000000001"
        b = "00000000-0000-0000-0000-000000000002"
        assert _sorted_pair(a, b) == (a, b)
        assert _sorted_pair(b, a) == (a, b)


# ---------------------------------------------------------------------------
# _jsonb_param
# ---------------------------------------------------------------------------

class TestJsonbParam:
    def test_dict_serialized(self):
        result = json.loads(_jsonb_param({"a": 1}))
        assert result == {"a": 1}

    def test_non_serializable_uses_str(self):
        from datetime import datetime, timezone
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = _jsonb_param({"ts": ts})
        parsed = json.loads(result)
        assert "ts" in parsed

    def test_empty_dict(self):
        assert _jsonb_param({}) == "{}"

    def test_list_serialized(self):
        result = json.loads(_jsonb_param([1, 2, 3]))
        assert result == [1, 2, 3]


# ---------------------------------------------------------------------------
# _decode_meta
# ---------------------------------------------------------------------------

class TestDecodeMeta:
    def test_dict_passthrough(self):
        d = {"k": "v"}
        assert _decode_meta(d) == d

    def test_valid_json_string_parsed(self):
        assert _decode_meta('{"x": 1}') == {"x": 1}

    def test_json_array_returns_empty(self):
        assert _decode_meta("[1,2]") == {}

    def test_invalid_json_returns_empty(self):
        assert _decode_meta("not{json") == {}

    def test_none_returns_empty(self):
        assert _decode_meta(None) == {}

    def test_integer_returns_empty(self):
        assert _decode_meta(42) == {}


# ---------------------------------------------------------------------------
# _jaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def test_identical_sets_give_1(self):
        a = {"a", "b", "c"}
        assert abs(_jaccard(a, a) - 1.0) < 1e-9

    def test_disjoint_sets_give_0(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_empty_set_gives_0(self):
        assert _jaccard(set(), {"a"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0

    def test_partial_overlap(self):
        # {a,b} ∩ {b,c} = {b}, union = {a,b,c}, J = 1/3
        sim = _jaccard({"a", "b"}, {"b", "c"})
        assert abs(sim - 1/3) < 1e-9

    def test_symmetry(self):
        a, b = {"x", "y"}, {"y", "z"}
        assert _jaccard(a, b) == _jaccard(b, a)


# ---------------------------------------------------------------------------
# _scaled_similarity
# ---------------------------------------------------------------------------

class TestScaledSimilarity:
    def test_equal_values_give_1(self):
        assert _scaled_similarity(5.0, 5.0) == 1.0

    def test_very_different_values_give_low_score(self):
        sim = _scaled_similarity(100.0, 1.0)
        assert sim is not None
        assert sim < 0.5

    def test_invalid_input_returns_none(self):
        assert _scaled_similarity("bad", 1.0) is None
        assert _scaled_similarity(None, 1.0) is None

    def test_zero_denom_gives_1(self):
        # both zero → denom = floor = 1.0, diff = 0 → sim = 1
        assert _scaled_similarity(0.0, 0.0) == 1.0

    def test_floor_prevents_division_by_zero(self):
        # Small values, floor=1.0 protects denominator
        result = _scaled_similarity(0.1, 0.2, floor=1.0)
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_result_never_negative(self):
        sim = _scaled_similarity(1.0, 1000.0)
        assert sim is not None
        assert sim >= 0.0


# ---------------------------------------------------------------------------
# _cosine_sim
# ---------------------------------------------------------------------------

class TestCosineSim:
    def test_identical_dicts_give_1(self):
        a = {"hello": 3, "world": 2}
        assert abs(_cosine_sim(a, a) - 1.0) < 1e-9

    def test_orthogonal_dicts_give_0(self):
        assert _cosine_sim({"hello": 1}, {"world": 1}) == 0.0

    def test_empty_dicts_give_0(self):
        assert _cosine_sim({}, {"hello": 1}) == 0.0
        assert _cosine_sim({"hello": 1}, {}) == 0.0

    def test_partial_overlap_between_0_and_1(self):
        a = {"hello": 2, "world": 1}
        b = {"hello": 2, "foo": 3}
        sim = _cosine_sim(a, b)
        assert 0.0 < sim < 1.0

    def test_symmetry(self):
        a = {"a": 3, "b": 2}
        b = {"a": 1, "c": 4}
        assert abs(_cosine_sim(a, b) - _cosine_sim(b, a)) < 1e-9
