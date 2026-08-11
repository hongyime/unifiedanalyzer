from __future__ import annotations

from collections import Counter
from typing import Iterable, Sequence


def classification_metrics(y_true: Sequence[str], y_pred: Sequence[str]) -> dict:
    try:
        from sklearn.metrics import precision_recall_fscore_support  # type: ignore
    except Exception:  # noqa: BLE001 - sklearn is optional at runtime
        precision_recall_fscore_support = None

    labels = sorted(set(y_true) | set(y_pred))
    if precision_recall_fscore_support and labels:
        precision, recall, f1, support = precision_recall_fscore_support(
            y_true,
            y_pred,
            labels=labels,
            zero_division=0,
        )
        per_label = {
            label: {
                "precision": round(float(precision[idx]), 4),
                "recall": round(float(recall[idx]), 4),
                "f1": round(float(f1[idx]), 4),
                "support": int(support[idx]),
            }
            for idx, label in enumerate(labels)
        }
        correct = sum(1 for truth, pred in zip(y_true, y_pred) if truth == pred)
        return {
            "accuracy": round(correct / len(y_true), 4) if y_true else 0.0,
            "macro_f1": round(sum(float(v["f1"]) for v in per_label.values()) / len(per_label), 4),
            "support": len(y_true),
            "labels": per_label,
            "metrics_backend": "sklearn",
        }

    per_label = {}
    total_correct = 0
    for label in labels:
        tp = sum(1 for truth, pred in zip(y_true, y_pred) if truth == label and pred == label)
        fp = sum(1 for truth, pred in zip(y_true, y_pred) if truth != label and pred == label)
        fn = sum(1 for truth, pred in zip(y_true, y_pred) if truth == label and pred != label)
        support = sum(1 for truth in y_true if truth == label)
        total_correct += tp
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
    macro_f1 = sum(v["f1"] for v in per_label.values()) / len(per_label) if per_label else 0.0
    return {
        "accuracy": round(total_correct / len(y_true), 4) if y_true else 0.0,
        "macro_f1": round(macro_f1, 4),
        "support": len(y_true),
        "labels": per_label,
        "metrics_backend": "local",
    }


def recall_at_k(expected_ids: Iterable[str], ranked_ids: Sequence[str], k: int) -> float:
    expected = set(expected_ids)
    if not expected:
        return 0.0
    hits = expected.intersection(ranked_ids[:k])
    return round(len(hits) / len(expected), 4)


def mrr_at_k(expected_ids: Iterable[str], ranked_ids: Sequence[str], k: int) -> float:
    expected = set(expected_ids)
    if not expected:
        return 0.0
    for idx, item_id in enumerate(ranked_ids[:k], start=1):
        if item_id in expected:
            return round(1.0 / idx, 4)
    return 0.0


def duplicate_count(fingerprints: Iterable[str]) -> int:
    counts = Counter(fingerprints)
    return sum(count - 1 for count in counts.values() if count > 1)


DEFAULT_GATE_THRESHOLDS: dict[str, dict[str, float]] = {
    "search": {
        "recall_at_20_min": 0.75,
        "mrr_at_20_min": 0.50,
        "recall_at_20_drop_fail": -0.10,
    },
    "sentiment": {
        "macro_f1_min": 0.65,
        "macro_f1_drop_fail": -0.08,
    },
    "identity": {
        "macro_f1_min": 0.70,
        "macro_f1_drop_warn": -0.05,
    },
    "face": {
        "macro_f1_min": 0.70,
        "macro_f1_drop_warn": -0.05,
    },
    "location": {
        "macro_f1_min": 0.70,
        "macro_f1_drop_warn": -0.05,
    },
    "alerts": {
        "duplicate_count_max": 0,
    },
}


def regression_delta(current: dict, previous: dict | None) -> dict[str, float]:
    if not previous:
        return {}
    delta: dict[str, float] = {}
    for key, value in current.items():
        previous_value = previous.get(key)
        if isinstance(value, (int, float)) and isinstance(previous_value, (int, float)):
            delta[key] = round(float(value) - float(previous_value), 4)
    return delta


def evaluate_metric_gates(task: str, metrics: dict, previous_metrics: dict | None = None) -> dict:
    thresholds = DEFAULT_GATE_THRESHOLDS.get(task, {})
    failures: list[str] = []
    warnings: list[str] = []
    delta = regression_delta(metrics, previous_metrics)

    def metric_value(name: str) -> float:
        value = metrics.get(name)
        return float(value) if isinstance(value, (int, float)) else 0.0

    for key, threshold in thresholds.items():
        if key.endswith("_min"):
            metric = key[:-4]
            if metric_value(metric) < threshold:
                failures.append(f"{metric} below minimum {threshold}")
        elif key.endswith("_max"):
            metric = key[:-4]
            if metric_value(metric) > threshold:
                failures.append(f"{metric} above maximum {threshold}")
        elif key.endswith("_drop_fail"):
            metric = key[:-10]
            if delta.get(metric, 0.0) < threshold:
                failures.append(f"{metric} regressed by {delta[metric]}")
        elif key.endswith("_drop_warn"):
            metric = key[:-10]
            if delta.get(metric, 0.0) < threshold:
                warnings.append(f"{metric} regressed by {delta[metric]}")

    status = "fail" if failures else "warn" if warnings else "pass"
    return {
        "gate_status": status,
        "gate_failures": failures,
        "gate_warnings": warnings,
        "gate_delta": delta,
        "gate_thresholds": thresholds,
    }
