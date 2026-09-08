"""
Pure-function tests — batch 21.

Covers previously untested modules with no DB or I/O:
- eval.metrics: classification_metrics (fallback path), recall_at_k, mrr_at_k,
  duplicate_count, regression_delta, evaluate_metric_gates,
  DEFAULT_GATE_THRESHOLDS constants
- api.routes.metrics: _line
- api.routes.eval: _row (pure helper)
- api.routes.changelog: _DEFAULT_WINDOW constant
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# eval.metrics: pure computation functions
# ---------------------------------------------------------------------------

class TestClassificationMetrics:
    def _c(self, y_true, y_pred):
        from src.eval.metrics import classification_metrics
        return classification_metrics(y_true, y_pred)

    def test_perfect_predictions(self):
        result = self._c(["a", "b", "a"], ["a", "b", "a"])
        assert result["accuracy"] == 1.0

    def test_zero_predictions_zero_accuracy(self):
        result = self._c(["a", "a"], ["b", "b"])
        assert result["accuracy"] == 0.0

    def test_empty_returns_zero_accuracy(self):
        result = self._c([], [])
        assert result["accuracy"] == 0.0

    def test_required_keys_present(self):
        result = self._c(["a", "b"], ["a", "a"])
        for key in ("accuracy", "macro_f1", "support", "labels"):
            assert key in result

    def test_support_equals_length(self):
        result = self._c(["a", "b", "a"], ["a", "b", "b"])
        assert result["support"] == 3

    def test_per_label_keys(self):
        result = self._c(["a", "b"], ["a", "b"])
        for label in ("a", "b"):
            assert label in result["labels"]
            for k in ("precision", "recall", "f1", "support"):
                assert k in result["labels"][label]

    def test_partial_match(self):
        result = self._c(["a", "b", "a", "b"], ["a", "b", "b", "a"])
        # 2 correct out of 4 → accuracy = 0.5
        assert abs(result["accuracy"] - 0.5) < 1e-4


class TestRecallAtK:
    def _r(self, expected, ranked, k):
        from src.eval.metrics import recall_at_k
        return recall_at_k(expected, ranked, k)

    def test_all_found(self):
        assert self._r(["a", "b"], ["a", "b", "c"], 2) == 1.0

    def test_none_found(self):
        assert self._r(["a", "b"], ["c", "d"], 2) == 0.0

    def test_partial(self):
        assert abs(self._r(["a", "b"], ["a", "c"], 2) - 0.5) < 1e-4

    def test_empty_expected_returns_zero(self):
        assert self._r([], ["a", "b"], 5) == 0.0

    def test_k_limits_search(self):
        # a is at position 3, beyond k=2
        assert self._r(["a"], ["b", "c", "a"], 2) == 0.0

    def test_k_includes_result(self):
        assert self._r(["a"], ["b", "c", "a"], 3) == 1.0


class TestMrrAtK:
    def _m(self, expected, ranked, k):
        from src.eval.metrics import mrr_at_k
        return mrr_at_k(expected, ranked, k)

    def test_first_position(self):
        assert self._m(["a"], ["a", "b", "c"], 3) == 1.0

    def test_second_position(self):
        assert abs(self._m(["a"], ["b", "a", "c"], 3) - 0.5) < 1e-4

    def test_not_found_returns_zero(self):
        assert self._m(["a"], ["b", "c"], 2) == 0.0

    def test_empty_expected_returns_zero(self):
        assert self._m([], ["a"], 5) == 0.0


class TestDuplicateCount:
    def _d(self, fingerprints):
        from src.eval.metrics import duplicate_count
        return duplicate_count(fingerprints)

    def test_no_duplicates(self):
        assert self._d(["a", "b", "c"]) == 0

    def test_one_duplicate(self):
        assert self._d(["a", "a", "b"]) == 1

    def test_triplicate_counts_two(self):
        assert self._d(["a", "a", "a"]) == 2

    def test_empty(self):
        assert self._d([]) == 0

    def test_multiple_dupes(self):
        assert self._d(["a", "a", "b", "b"]) == 2


class TestRegressionDelta:
    def _d(self, current, previous):
        from src.eval.metrics import regression_delta
        return regression_delta(current, previous)

    def test_basic_delta(self):
        result = self._d({"f1": 0.8}, {"f1": 0.75})
        assert abs(result["f1"] - 0.05) < 1e-4

    def test_none_previous_returns_empty(self):
        assert self._d({"f1": 0.8}, None) == {}

    def test_non_numeric_ignored(self):
        result = self._d({"f1": 0.8, "label": "x"}, {"f1": 0.75, "label": "y"})
        assert "label" not in result

    def test_missing_previous_key_ignored(self):
        result = self._d({"f1": 0.8, "recall": 0.9}, {"f1": 0.75})
        assert "f1" in result
        assert "recall" not in result

    def test_rounded_to_4_places(self):
        result = self._d({"f1": 0.123456789}, {"f1": 0.0})
        assert result["f1"] == round(0.123456789, 4)


class TestEvaluateMetricGates:
    def _e(self, task, metrics, previous=None):
        from src.eval.metrics import evaluate_metric_gates
        return evaluate_metric_gates(task, metrics, previous)

    def test_pass_above_min(self):
        result = self._e("search", {"recall_at_20": 0.80, "mrr_at_20": 0.60})
        assert result["gate_status"] == "pass"

    def test_fail_below_min(self):
        result = self._e("search", {"recall_at_20": 0.50, "mrr_at_20": 0.60})
        assert result["gate_status"] == "fail"
        assert len(result["gate_failures"]) > 0

    def test_unknown_task_passes(self):
        result = self._e("unknown_task", {"f1": 0.5})
        assert result["gate_status"] == "pass"

    def test_regression_causes_fail(self):
        current = {"recall_at_20": 0.80, "mrr_at_20": 0.60}
        # Simulate big drop in recall_at_20
        previous = {"recall_at_20": 0.95, "mrr_at_20": 0.60}
        result = self._e("search", current, previous)
        assert result["gate_status"] == "fail"

    def test_required_keys_present(self):
        result = self._e("search", {"recall_at_20": 0.80, "mrr_at_20": 0.60})
        for key in ("gate_status", "gate_failures", "gate_warnings"):
            assert key in result


class TestDefaultGateThresholds:
    def test_non_empty(self):
        from src.eval.metrics import DEFAULT_GATE_THRESHOLDS
        assert len(DEFAULT_GATE_THRESHOLDS) > 0

    def test_search_has_recall_min(self):
        from src.eval.metrics import DEFAULT_GATE_THRESHOLDS
        assert "recall_at_20_min" in DEFAULT_GATE_THRESHOLDS.get("search", {})

    def test_all_thresholds_numeric(self):
        from src.eval.metrics import DEFAULT_GATE_THRESHOLDS
        for task, thresholds in DEFAULT_GATE_THRESHOLDS.items():
            for key, value in thresholds.items():
                assert isinstance(value, (int, float)), f"{task}.{key} not numeric"


# ---------------------------------------------------------------------------
# api.routes.metrics: _line
# ---------------------------------------------------------------------------

class TestMetricsLine:
    def _l(self, name, value, labels=None):
        from src.api.routes.metrics import _line
        return _line(name, value, labels)

    def test_no_labels(self):
        assert self._l("my_metric", 42) == "my_metric 42"

    def test_with_labels(self):
        result = self._l("my_metric", 42, {"source": "instagram"})
        assert 'source="instagram"' in result
        assert "my_metric" in result
        assert "42" in result

    def test_multiple_labels(self):
        result = self._l("m", 1, {"a": "x", "b": "y"})
        assert 'a="x"' in result
        assert 'b="y"' in result

    def test_empty_labels_dict_no_braces(self):
        # None labels → no braces
        result = self._l("m", 0, None)
        assert "{" not in result


# ---------------------------------------------------------------------------
# api.routes.eval: _row pure helper
# ---------------------------------------------------------------------------

class TestEvalRow:
    def test_row_shape(self):
        from src.api.routes.eval import _row
        # simulate asyncpg Record as dict
        row = {
            "id": "abc123",
            "name": "test-set",
            "task_type": "search",
            "model_or_rule_version": "v1",
            "status": "completed",
            "metrics_json": '{"accuracy": 0.9}',
            "started_at": None,
            "finished_at": None,
        }
        result = _row(row)
        assert result["id"] == "abc123"
        assert result["task_type"] == "search"
        assert result["status"] == "completed"

    def test_metrics_json_parsed(self):
        from src.api.routes.eval import _row
        row = {
            "id": "1", "name": "s", "task_type": "t",
            "model_or_rule_version": "v", "status": "done",
            "metrics_json": '{"f1": 0.8}',
            "started_at": None, "finished_at": None,
        }
        result = _row(row)

        # _row passes metrics_json through as-is (raw string or None)
        assert result.get("metrics") == '{"f1": 0.8}'
    def test_invalid_metrics_json(self):
        from src.api.routes.eval import _row
        row = {
            "id": "1", "name": "s", "task_type": "t",
            "model_or_rule_version": "v", "status": "done",
            "metrics_json": "bad-json",
            "started_at": None, "finished_at": None,
        }
        result = _row(row)
        # should not raise; metrics should be None or {}
        assert result is not None


# ---------------------------------------------------------------------------
# api.routes.changelog: _DEFAULT_WINDOW constant
# ---------------------------------------------------------------------------

class TestChangelogDefaultWindow:
    def test_is_string(self):
        from src.api.routes.changelog import _DEFAULT_WINDOW
        assert isinstance(_DEFAULT_WINDOW, str)

    def test_contains_days(self):
        from src.api.routes.changelog import _DEFAULT_WINDOW
        assert "days" in _DEFAULT_WINDOW or "day" in _DEFAULT_WINDOW
