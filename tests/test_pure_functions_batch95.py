"""
Pure-function tests — batch 95.

Covers:
- pipeline.calibration_watchdog: _int_env, _float_env
- pipeline.entity_enrichment: _is_enabled, _max_chars, _batch_size,
  _model_name, _sort_bucket
- pipeline.interaction_graph: _jsonb_param
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# pipeline.calibration_watchdog: _int_env
# ---------------------------------------------------------------------------

class TestCalibrationIntEnv:
    def _i(self, key, default=5, value=None):
        from src.pipeline.calibration_watchdog import _int_env
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _int_env(key, default)
        finally:
            os.environ.pop(key, None)

    def test_returns_default_when_unset(self):
        assert self._i("__CW_INT_X__") == 5

    def test_returns_env_value(self):
        assert self._i("__CW_INT_Y__", value=10) == 10

    def test_invalid_returns_default(self):
        assert self._i("__CW_INT_Z__", value="bad") == 5


# ---------------------------------------------------------------------------
# pipeline.calibration_watchdog: _float_env
# ---------------------------------------------------------------------------

class TestCalibrationFloatEnv:
    def _f(self, key, default=0.5, value=None):
        from src.pipeline.calibration_watchdog import _float_env
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _float_env(key, default)
        finally:
            os.environ.pop(key, None)

    def test_returns_default_when_unset(self):
        assert abs(self._f("__CW_FLOAT_X__") - 0.5) < 1e-9

    def test_returns_env_value(self):
        assert abs(self._f("__CW_FLOAT_Y__", value=0.8) - 0.8) < 1e-9

    def test_invalid_returns_default(self):
        assert abs(self._f("__CW_FLOAT_Z__", value="bad") - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# pipeline.entity_enrichment: _is_enabled
# ---------------------------------------------------------------------------

class TestEntityEnrichmentIsEnabled:
    def _e(self, value=None):
        from src.pipeline.entity_enrichment import _is_enabled
        key = "ENTITY_ENRICHMENT_ENABLED"
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _is_enabled()
        finally:
            os.environ.pop(key, None)

    def test_default_disabled(self):
        assert self._e() is False

    def test_one_enables(self):
        assert self._e("1") is True

    def test_true_enables(self):
        assert self._e("true") is True

    def test_zero_disables(self):
        assert self._e("0") is False


# ---------------------------------------------------------------------------
# pipeline.entity_enrichment: _max_chars, _batch_size, _model_name
# ---------------------------------------------------------------------------

class TestEntityEnrichmentConfig:
    def test_max_chars_default(self):
        from src.pipeline.entity_enrichment import _max_chars
        os.environ.pop("NER_MAX_CHARS_PER_ENTITY", None)
        assert _max_chars() == 20000

    def test_batch_size_default(self):
        from src.pipeline.entity_enrichment import _batch_size
        os.environ.pop("NER_ENTITY_BATCH_PER_RUN", None)
        assert _batch_size() == 100

    def test_model_name_default(self):
        from src.pipeline.entity_enrichment import _model_name
        os.environ.pop("NER_MODEL", None)
        assert _model_name() == "en_core_web_trf"

    def test_model_name_env_override(self):
        from src.pipeline.entity_enrichment import _model_name
        os.environ["NER_MODEL"] = "en_core_web_sm"
        try:
            assert _model_name() == "en_core_web_sm"
        finally:
            os.environ.pop("NER_MODEL", None)


# ---------------------------------------------------------------------------
# pipeline.entity_enrichment: _sort_bucket
# ---------------------------------------------------------------------------

class TestSortBucket:
    def _s(self, counts, top_n=50):
        from src.pipeline.entity_enrichment import _sort_bucket
        return _sort_bucket(counts, top_n)

    def test_empty_returns_empty(self):
        assert self._s({}) == []

    def test_returns_list_of_dicts(self):
        result = self._s({"Alice": 3, "Bob": 1})
        assert isinstance(result, list)
        assert all(isinstance(item, dict) for item in result)

    def test_sorted_by_count_descending(self):
        result = self._s({"Alice": 3, "Bob": 5, "Charlie": 1})
        counts = [item.get("count", item.get("n", 0)) for item in result]
        assert counts == sorted(counts, reverse=True)

    def test_top_n_limits_output(self):
        counts = {f"name_{i}": i for i in range(100)}
        result = self._s(counts, top_n=5)
        assert len(result) <= 5


# ---------------------------------------------------------------------------
# pipeline.interaction_graph: _jsonb_param
# ---------------------------------------------------------------------------

class TestInteractionGraphJsonbParam:
    def _j(self, raw):
        from src.pipeline.interaction_graph import _jsonb_param
        return _jsonb_param(raw)

    def test_none_returns_empty_json(self):
        assert self._j(None) == "{}"

    def test_string_returned_as_is(self):
        assert self._j('{"k": 1}') == '{"k": 1}'

    def test_dict_serialized(self):
        import json
        result = self._j({"k": 1})
        assert json.loads(result) == {"k": 1}

    def test_list_serialized(self):
        import json
        result = self._j([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]
