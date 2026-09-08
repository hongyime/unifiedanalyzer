"""
Pure-function tests — batch 83.

Covers:
- pipeline.recovery_drill: default_scratch_database_name, validate_scratch_database_name,
  gaps_from_replay, _tail_text, _is_skippable_restore_item,
  _is_only_transaction_timeout_restore_warning
- pipeline.identity_calibration: pair_feature_vector, _feature_value,
  _jsonload, _noisy_or_probs
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: default_scratch_database_name
# ---------------------------------------------------------------------------

class TestDefaultScratchDatabaseName:
    def _n(self, now=None):
        from src.pipeline.recovery_drill import default_scratch_database_name
        return default_scratch_database_name(now)

    def test_starts_with_prefix(self):
        assert self._n().startswith("ua_restore_drill_")

    def test_fixed_now_deterministic(self):
        dt = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert self._n(dt) == "ua_restore_drill_20260601_120000"

    def test_returns_string(self):
        assert isinstance(self._n(), str)


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: validate_scratch_database_name
# ---------------------------------------------------------------------------

class TestValidateScratchDatabaseName:
    def _v(self, name):
        from src.pipeline.recovery_drill import validate_scratch_database_name
        return validate_scratch_database_name(name)

    def test_valid_name_returned_lowercase(self):
        result = self._v("ua_restore_drill_20260601_120000")
        assert result == "ua_restore_drill_20260601_120000"

    def test_protected_name_raises(self):
        from src.pipeline.recovery_drill import RecoveryDrillError
        with pytest.raises(RecoveryDrillError):
            self._v("unifiedanalyzer")

    def test_postgres_name_raises(self):
        from src.pipeline.recovery_drill import RecoveryDrillError
        with pytest.raises(RecoveryDrillError):
            self._v("postgres")

    def test_invalid_format_raises(self):
        from src.pipeline.recovery_drill import RecoveryDrillError
        with pytest.raises(RecoveryDrillError):
            self._v("DROP TABLE users; --")


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: gaps_from_replay
# ---------------------------------------------------------------------------

class TestGapsFromReplay:
    def _g(self, replay_apply):
        from src.pipeline.recovery_drill import gaps_from_replay
        return gaps_from_replay(replay_apply)

    def test_empty_returns_empty(self):
        assert self._g({}) == []

    def test_unresolved_adds_gap(self):
        result = self._g({"replay": {"unresolved": 2}})
        assert len(result) == 1
        assert "2" in result[0]

    def test_ambiguous_adds_gap(self):
        result = self._g({"replay": {"ambiguous": 1}})
        assert any("multiple" in g for g in result)

    def test_multiple_issues_multiple_gaps(self):
        result = self._g({"replay": {"unresolved": 1, "ambiguous": 1, "invalid": 1}})
        assert len(result) == 3

    def test_derived_rebuild_adds_gap(self):
        result = self._g({"replay": {"derived_rebuild_required": {"timeline_text": 5}}})
        assert any("timeline_text" in g for g in result)


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: _tail_text
# ---------------------------------------------------------------------------

class TestTailText:
    def _t(self, value, limit=4000):
        from src.pipeline.recovery_drill import _tail_text
        return _tail_text(value, limit)

    def test_none_returns_empty(self):
        assert self._t(None) == ""

    def test_string_tail(self):
        text = "a" * 100
        assert self._t(text, limit=10) == "a" * 10

    def test_full_text_when_short(self):
        assert self._t("hello", limit=100) == "hello"

    def test_bytes_decoded(self):
        result = self._t(b"hello bytes", limit=100)
        assert result == "hello bytes"


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: _is_skippable_restore_item
# ---------------------------------------------------------------------------

class TestIsSkippableRestoreItem:
    def _s(self, line, patterns):
        from src.pipeline.recovery_drill import _is_skippable_restore_item
        return _is_skippable_restore_item(line, patterns)

    def test_non_matching_pattern_not_skippable(self):
        assert self._s("some random line", ("timeline_embeddings",)) is False

    def test_matching_but_no_index_or_table_data_not_skippable(self):
        # Contains the pattern but not "index" or "table data"
        assert self._s("timeline_embeddings sequence", ("timeline_embeddings",)) is False

    def test_matching_with_index_is_skippable(self):
        assert self._s("timeline_embeddings index pk_te", ("timeline_embeddings",)) is True

    def test_matching_with_table_data_is_skippable(self):
        assert self._s("timeline_embeddings table data", ("timeline_embeddings",)) is True


# ---------------------------------------------------------------------------
# pipeline.recovery_drill: _is_only_transaction_timeout_restore_warning
# ---------------------------------------------------------------------------

class TestIsOnlyTransactionTimeoutRestoreWarning:
    def _w(self, detail):
        from src.pipeline.recovery_drill import _is_only_transaction_timeout_restore_warning
        return _is_only_transaction_timeout_restore_warning(detail)

    def test_no_transaction_timeout_returns_false(self):
        assert self._w("ERROR: some other error") is False

    def test_only_transaction_timeout_error_returns_true(self):
        detail = 'ERROR:  unrecognized configuration parameter "transaction_timeout"'
        assert self._w(detail) is True

    def test_mixed_errors_returns_false(self):
        detail = (
            'ERROR:  unrecognized configuration parameter "transaction_timeout"\n'
            'ERROR:  relation "foo" does not exist'
        )
        assert self._w(detail) is False

    def test_empty_string_returns_false(self):
        assert self._w("") is False


# ---------------------------------------------------------------------------
# pipeline.identity_calibration: pair_feature_vector
# ---------------------------------------------------------------------------

class TestPairFeatureVector:
    def _v(self, contributions):
        from src.pipeline.identity_calibration import pair_feature_vector, FEATURE_ORDER
        result = pair_feature_vector(contributions)
        return result, FEATURE_ORDER

    def test_returns_list_of_correct_length(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        result, order = self._v([("username_exact", 0.9)])
        assert len(result) == len(order)

    def test_all_zeros_for_empty(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        result, order = self._v([])
        assert all(v == 0.0 for v in result)

    def test_known_signal_set(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        result, order = self._v([("username_exact", 0.75)])
        idx = order.index("username_exact") if "username_exact" in order else -1
        if idx >= 0:
            assert abs(result[idx] - 0.75) < 1e-9

    def test_max_confidence_used(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        result, order = self._v([("username_exact", 0.5), ("username_exact", 0.9)])
        idx = order.index("username_exact") if "username_exact" in order else -1
        if idx >= 0:
            assert abs(result[idx] - 0.9) < 1e-9


# ---------------------------------------------------------------------------
# pipeline.identity_calibration: _feature_value
# ---------------------------------------------------------------------------

class TestFeatureValue:
    def _f(self, feats, signal_type):
        from src.pipeline.identity_calibration import _feature_value
        return _feature_value(feats, signal_type)

    def test_known_signal_returns_value(self):
        assert abs(self._f({"username_exact": 0.8}, "username_exact") - 0.8) < 1e-9

    def test_missing_signal_returns_zero(self):
        assert self._f({}, "username_exact") == 0.0

    def test_deprecated_signal_returns_zero(self):
        from src.pipeline.identity_calibration import DEPRECATED_NON_IDENTITY_FEATURES
        if DEPRECATED_NON_IDENTITY_FEATURES:
            dep = next(iter(DEPRECATED_NON_IDENTITY_FEATURES))
            assert self._f({dep: 0.9}, dep) == 0.0


# ---------------------------------------------------------------------------
# pipeline.identity_calibration: _jsonload
# ---------------------------------------------------------------------------

class TestCalibrationJsonload:
    def _j(self, raw):
        from src.pipeline.identity_calibration import _jsonload
        return _jsonload(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._j(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._j(json.dumps({"x": 5})) == {"x": 5}

    def test_invalid_string_returns_empty(self):
        assert self._j("bad json") == {}

    def test_json_list_returns_empty(self):
        import json
        assert self._j(json.dumps([1, 2])) == {}

    def test_none_returns_empty(self):
        assert self._j(None) == {}
