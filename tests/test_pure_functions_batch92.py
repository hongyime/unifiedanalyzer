"""
Pure-function tests — batch 92.

Covers:
- pipeline.translation_worker: text_version_hash, translation_decision
- util.audit_log: _canonical_json, _is_sha256_hex, _clean_str
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.translation_worker: text_version_hash
# ---------------------------------------------------------------------------

class TestTextVersionHash:
    def _h(self, text, version=None):
        from src.pipeline.translation_worker import text_version_hash, TRANSLATOR_VERSION
        if version is not None:
            return text_version_hash(text, version)
        return text_version_hash(text)

    def test_returns_40_hex(self):
        result = self._h("hello")
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._h("test") == self._h("test")

    def test_different_text_different_hash(self):
        assert self._h("foo") != self._h("bar")

    def test_different_version_different_hash(self):
        assert self._h("text", "v1") != self._h("text", "v2")

    def test_empty_text(self):
        result = self._h("")
        assert len(result) == 40


# ---------------------------------------------------------------------------
# pipeline.translation_worker: translation_decision
# ---------------------------------------------------------------------------

class TestTranslationDecision:
    def _d(self, source_language, token_count, watched=False, target_language="en"):
        from src.pipeline.translation_worker import translation_decision
        return translation_decision(
            source_language=source_language,
            token_count=token_count,
            watched=watched,
            target_language=target_language,
        )

    def test_english_skipped(self):
        result = self._d("en", 10)
        assert result.should_translate is False
        assert result.reason == "english_or_unknown"

    def test_unknown_skipped(self):
        result = self._d("und", 10)
        assert result.should_translate is False

    def test_none_language_skipped(self):
        result = self._d(None, 10)
        assert result.should_translate is False

    def test_non_english_long_translated(self):
        result = self._d("zh", 10)
        assert result.should_translate is True

    def test_too_short_skipped(self):
        result = self._d("zh", 2)
        assert result.should_translate is False
        assert result.reason == "too_short"

    def test_too_short_watched_translated(self):
        result = self._d("zh", 2, watched=True)
        assert result.should_translate is True

    def test_same_as_target_skipped(self):
        result = self._d("en", 10, target_language="en")
        assert result.should_translate is False


# ---------------------------------------------------------------------------
# util.audit_log: _canonical_json
# ---------------------------------------------------------------------------

class TestCanonicalJson:
    def _c(self, obj):
        from src.util.audit_log import _canonical_json
        return _canonical_json(obj)

    def test_returns_string(self):
        assert isinstance(self._c({"k": 1}), str)

    def test_deterministic_regardless_of_insertion_order(self):
        a = self._c({"b": 2, "a": 1})
        b = self._c({"a": 1, "b": 2})
        assert a == b

    def test_no_spaces(self):
        result = self._c({"k": 1})
        assert " " not in result

    def test_datetime_serialized_as_string(self):
        from datetime import datetime, timezone
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        result = self._c({"ts": dt})
        assert "2026" in result

    def test_empty_dict(self):
        assert self._c({}) == "{}"


# ---------------------------------------------------------------------------
# util.audit_log: _is_sha256_hex
# ---------------------------------------------------------------------------

class TestIsSha256Hex:
    def _s(self, v):
        from src.util.audit_log import _is_sha256_hex
        return _is_sha256_hex(v)

    def test_valid_64_hex(self):
        assert self._s("a" * 64) is True

    def test_too_short(self):
        assert self._s("a" * 63) is False

    def test_too_long(self):
        assert self._s("a" * 65) is False

    def test_non_hex_char(self):
        assert self._s("g" * 64) is False

    def test_none_returns_false(self):
        assert self._s(None) is False

    def test_uppercase_hex_invalid(self):
        # The check is lowercase only
        assert self._s("A" * 64) is False


# ---------------------------------------------------------------------------
# util.audit_log: _clean_str
# ---------------------------------------------------------------------------

class TestAuditLogCleanStr:
    def _c(self, v):
        from src.util.audit_log import _clean_str
        return _clean_str(v)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_returns_none(self):
        assert self._c("") is None

    def test_whitespace_returns_none(self):
        assert self._c("   ") is None

    def test_strips_and_returns(self):
        assert self._c("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert self._c(42) == "42"
