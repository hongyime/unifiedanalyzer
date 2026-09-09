"""
Pure-function tests — batch 98.

Covers:
- pipeline.run_interaction_subset: _csv_set (same as run_timeline_subset)
- pipeline.run_timeline_subset: _csv_set
- pipeline.phone_enrichment: _is_enabled, _parse_phone
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# pipeline.run_interaction_subset: _csv_set
# ---------------------------------------------------------------------------

class TestInteractionCsvSet:
    def _s(self, raw):
        from src.pipeline.run_interaction_subset import _csv_set
        return _csv_set(raw)

    def test_none_returns_none(self):
        assert self._s(None) is None

    def test_empty_string_returns_none(self):
        assert self._s("") is None

    def test_whitespace_only_returns_none(self):
        assert self._s("  ") is None

    def test_csv_split(self):
        result = self._s("instagram,telegram,whatsapp")
        assert result == {"instagram", "telegram", "whatsapp"}

    def test_whitespace_stripped(self):
        result = self._s(" instagram , telegram ")
        assert "instagram" in result
        assert "telegram" in result

    def test_single_value(self):
        result = self._s("instagram")
        assert result == {"instagram"}


# ---------------------------------------------------------------------------
# pipeline.run_timeline_subset: _csv_set (same semantics)
# ---------------------------------------------------------------------------

class TestTimelineCsvSet:
    def _s(self, raw):
        from src.pipeline.run_timeline_subset import _csv_set
        return _csv_set(raw)

    def test_none_returns_none(self):
        assert self._s(None) is None

    def test_empty_returns_none(self):
        assert self._s("") is None

    def test_csv_split(self):
        result = self._s("a,b,c")
        assert result == {"a", "b", "c"}

    def test_single_value(self):
        result = self._s("x")
        assert result == {"x"}


# ---------------------------------------------------------------------------
# pipeline.phone_enrichment: _is_enabled
# ---------------------------------------------------------------------------

class TestPhoneEnrichmentIsEnabled:
    def _e(self, value=None):
        from src.pipeline.phone_enrichment import _is_enabled
        key = "PHONE_ENRICHMENT_ENABLED"
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _is_enabled()
        finally:
            os.environ.pop(key, None)

    def test_default_enabled(self):
        assert self._e() is True

    def test_one_enables(self):
        assert self._e("1") is True

    def test_zero_disables(self):
        assert self._e("0") is False

    def test_true_disables(self):
        # only "1" enables; "true" is not in the check
        assert self._e("true") is False


# ---------------------------------------------------------------------------
# pipeline.phone_enrichment: _parse_phone
# ---------------------------------------------------------------------------

class TestParsePhone:
    def _p(self, value_field):
        from src.pipeline.phone_enrichment import _parse_phone
        return _parse_phone(value_field)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_empty_returns_none(self):
        assert self._p("") is None

    def test_phone_prefix_stripped(self):
        assert self._p("phone:+6591234567") == "+6591234567"

    def test_phone_prefix_no_plus(self):
        assert self._p("phone:6591234567") == "6591234567"

    def test_plus_prefix_returned(self):
        assert self._p("+6591234567") == "+6591234567"

    def test_bare_digits_returns_none(self):
        # No "phone:" prefix, no "+" → returns None
        assert self._p("6591234567") is None

    def test_whitespace_stripped(self):
        result = self._p("phone: +6591234567 ")
        assert result == "+6591234567"
