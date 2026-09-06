"""
QA-lane tests for pure helper functions in:
- src/notifications/telegram.py: _preview, _parse_retry_after
- src/notifications/alerts.py: _num, _esc
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# telegram._preview
# ---------------------------------------------------------------------------

from src.notifications.telegram import _parse_retry_after, _preview


class TestPreview:
    def test_short_text_unchanged(self):
        assert _preview("hello world") == "hello world"

    def test_collapses_whitespace(self):
        assert _preview("hello   world") == "hello world"

    def test_truncates_at_limit(self):
        text = "a" * 1500
        result = _preview(text, limit=100)
        assert len(result) <= 100

    def test_default_limit_1000(self):
        text = "a" * 2000
        result = _preview(text)
        assert len(result) <= 1000

    def test_collapses_newlines(self):
        result = _preview("line1\nline2\nline3")
        assert "\n" not in result
        assert "line1" in result

    def test_non_string_coerced(self):
        result = _preview(42)
        assert result == "42"


# ---------------------------------------------------------------------------
# telegram._parse_retry_after
# ---------------------------------------------------------------------------

class TestParseRetryAfter:
    def test_none_returns_none(self):
        assert _parse_retry_after(None) is None

    def test_no_429_returns_none(self):
        assert _parse_retry_after("some other error") is None

    def test_empty_returns_none(self):
        assert _parse_retry_after("") is None

    def test_429_without_retry_info_returns_5(self):
        # 429 present but no extractable retry_after → fallback 5
        assert _parse_retry_after("Error 429 Too Many Requests") == 5

    def test_json_body_with_retry_after(self):
        error = '429 Too Many Requests {"parameters": {"retry_after": 30}}'
        result = _parse_retry_after(error)
        assert result == 30

    def test_text_retry_after_extracted(self):
        error = "429 Too Many Requests retry after 15 seconds"
        result = _parse_retry_after(error)
        assert result == 15

    def test_minimum_1_second(self):
        error = '429 {"parameters": {"retry_after": 0}}'
        result = _parse_retry_after(error)
        assert result is None or result >= 1


# ---------------------------------------------------------------------------
# alerts._num, _esc
# ---------------------------------------------------------------------------

from src.notifications.alerts import _esc, _num


class TestAlertsNum:
    def test_integer(self):
        assert _num(1500) == "1,500"

    def test_zero(self):
        assert _num(0) == "0"

    def test_none(self):
        assert _num(None) == "0"

    def test_invalid(self):
        assert _num("bad") == "0"

    def test_small_integer(self):
        assert _num(42) == "42"


class TestAlertsEsc:
    def test_html_escaped(self):
        result = _esc("<b>bold</b>")
        assert "&lt;" in result
        assert "&gt;" in result

    def test_ampersand_escaped(self):
        assert _esc("a & b") == "a &amp; b"

    def test_plain_text_unchanged(self):
        assert _esc("hello world") == "hello world"

    def test_none_coerced(self):
        result = _esc(None)
        assert result == "None"

    def test_integer_coerced(self):
        assert _esc(42) == "42"
