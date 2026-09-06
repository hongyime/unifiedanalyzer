"""
QA-lane tests for src/merge_candidates.py: merge_candidate_min_weight,
merge_candidate_notify_min_confidence.
"""
from __future__ import annotations

import pytest

from src.merge_candidates import (
    DEFAULT_MERGE_CANDIDATE_MIN_WEIGHT,
    DEFAULT_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE,
    merge_candidate_min_weight,
    merge_candidate_notify_min_confidence,
)


class TestMergeCandidateMinWeight:
    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("NEW_IDENTITY_LINK_MIN_WEIGHT", raising=False)
        assert merge_candidate_min_weight() == DEFAULT_MERGE_CANDIDATE_MIN_WEIGHT

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("NEW_IDENTITY_LINK_MIN_WEIGHT", "70")
        assert merge_candidate_min_weight() == 70

    def test_returns_int(self, monkeypatch):
        monkeypatch.delenv("NEW_IDENTITY_LINK_MIN_WEIGHT", raising=False)
        assert isinstance(merge_candidate_min_weight(), int)


class TestMergeCandidateNotifyMinConfidence:
    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE", raising=False)
        assert merge_candidate_notify_min_confidence() == float(DEFAULT_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE)

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE", "75.5")
        assert abs(merge_candidate_notify_min_confidence() - 75.5) < 1e-9

    def test_returns_float(self, monkeypatch):
        monkeypatch.delenv("MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE", raising=False)
        assert isinstance(merge_candidate_notify_min_confidence(), float)
