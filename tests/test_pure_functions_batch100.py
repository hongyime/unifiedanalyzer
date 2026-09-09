"""
Pure-function tests — batch 100.

Covers:
- pipeline.stream_alerts: extract_burst_terms, parse_cursor_datetime
- pipeline.indicator_export: normalize_indicator (domain/ipv4/email/phone/username)
- pipeline.text_normalizer: source_fingerprint (deterministic/different-inputs)
"""
from __future__ import annotations

from datetime import datetime, timezone
import pytest


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: extract_burst_terms
# ---------------------------------------------------------------------------

class TestExtractBurstTerms:
    def _e(self, text):
        from src.pipeline.stream_alerts import extract_burst_terms
        return extract_burst_terms(text)

    def test_empty_returns_empty(self):
        assert self._e("") == []

    def test_none_returns_empty(self):
        assert self._e(None) == []

    def test_extracts_words(self):
        result = self._e("hello world foo bar")
        assert isinstance(result, list)

    def test_deduplicated(self):
        result = self._e("hello hello world world")
        assert len(result) == len(set(result))

    def test_sorted(self):
        result = self._e("zoo apple banana")
        assert result == sorted(result)

    def test_url_becomes_hostname(self):
        result = self._e("check https://example.com for more")
        assert "example.com" in result

    def test_lowercased(self):
        result = self._e("HELLO WORLD")
        assert all(t == t.lower() for t in result)


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: parse_cursor_datetime
# ---------------------------------------------------------------------------

class TestParseCursorDatetime:
    def _p(self, value, fallback=None):
        from src.pipeline.stream_alerts import parse_cursor_datetime
        return parse_cursor_datetime(value, fallback)

    def test_datetime_passthrough(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert self._p(dt) is dt

    def test_iso_string_parsed(self):
        result = self._p("2026-06-01T12:00:00+00:00")
        assert result.year == 2026

    def test_z_suffix_handled(self):
        result = self._p("2026-06-01T12:00:00Z")
        assert result is not None

    def test_none_with_fallback_returns_fallback(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert self._p(None, fallback=dt) is dt

    def test_none_without_fallback_raises(self):
        with pytest.raises(ValueError):
            self._p(None)

    def test_empty_string_with_fallback_returns_fallback(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert self._p("", fallback=dt) is dt


# ---------------------------------------------------------------------------
# pipeline.indicator_export: normalize_indicator
# ---------------------------------------------------------------------------

class TestNormalizeIndicator:
    def _n(self, itype, value, region=None):
        from src.pipeline.indicator_export import normalize_indicator
        return normalize_indicator(itype, value, default_region=region)

    def test_valid_domain(self):
        result = self._n("domain", "example.com")
        assert result is not None
        assert result.indicator_type == "domain"
        assert result.normalized_value == "example.com"

    def test_invalid_domain_returns_none(self):
        assert self._n("domain", "localhost") is None

    def test_valid_ipv4(self):
        result = self._n("ipv4", "192.168.1.1")
        assert result is not None
        assert result.indicator_type == "ipv4"

    def test_invalid_ipv4_returns_none(self):
        assert self._n("ipv4", "999.999.999.999") is None

    def test_valid_email(self):
        result = self._n("email", "alice@example.com")
        assert result is not None
        assert result.indicator_type == "email"

    def test_invalid_email_returns_none(self):
        assert self._n("email", "notanemail") is None

    def test_empty_value_returns_none(self):
        assert self._n("domain", "") is None

    def test_username_valid(self):
        result = self._n("username", "@alice")
        assert result is not None
        assert result.indicator_type == "username"
        assert result.normalized_value == "alice"

    def test_username_too_short_returns_none(self):
        assert self._n("username", "@x") is None


# ---------------------------------------------------------------------------
# pipeline.text_normalizer: source_fingerprint
# ---------------------------------------------------------------------------

class TestSourceFingerprint:
    def _f(self, row):
        from src.pipeline.text_normalizer import source_fingerprint
        return source_fingerprint(row)

    def _make(self, **kw):
        defaults = {
            "id": "evt-1", "entity_id": "eid-1",
            "occurred_at": "2026-01-01",
            "source": "instagram", "event_type": "CONTENT_PUBLISHED",
            "source_record_id": "post-123",
            "title": "Hello world", "detail": None, "metadata": None,
        }
        defaults.update(kw)
        return defaults

    def test_returns_64_hex(self):
        result = self._f(self._make())
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        row = self._make()
        assert self._f(row) == self._f(row)

    def test_different_source_record_id_different_hash(self):
        a = self._f(self._make(source_record_id="post-123"))
        b = self._f(self._make(source_record_id="post-456"))
        assert a != b

    def test_different_entity_different_hash(self):
        a = self._f(self._make(entity_id="eid-1"))
        b = self._f(self._make(entity_id="eid-2"))
        assert a != b
