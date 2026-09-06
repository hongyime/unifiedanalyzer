"""
QA-lane tests for pure functions in src/eval/metrics.py:
- recall_at_k: ranking recall metric
- mrr_at_k: mean reciprocal rank
- duplicate_count: fingerprint deduplication count
- regression_delta: metric delta between runs
- evaluate_metric_gates: gate pass/fail/warn evaluation
- classification_metrics: accuracy/F1 without sklearn
"""
from __future__ import annotations

import pytest

from src.eval.metrics import (
    classification_metrics,
    duplicate_count,
    evaluate_metric_gates,
    mrr_at_k,
    recall_at_k,
    regression_delta,
)


# ---------------------------------------------------------------------------
# recall_at_k
# ---------------------------------------------------------------------------

class TestRecallAtK:
    def test_perfect_recall(self):
        assert recall_at_k(["a", "b"], ["a", "b", "c"], k=3) == 1.0

    def test_zero_recall(self):
        assert recall_at_k(["a", "b"], ["c", "d", "e"], k=3) == 0.0

    def test_partial_recall(self):
        result = recall_at_k(["a", "b", "c"], ["a", "x", "y"], k=3)
        assert abs(result - 1/3) < 0.001

    def test_empty_expected_returns_zero(self):
        assert recall_at_k([], ["a", "b"], k=3) == 0.0

    def test_k_limits_candidates(self):
        # "b" is in ranked but after k=1 cutoff
        result = recall_at_k(["b"], ["a", "b"], k=1)
        assert result == 0.0

    def test_k_includes_hit(self):
        result = recall_at_k(["b"], ["a", "b"], k=2)
        assert result == 1.0


# ---------------------------------------------------------------------------
# mrr_at_k
# ---------------------------------------------------------------------------

class TestMrrAtK:
    def test_first_rank_gives_1(self):
        assert mrr_at_k(["a"], ["a", "b", "c"], k=3) == 1.0

    def test_second_rank_gives_half(self):
        result = mrr_at_k(["b"], ["a", "b", "c"], k=3)
        assert abs(result - 0.5) < 0.001

    def test_third_rank_gives_third(self):
        result = mrr_at_k(["c"], ["a", "b", "c"], k=3)
        assert abs(result - 1/3) < 0.001

    def test_not_found_returns_zero(self):
        assert mrr_at_k(["z"], ["a", "b", "c"], k=3) == 0.0

    def test_empty_expected_returns_zero(self):
        assert mrr_at_k([], ["a", "b"], k=3) == 0.0

    def test_k_limits_search(self):
        # "c" is at rank 3 but k=2 — not found
        result = mrr_at_k(["c"], ["a", "b", "c"], k=2)
        assert result == 0.0


# ---------------------------------------------------------------------------
# duplicate_count
# ---------------------------------------------------------------------------

class TestDuplicateCount:
    def test_no_duplicates(self):
        assert duplicate_count(["a", "b", "c"]) == 0

    def test_one_duplicate(self):
        assert duplicate_count(["a", "a", "b"]) == 1

    def test_triple_counts_as_two(self):
        # "a" appears 3 times → 3-1=2 duplicates
        assert duplicate_count(["a", "a", "a"]) == 2

    def test_multiple_duplicate_groups(self):
        # "a" twice + "b" twice = 1+1=2
        assert duplicate_count(["a", "a", "b", "b", "c"]) == 2

    def test_empty_returns_zero(self):
        assert duplicate_count([]) == 0

    def test_all_unique(self):
        assert duplicate_count(["x", "y", "z"]) == 0


# ---------------------------------------------------------------------------
# regression_delta
# ---------------------------------------------------------------------------

class TestRegressionDelta:
    def test_no_previous_returns_empty(self):
        assert regression_delta({"accuracy": 0.9}, None) == {}

    def test_improvement_positive_delta(self):
        delta = regression_delta({"f1": 0.8}, {"f1": 0.7})
        assert abs(delta["f1"] - 0.1) < 0.001

    def test_regression_negative_delta(self):
        delta = regression_delta({"f1": 0.6}, {"f1": 0.8})
        assert delta["f1"] < 0

    def test_non_numeric_fields_skipped(self):
        delta = regression_delta({"f1": 0.8, "label": "good"}, {"f1": 0.7, "label": "ok"})
        assert "label" not in delta

    def test_missing_previous_key_skipped(self):
        delta = regression_delta({"f1": 0.8, "new_metric": 0.5}, {"f1": 0.7})
        assert "new_metric" not in delta


# ---------------------------------------------------------------------------
# evaluate_metric_gates
# ---------------------------------------------------------------------------

class TestEvaluateMetricGates:
    def test_pass_when_above_minimum(self):
        result = evaluate_metric_gates("sentiment", {"macro_f1": 0.80})
        assert result["gate_status"] == "pass"
        assert result["gate_failures"] == []

    def test_fail_when_below_minimum(self):
        result = evaluate_metric_gates("sentiment", {"macro_f1": 0.50})
        assert result["gate_status"] == "fail"
        assert any("macro_f1" in f for f in result["gate_failures"])

    def test_fail_on_regression(self):
        result = evaluate_metric_gates(
            "sentiment",
            {"macro_f1": 0.70},
            previous_metrics={"macro_f1": 0.90},  # -0.20 regression
        )
        assert result["gate_status"] == "fail"

    def test_warn_on_small_regression(self):
        result = evaluate_metric_gates(
            "identity",
            {"macro_f1": 0.80},
            previous_metrics={"macro_f1": 0.86},  # -0.06 < warn threshold -0.05
        )
        assert result["gate_status"] in ("warn", "fail")

    def test_unknown_task_has_no_thresholds(self):
        result = evaluate_metric_gates("unknown_task", {"macro_f1": 0.0})
        assert result["gate_status"] == "pass"
        assert result["gate_thresholds"] == {}

    def test_delta_computed_correctly(self):
        result = evaluate_metric_gates(
            "sentiment",
            {"macro_f1": 0.75},
            previous_metrics={"macro_f1": 0.70},
        )
        assert abs(result["gate_delta"].get("macro_f1", 0) - 0.05) < 0.001

    def test_duplicate_count_max_gate(self):
        # alerts task: duplicate_count_max=0
        result = evaluate_metric_gates("alerts", {"duplicate_count": 1})
        assert result["gate_status"] == "fail"

    def test_duplicate_count_zero_passes(self):
        result = evaluate_metric_gates("alerts", {"duplicate_count": 0})
        assert result["gate_status"] == "pass"


# ---------------------------------------------------------------------------
# classification_metrics (local backend — no sklearn)
# ---------------------------------------------------------------------------

class TestClassificationMetrics:
    def test_perfect_predictions(self):
        result = classification_metrics(["a", "b", "a"], ["a", "b", "a"])
        assert result["accuracy"] == 1.0
        assert result["macro_f1"] == 1.0

    def test_all_wrong(self):
        result = classification_metrics(["a", "a"], ["b", "b"])
        assert result["accuracy"] == 0.0

    def test_empty_sequences(self):
        result = classification_metrics([], [])
        assert result["accuracy"] == 0.0
        assert result["support"] == 0

    def test_support_matches_input_length(self):
        result = classification_metrics(["a", "b", "c"], ["a", "b", "a"])
        assert result["support"] == 3

    def test_per_label_keys_present(self):
        result = classification_metrics(["a", "b"], ["a", "b"])
        assert "a" in result["labels"]
        assert "b" in result["labels"]
        assert "precision" in result["labels"]["a"]
        assert "recall" in result["labels"]["a"]
        assert "f1" in result["labels"]["a"]
