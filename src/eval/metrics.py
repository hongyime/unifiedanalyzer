from __future__ import annotations

from collections import Counter
from typing import Iterable, Sequence


def classification_metrics(y_true: Sequence[str], y_pred: Sequence[str]) -> dict:
    labels = sorted(set(y_true) | set(y_pred))
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
