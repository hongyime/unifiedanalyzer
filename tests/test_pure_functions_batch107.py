"""
Pure-function tests — batch 107.

Covers:
- pipeline.incremental_runner: _coverage_payload
- pipeline.text_normalizer: _select_metadata, _append_text, _append_metadata_text
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.incremental_runner: _coverage_payload
# ---------------------------------------------------------------------------

class TestCoveragePayload:
    def _p(self, stats, status="ok", error=None):
        from src.pipeline.incremental_runner import _coverage_payload
        return _coverage_payload(stats, status, error)

    def test_empty_stats_returns_empty_list(self):
        assert self._p({}) == []

    def test_top_unresolved_json_returned(self):
        result = self._p({"top_unresolved_json": [{"id": "x"}]})
        assert result == [{"id": "x"}]

    def test_top_unresolved_returned(self):
        result = self._p({"top_unresolved": [1, 2, 3]})
        assert result == [1, 2, 3]

    def test_skipped_key_returned(self):
        result = self._p({"skipped": "no_data"})
        assert result == "no_data"

    def test_failed_with_error_returns_error_dict(self):
        result = self._p({}, status="failed", error="timeout")
        assert result == {"error": "timeout"}

    def test_failed_without_error_returns_empty(self):
        assert self._p({}, status="failed") == []

    def test_first_non_empty_key_wins(self):
        # top_unresolved_json comes before top_unresolved
        result = self._p({
            "top_unresolved_json": [{"id": "x"}],
            "top_unresolved": [1, 2]
        })
        assert result == [{"id": "x"}]


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: _select_metadata
# ---------------------------------------------------------------------------

class TestSelectMetadata:
    def _s(self, metadata):
        from src.pipeline.text_normalizer import _select_metadata
        return _select_metadata(metadata)

    def test_empty_metadata_returns_empty(self):
        assert self._s({}) == {}

    def test_none_value_excluded(self):
        result = self._s({"caption": None, "title": "Hello"})
        assert "caption" not in result

    def test_empty_string_excluded(self):
        result = self._s({"caption": "", "title": "Hello"})
        assert "caption" not in result

    def test_empty_list_excluded(self):
        result = self._s({"caption": [], "title": "Hello"})
        assert "caption" not in result

    def test_empty_dict_excluded(self):
        result = self._s({"metadata": {}, "title": "Hello"})
        assert "metadata" not in result

    def test_known_key_with_value_included(self):
        from src.pipeline.text_normalizer import _TEXT_METADATA_KEYS
        if _TEXT_METADATA_KEYS:
            key = next(iter(_TEXT_METADATA_KEYS))
            result = self._s({key: "test value"})
            assert key in result
            assert result[key] == "test value"

    def test_unknown_key_excluded(self):
        result = self._s({"unknown_xyz_key_never_exists": "value"})
        assert "unknown_xyz_key_never_exists" not in result


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: _append_text
# ---------------------------------------------------------------------------

class TestAppendText:
    def _a(self, parts, value):
        from src.pipeline.text_normalizer import _append_text
        _append_text(parts, value)

    def test_none_not_appended(self):
        parts = []
        self._a(parts, None)
        assert parts == []

    def test_empty_string_not_appended(self):
        parts = []
        self._a(parts, "")
        assert parts == []

    def test_whitespace_only_not_appended(self):
        parts = []
        self._a(parts, "   ")
        assert parts == []

    def test_text_appended_stripped(self):
        parts = []
        self._a(parts, "  hello  ")
        assert parts == ["hello"]

    def test_non_string_coerced(self):
        parts = []
        self._a(parts, 42)
        assert parts == ["42"]


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: _append_metadata_text
# ---------------------------------------------------------------------------

class TestAppendMetadataText:
    def _a(self, parts, value):
        from src.pipeline.text_normalizer import _append_metadata_text
        _append_metadata_text(parts, value)

    def test_none_not_appended(self):
        parts = []
        self._a(parts, None)
        assert parts == []

    def test_string_appended(self):
        parts = []
        self._a(parts, "hello")
        assert "hello" in parts

    def test_list_of_strings_appended(self):
        parts = []
        self._a(parts, ["hello", "world"])
        assert "hello" in parts
        assert "world" in parts

    def test_dict_values_appended(self):
        parts = []
        self._a(parts, {"k1": "hello", "k2": "world"})
        assert "hello" in parts or "world" in parts

    def test_empty_list_not_appended(self):
        parts = []
        self._a(parts, [])
        assert parts == []

    def test_nested_list_appended(self):
        parts = []
        self._a(parts, [["hello", "world"]])
        assert "hello" in parts or "world" in parts
