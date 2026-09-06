"""
QA-lane tests for pure functions in src/pipeline/timeline_text_features.py:
- _env_bool: env var boolean parsing
- _env_int: env var integer parsing
- build_feature_record: timeline event → feature dict (uses text_normalizer)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.pipeline.timeline_text_features import _env_bool, _env_int, build_feature_record

_NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _env_bool
# ---------------------------------------------------------------------------

class TestEnvBool:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_TTF_BOOL", raising=False)
        assert _env_bool("TEST_TTF_BOOL", True) is True
        assert _env_bool("TEST_TTF_BOOL", False) is False

    def test_truthy_values(self, monkeypatch):
        for val in ("1", "true", "yes", "on", "TRUE", "YES"):
            monkeypatch.setenv("TEST_TTF_BOOL", val)
            assert _env_bool("TEST_TTF_BOOL", False) is True

    def test_falsy_values(self, monkeypatch):
        for val in ("0", "false", "no", "off"):
            monkeypatch.setenv("TEST_TTF_BOOL", val)
            assert _env_bool("TEST_TTF_BOOL", True) is False


# ---------------------------------------------------------------------------
# _env_int
# ---------------------------------------------------------------------------

class TestEnvInt:
    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_TTF_INT", raising=False)
        assert _env_int("TEST_TTF_INT", 42) == 42

    def test_custom_value(self, monkeypatch):
        monkeypatch.setenv("TEST_TTF_INT", "100")
        assert _env_int("TEST_TTF_INT", 0) == 100

    def test_empty_string_uses_default(self, monkeypatch):
        monkeypatch.setenv("TEST_TTF_INT", "")
        assert _env_int("TEST_TTF_INT", 7) == 7


# ---------------------------------------------------------------------------
# build_feature_record
# ---------------------------------------------------------------------------

class TestBuildFeatureRecord:
    def _event(self, **overrides):
        base = {
            "id": "00000000-0000-0000-0000-000000000001",
            "entity_id": "00000000-0000-0000-0000-000000000002",
            "occurred_at": _NOW,
            "source": "instagram",
            "event_type": "CONTENT_PUBLISHED",
            "source_record_id": "post-1",
            "title": "Hello world, testing this feature",
            "detail": None,
            "metadata": None,
        }
        base.update(overrides)
        return base

    def test_returns_dict_for_valid_event(self):
        result = build_feature_record(self._event())
        assert isinstance(result, dict)
        assert result["event_id"] == "00000000-0000-0000-0000-000000000001"
        assert result["canonical_text"] == "Hello world, testing this feature"

    def test_returns_none_for_empty_text(self):
        result = build_feature_record(self._event(title=None, detail=None, metadata=None))
        assert result is None

    def test_token_count_positive(self):
        result = build_feature_record(self._event())
        assert result is not None
        assert result["token_count"] > 0

    def test_text_sha1_is_40_chars(self):
        result = build_feature_record(self._event())
        assert result is not None
        assert len(result["text_sha1"]) == 40

    def test_source_fingerprint_is_64_chars(self):
        result = build_feature_record(self._event())
        assert result is not None
        assert len(result["source_fingerprint"]) == 64

    def test_source_and_event_type_preserved(self):
        result = build_feature_record(self._event())
        assert result is not None
        assert result["source"] == "instagram"
        assert result["event_type"] == "CONTENT_PUBLISHED"

    def test_max_chars_respected(self):
        long_text = "a " * 5000
        result = build_feature_record(self._event(title=long_text), max_chars=50)
        assert result is not None
        assert result["char_count"] <= 50
