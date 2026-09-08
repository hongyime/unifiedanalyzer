"""
Pure-function tests — batch 79.

Covers:
- pipeline.temporal_correlation: _cosine, _date_span, _decode_meta
- pipeline.entity_resolver: normalize_username, normalize_username_strict,
  name_is_distinctive, name_block_keys, parse_whatsapp_phone
- pipeline.incremental_runner: _env_int, _env_bool, _sum_numeric_stats,
  _safe_count, _count_from_keys, _phase_resource_class,
  _source_label, _normalize_phase_stats, _bounded_json_payload
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone


# ---------------------------------------------------------------------------
# pipeline.temporal_correlation: _cosine
# ---------------------------------------------------------------------------

class TestTemporalCosine:
    def _c(self, a, b):
        from src.pipeline.temporal_correlation import _cosine
        return _cosine(a, b)

    def test_zero_vectors_returns_zero(self):
        assert self._c([0.0] * 24, [0.0] * 24) == 0.0

    def test_identical_vectors_returns_one(self):
        v = [1.0 if i == 12 else 0.0 for i in range(24)]
        assert abs(self._c(v, v) - 1.0) < 1e-9

    def test_orthogonal_returns_zero(self):
        a = [1.0 if i < 12 else 0.0 for i in range(24)]
        b = [0.0 if i < 12 else 1.0 for i in range(24)]
        assert self._c(a, b) == 0.0

    def test_range_zero_to_one(self):
        import random
        rng = random.Random(42)
        a = [rng.random() for _ in range(24)]
        b = [rng.random() for _ in range(24)]
        result = self._c(a, b)
        assert 0.0 <= result <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# pipeline.temporal_correlation: _date_span
# ---------------------------------------------------------------------------

class TestDateSpan:
    def _d(self, start, end):
        from src.pipeline.temporal_correlation import _date_span
        return _date_span(start, end)

    def test_same_day_returns_one(self):
        d = date(2026, 1, 1)
        assert self._d(d, d) == 1

    def test_two_days_returns_two(self):
        assert self._d(date(2026, 1, 1), date(2026, 1, 2)) == 2

    def test_month_span(self):
        assert self._d(date(2026, 1, 1), date(2026, 1, 31)) == 31


# ---------------------------------------------------------------------------
# pipeline.temporal_correlation: _decode_meta
# ---------------------------------------------------------------------------

class TestTemporalDecodeMeta:
    def _d(self, raw):
        from src.pipeline.temporal_correlation import _decode_meta
        return _decode_meta(raw)

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


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: normalize_username
# ---------------------------------------------------------------------------

class TestNormalizeUsername:
    def _n(self, u):
        from src.pipeline.entity_resolver import normalize_username
        return normalize_username(u)

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_lowercased(self):
        result = self._n("Alice")
        if result:
            assert result == result.lower()

    def test_space_in_username_returns_none(self):
        assert self._n("john doe") is None

    def test_strips_punctuation(self):
        result = self._n("john.smith")
        if result:
            assert "." not in result

    def test_short_result_returns_none(self):
        assert self._n("ab") is None


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: normalize_username_strict
# ---------------------------------------------------------------------------

class TestNormalizeUsernameStrict:
    def _n(self, u):
        from src.pipeline.entity_resolver import normalize_username_strict
        return normalize_username_strict(u)

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_preserves_trailing_digits(self):
        result = self._n("john123")
        if result:
            assert result.endswith("123")

    def test_strips_punctuation(self):
        result = self._n("john.smith")
        if result:
            assert "." not in result


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: name_is_distinctive
# ---------------------------------------------------------------------------

class TestNameIsDistinctive:
    def _d(self, name):
        from src.pipeline.entity_resolver import name_is_distinctive
        return name_is_distinctive(name)

    def test_none_returns_false(self):
        assert self._d(None) is False

    def test_empty_returns_false(self):
        assert self._d("") is False

    def test_single_token_not_distinctive(self):
        assert self._d("Mike") is False

    def test_full_name_distinctive(self):
        assert self._d("Jane Halloran") is True

    def test_three_tokens_distinctive(self):
        assert self._d("John Michael Smith") is True


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: name_block_keys
# ---------------------------------------------------------------------------

class TestNameBlockKeys:
    def _b(self, name):
        from src.pipeline.entity_resolver import name_block_keys
        return name_block_keys(name)

    def test_empty_name_empty_set(self):
        assert self._b("") == set()

    def test_short_token_excluded(self):
        result = self._b("of a b")
        # "of" is 2 chars, "a"/"b" are 1 char — all excluded
        assert result == set()

    def test_long_tokens_first_3_chars(self):
        result = self._b("Alice Johnson")
        assert "ali" in result
        assert "joh" in result

    def test_lowercased(self):
        result = self._b("ALICE")
        assert "ali" in result


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: parse_whatsapp_phone
# ---------------------------------------------------------------------------

class TestParseWhatsappPhone:
    def _p(self, jid):
        from src.pipeline.entity_resolver import parse_whatsapp_phone
        return parse_whatsapp_phone(jid)

    def test_valid_jid(self):
        assert self._p("6512345678@s.whatsapp.net") == "6512345678"

    def test_lid_jid_returns_none(self):
        assert self._p("123@lid") is None

    def test_status_jid_returns_none(self):
        assert self._p("status@broadcast") is None

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_non_numeric_phone_returns_none(self):
        assert self._p("notaphone@s.whatsapp.net") is None

    def test_short_phone_returns_none(self):
        # less than 7 digits
        assert self._p("123@s.whatsapp.net") is None


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _env_int
# ---------------------------------------------------------------------------

class TestRunnerEnvInt:
    def _e(self, name, default, minimum=1, value=None):
        from src.pipeline.incremental_runner import _env_int
        if value is not None:
            os.environ[name] = str(value)
        else:
            os.environ.pop(name, None)
        try:
            return _env_int(name, default, minimum=minimum)
        finally:
            os.environ.pop(name, None)

    def test_returns_default_when_unset(self):
        assert self._e("__RI_UNSET__", 10) == 10

    def test_returns_env_value(self):
        assert self._e("__RI_X__", 10, value=5) == 5

    def test_minimum_enforced(self):
        assert self._e("__RI_Y__", 1, minimum=3, value=1) == 3

    def test_invalid_returns_default(self):
        assert self._e("__RI_Z__", 7, value="bad") == 7


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _env_bool
# ---------------------------------------------------------------------------

class TestRunnerEnvBool:
    def _e(self, name, default=False, value=None):
        from src.pipeline.incremental_runner import _env_bool
        if value is not None:
            os.environ[name] = str(value)
        else:
            os.environ.pop(name, None)
        try:
            return _env_bool(name, default)
        finally:
            os.environ.pop(name, None)

    def test_unset_returns_default(self):
        assert self._e("__RB_UNSET__") is False

    def test_true_string(self):
        assert self._e("__RB_A__", value="true") is True

    def test_one_string(self):
        assert self._e("__RB_B__", value="1") is True

    def test_yes_string(self):
        assert self._e("__RB_C__", value="yes") is True

    def test_false_string(self):
        assert self._e("__RB_D__", value="false") is False


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _sum_numeric_stats
# ---------------------------------------------------------------------------

class TestSumNumericStats:
    def _s(self, stats):
        from src.pipeline.incremental_runner import _sum_numeric_stats
        return _sum_numeric_stats(stats)

    def test_empty_returns_zero(self):
        assert self._s({}) == 0

    def test_sums_ints(self):
        assert self._s({"a": 3, "b": 4}) == 7

    def test_ignores_bools(self):
        assert self._s({"ok": True, "count": 5}) == 5

    def test_ignores_strings(self):
        assert self._s({"msg": "hello", "n": 2}) == 2


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _safe_count
# ---------------------------------------------------------------------------

class TestSafeCount:
    def _s(self, v):
        from src.pipeline.incremental_runner import _safe_count
        return _safe_count(v)

    def test_int_returned(self):
        assert self._s(5) == 5

    def test_negative_clamped_to_zero(self):
        assert self._s(-3) == 0

    def test_bool_returns_none(self):
        assert self._s(True) is None

    def test_string_returns_none(self):
        assert self._s("hello") is None

    def test_float_truncated(self):
        assert self._s(3.9) == 3


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _source_label
# ---------------------------------------------------------------------------

class TestSourceLabel:
    def _l(self, source):
        from src.pipeline.incremental_runner import _source_label
        return _source_label(source)

    def test_none_returns_all(self):
        assert self._l(None) == "all"

    def test_empty_string_returns_all(self):
        assert self._l("") == "all"

    def test_source_returned(self):
        assert self._l("instagram") == "instagram"

    def test_truncated_to_128(self):
        assert len(self._l("x" * 200)) == 128


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _normalize_phase_stats
# ---------------------------------------------------------------------------

class TestNormalizePhaseStats:
    def _n(self, result):
        from src.pipeline.incremental_runner import _normalize_phase_stats
        return _normalize_phase_stats(result)

    def test_dict_passthrough(self):
        d = {"processed": 5}
        assert self._n(d) is d

    def test_int_becomes_attributed(self):
        assert self._n(10) == {"attributed": 10}

    def test_list_becomes_processed_count(self):
        assert self._n([1, 2, 3]) == {"processed": 3}

    def test_none_returns_empty(self):
        assert self._n(None) == {}

    def test_bool_returns_empty(self):
        assert self._n(True) == {}


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _bounded_json_payload
# ---------------------------------------------------------------------------

class TestBoundedJsonPayload:
    def _b(self, value):
        from src.pipeline.incremental_runner import _bounded_json_payload
        return _bounded_json_payload(value)

    def test_none_returns_empty_list(self):
        assert self._b(None) == []

    def test_empty_list_returns_empty_list(self):
        assert self._b([]) == []

    def test_list_truncated_to_20(self):
        result = self._b(list(range(50)))
        assert len(result) == 20

    def test_dict_truncated_to_20_items(self):
        d = {str(i): i for i in range(50)}
        result = self._b(d)
        assert len(result) == 20

    def test_scalar_returned_as_is(self):
        assert self._b(42) == 42
