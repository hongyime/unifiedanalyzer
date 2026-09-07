"""
Pure-function tests — batch 6.

Covers previously untested modules with no DB or I/O:
- pipeline.relationship_intelligence: _sorted_pair, _jsonb_param, _decode_meta,
  _jaccard, _scaled_similarity, _cosine_sim
- pipeline.incremental_runner: _env_int, _env_bool, _translation_phase_enabled
"""
from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# relationship_intelligence pure helpers
# ---------------------------------------------------------------------------

class TestSortedPair:
    def _s(self, a, b):
        from src.pipeline.relationship_intelligence import _sorted_pair
        return _sorted_pair(a, b)

    def test_already_sorted(self):
        assert self._s("a", "b") == ("a", "b")

    def test_reversed_input(self):
        assert self._s("b", "a") == ("a", "b")

    def test_equal_values(self):
        assert self._s("x", "x") == ("x", "x")

    def test_uuid_like_strings(self):
        a = "00000000-0000-0000-0000-000000000001"
        b = "00000000-0000-0000-0000-000000000002"
        result = self._s(b, a)
        assert result == (a, b)


class TestJsonbParam:
    def _j(self, raw):
        from src.pipeline.relationship_intelligence import _jsonb_param
        return _jsonb_param(raw)

    def test_dict_to_json_string(self):
        result = self._j({"key": "value"})
        assert json.loads(result) == {"key": "value"}

    def test_list_to_json_string(self):
        result = self._j([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_none_to_json_null(self):
        assert self._j(None) == "null"

    def test_non_serialisable_uses_str_default(self):
        from datetime import datetime
        dt = datetime(2026, 1, 1)
        result = self._j({"ts": dt})
        parsed = json.loads(result)
        assert "ts" in parsed


class TestDecodeMetaRelIntel:
    def _d(self, raw):
        from src.pipeline.relationship_intelligence import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"a": 1}) == {"a": 1}

    def test_json_string(self):
        assert self._d('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_array_json_returns_empty(self):
        assert self._d("[1,2]") == {}


class TestJaccard:
    def _j(self, a, b):
        from src.pipeline.relationship_intelligence import _jaccard
        return _jaccard(a, b)

    def test_identical_sets(self):
        assert self._j({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self):
        assert self._j({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self):
        # intersection=1, union=3 → 1/3
        result = self._j({"a", "b"}, {"b", "c"})
        assert abs(result - 1/3) < 1e-9

    def test_empty_a(self):
        assert self._j(set(), {"a"}) == 0.0

    def test_empty_b(self):
        assert self._j({"a"}, set()) == 0.0

    def test_both_empty(self):
        assert self._j(set(), set()) == 0.0

    def test_subset(self):
        # a ⊂ b: intersection=1, union=2 → 0.5
        result = self._j({"a"}, {"a", "b"})
        assert abs(result - 0.5) < 1e-9


class TestScaledSimilarity:
    def _s(self, a, b, floor=1.0):
        from src.pipeline.relationship_intelligence import _scaled_similarity
        return _scaled_similarity(a, b, floor)

    def test_identical_values(self):
        assert self._s(5.0, 5.0) == 1.0

    def test_zero_difference(self):
        assert self._s(0, 0) == 1.0

    def test_max_difference(self):
        # |10-0| / max(1, 10, 0) = 10/10 = 1.0 → similarity = 0.0
        assert self._s(10.0, 0.0) == 0.0

    def test_partial_difference(self):
        # |8-4| / max(1, 8, 4) = 4/8 = 0.5 → similarity = 0.5
        result = self._s(8.0, 4.0)
        assert abs(result - 0.5) < 1e-9

    def test_non_numeric_returns_none(self):
        assert self._s("bad", 1.0) is None

    def test_none_returns_none(self):
        assert self._s(None, 1.0) is None

    def test_floor_applied(self):
        # |0.1-0.2| / max(10.0, 0.1, 0.2) = 0.1/10 = 0.01 → 0.99
        result = self._s(0.1, 0.2, floor=10.0)
        assert abs(result - 0.99) < 1e-9

    def test_result_never_negative(self):
        result = self._s(1000.0, 0.0)
        assert result >= 0.0


class TestCosineSim:
    def _c(self, a, b):
        from src.pipeline.relationship_intelligence import _cosine_sim
        return _cosine_sim(a, b)

    def test_identical_vectors(self):
        assert abs(self._c({"a": 1, "b": 2}, {"a": 1, "b": 2}) - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        assert self._c({"a": 1}, {"b": 1}) == 0.0

    def test_empty_dicts(self):
        assert self._c({}, {}) == 0.0

    def test_empty_a(self):
        assert self._c({}, {"a": 1}) == 0.0

    def test_empty_b(self):
        assert self._c({"a": 1}, {}) == 0.0

    def test_partial_overlap(self):
        # a=(1,1,0), b=(0,1,1) → dot=1, mag_a=√2, mag_b=√2 → 1/2=0.5
        result = self._c({"x": 1, "y": 1}, {"y": 1, "z": 1})
        assert abs(result - 0.5) < 1e-9

    def test_zero_magnitude_a(self):
        assert self._c({"a": 0}, {"a": 1}) == 0.0

    def test_result_between_0_and_1(self):
        result = self._c({"a": 3, "b": 1}, {"a": 1, "b": 3})
        assert 0.0 <= result <= 1.0


# ---------------------------------------------------------------------------
# incremental_runner pure helpers
# ---------------------------------------------------------------------------

class TestEnvInt:
    def _e(self, name, default, minimum=1, monkeypatch=None):
        from src.pipeline.incremental_runner import _env_int
        return _env_int(name, default, minimum=minimum)

    def test_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("_TEST_ENV_INT", "42")
        from src.pipeline.incremental_runner import _env_int
        assert _env_int("_TEST_ENV_INT", 10) == 42

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_ENV_INT", raising=False)
        from src.pipeline.incremental_runner import _env_int
        assert _env_int("_TEST_ENV_INT", 99) == 99

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("_TEST_ENV_INT", "0")
        from src.pipeline.incremental_runner import _env_int
        assert _env_int("_TEST_ENV_INT", 5, minimum=3) == 3

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("_TEST_ENV_INT", "bad")
        from src.pipeline.incremental_runner import _env_int
        assert _env_int("_TEST_ENV_INT", 7) == 7


class TestEnvBool:
    def _b(self, name, default=False):
        from src.pipeline.incremental_runner import _env_bool
        return _env_bool(name, default)

    def test_true_values(self, monkeypatch):
        from src.pipeline.incremental_runner import _env_bool
        for val in ("1", "true", "True", "TRUE", "yes", "YES", "on", "ON"):
            monkeypatch.setenv("_TEST_ENV_BOOL", val)
            assert _env_bool("_TEST_ENV_BOOL") is True, f"Failed for {val!r}"

    def test_false_values(self, monkeypatch):
        from src.pipeline.incremental_runner import _env_bool
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("_TEST_ENV_BOOL", val)
            assert _env_bool("_TEST_ENV_BOOL") is False, f"Failed for {val!r}"

    def test_unset_returns_default_false(self, monkeypatch):
        monkeypatch.delenv("_TEST_ENV_BOOL", raising=False)
        from src.pipeline.incremental_runner import _env_bool
        assert _env_bool("_TEST_ENV_BOOL", False) is False

    def test_unset_returns_default_true(self, monkeypatch):
        monkeypatch.delenv("_TEST_ENV_BOOL", raising=False)
        from src.pipeline.incremental_runner import _env_bool
        assert _env_bool("_TEST_ENV_BOOL", True) is True


class TestTranslationPhaseEnabled:
    def test_disabled_by_env_flag(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRANSLATION_PHASE", "0")
        from src.pipeline.incremental_runner import _translation_phase_enabled
        assert _translation_phase_enabled() is False

    def test_enabled_by_env_flag(self, monkeypatch):
        monkeypatch.setenv("ENABLE_TRANSLATION_PHASE", "1")
        from src.pipeline.incremental_runner import _translation_phase_enabled
        assert _translation_phase_enabled() is True

    def test_noop_provider_disabled(self, monkeypatch):
        monkeypatch.delenv("ENABLE_TRANSLATION_PHASE", raising=False)
        monkeypatch.setenv("TRANSLATION_PROVIDER", "noop")
        from src.pipeline.incremental_runner import _translation_phase_enabled
        assert _translation_phase_enabled() is False

    def test_real_provider_enabled(self, monkeypatch):
        monkeypatch.delenv("ENABLE_TRANSLATION_PHASE", raising=False)
        monkeypatch.setenv("TRANSLATION_PROVIDER", "deepl")
        from src.pipeline.incremental_runner import _translation_phase_enabled
        assert _translation_phase_enabled() is True

    def test_disabled_provider_disabled(self, monkeypatch):
        monkeypatch.delenv("ENABLE_TRANSLATION_PHASE", raising=False)
        monkeypatch.setenv("TRANSLATION_PROVIDER", "disabled")
        from src.pipeline.incremental_runner import _translation_phase_enabled
        assert _translation_phase_enabled() is False
