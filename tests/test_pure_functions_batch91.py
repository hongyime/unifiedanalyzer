"""
Pure-function tests — batch 91.

Covers:
- pipeline.collector_priority_hints: _hint_type_for_confidence,
  _policy_for_confidence, _normalize_source, _clean,
  _json_value, _row_get, _confidence_from_row
- pipeline.media_common: lookup_entity
- pipeline.text_embedder: text_sha1
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.collector_priority_hints: _hint_type_for_confidence
# ---------------------------------------------------------------------------

class TestHintTypeForConfidence:
    def _h(self, confidence):
        from src.pipeline.collector_priority_hints import _hint_type_for_confidence
        return _hint_type_for_confidence(confidence)

    def test_exactly_one_returns_confirmed(self):
        from src.pipeline.collector_priority_hints import HINT_TYPE_SAME_PERSON_CONFIRMED_100
        assert self._h(1.0) == HINT_TYPE_SAME_PERSON_CONFIRMED_100

    def test_above_one_returns_confirmed(self):
        from src.pipeline.collector_priority_hints import HINT_TYPE_SAME_PERSON_CONFIRMED_100
        assert self._h(1.5) == HINT_TYPE_SAME_PERSON_CONFIRMED_100

    def test_below_one_returns_95_99(self):
        from src.pipeline.collector_priority_hints import HINT_TYPE_SAME_PERSON_95_99
        assert self._h(0.97) == HINT_TYPE_SAME_PERSON_95_99

    def test_zero_returns_95_99(self):
        from src.pipeline.collector_priority_hints import HINT_TYPE_SAME_PERSON_95_99
        assert self._h(0.0) == HINT_TYPE_SAME_PERSON_95_99


# ---------------------------------------------------------------------------
# pipeline.collector_priority_hints: _policy_for_confidence
# ---------------------------------------------------------------------------

class TestPolicyForConfidence:
    def _p(self, confidence):
        from src.pipeline.collector_priority_hints import _policy_for_confidence
        return _policy_for_confidence(confidence)

    def test_one_returns_confirmed_policy(self):
        result = self._p(1.0)
        assert "confirmed" in result

    def test_below_one_returns_probability_policy(self):
        result = self._p(0.97)
        assert "probability" in result or "no_auto_merge" in result


# ---------------------------------------------------------------------------
# pipeline.collector_priority_hints: _normalize_source
# ---------------------------------------------------------------------------

class TestCollectorNormalizeSource:
    def _n(self, value):
        from src.pipeline.collector_priority_hints import _normalize_source
        return _normalize_source(value)

    def test_ig_aliased_to_instagram(self):
        assert self._n("ig") == "instagram"

    def test_twitter_aliased_to_x(self):
        assert self._n("twitter") == "x"

    def test_known_source_passthrough(self):
        assert self._n("telegram") == "telegram"

    def test_none_returns_empty(self):
        assert self._n(None) == ""

    def test_spaces_replaced_with_underscore(self):
        result = self._n("some source")
        assert " " not in result


# ---------------------------------------------------------------------------
# pipeline.collector_priority_hints: _clean
# ---------------------------------------------------------------------------

class TestCollectorClean:
    def _c(self, v):
        from src.pipeline.collector_priority_hints import _clean
        return _clean(v)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_returns_none(self):
        assert self._c("") is None

    def test_strips_whitespace(self):
        assert self._c("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert self._c(42) == "42"


# ---------------------------------------------------------------------------
# pipeline.collector_priority_hints: _json_value
# ---------------------------------------------------------------------------

class TestCollectorJsonValue:
    def _j(self, v):
        from src.pipeline.collector_priority_hints import _json_value
        return _json_value(v)

    def test_none_returns_empty_dict(self):
        assert self._j(None) == {}

    def test_json_string_parsed(self):
        import json
        assert self._j(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_json_returned_as_is(self):
        result = self._j("not json")
        assert result == "not json"

    def test_dict_returned_as_is(self):
        d = {"x": 1}
        assert self._j(d) is d

    def test_int_returned_as_is(self):
        assert self._j(42) == 42


# ---------------------------------------------------------------------------
# pipeline.collector_priority_hints: _row_get
# ---------------------------------------------------------------------------

class TestCollectorRowGet:
    def _g(self, row, key, default=None):
        from src.pipeline.collector_priority_hints import _row_get
        return _row_get(row, key, default)

    def test_dict_found(self):
        assert self._g({"k": 42}, "k") == 42

    def test_dict_missing_returns_default(self):
        assert self._g({}, "k", "fb") == "fb"

    def test_none_returns_default(self):
        assert self._g(None, "k", 0) == 0


# ---------------------------------------------------------------------------
# pipeline.collector_priority_hints: _confidence_from_row
# ---------------------------------------------------------------------------

class TestConfidenceFromRow:
    def _c(self, row):
        from src.pipeline.collector_priority_hints import _confidence_from_row
        return _confidence_from_row(row)

    def test_normal_float(self):
        result = self._c({"confidence": 0.95})
        assert result is not None
        assert abs(result - 0.95) < 1e-9

    def test_missing_confidence_uses_weight(self):
        result = self._c({"weight": 0.8})
        assert result is not None

    def test_percentage_normalized(self):
        result = self._c({"confidence": 97})
        assert result is not None
        assert abs(result - 0.97) < 1e-9

    def test_invalid_returns_none(self):
        assert self._c({"confidence": "bad"}) is None

    def test_none_confidence_and_weight_returns_none(self):
        assert self._c({}) is None


# ---------------------------------------------------------------------------
# pipeline.media_common: lookup_entity
# ---------------------------------------------------------------------------

class TestLookupEntity:
    def _l(self, lookup, source, entity_id):
        from src.pipeline.media_common import lookup_entity
        return lookup_entity(lookup, source, entity_id)

    def test_found(self):
        lookup = {("instagram", "eid-1"): "merged-id"}
        assert self._l(lookup, "instagram", "eid-1") == "merged-id"

    def test_not_found_returns_none(self):
        assert self._l({}, "instagram", "eid-1") is None

    def test_none_entity_id_returns_none(self):
        assert self._l({}, "instagram", None) is None

    def test_different_source_not_found(self):
        lookup = {("telegram", "eid-1"): "merged-id"}
        assert self._l(lookup, "instagram", "eid-1") is None


# ---------------------------------------------------------------------------
# pipeline.text_embedder: text_sha1
# ---------------------------------------------------------------------------

class TestEmbedderTextSha1:
    def _s(self, text):
        from src.pipeline.text_embedder import text_sha1
        return text_sha1(text)

    def test_returns_40_hex_chars(self):
        result = self._s("hello world")
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._s("test") == self._s("test")

    def test_different_inputs_different_hashes(self):
        assert self._s("foo") != self._s("bar")

    def test_empty_string(self):
        result = self._s("")
        assert len(result) == 40
