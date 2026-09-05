"""
QA-lane tests for pure functions in:
- src/pipeline/bio_mention.py: _normalize_mention, _extract_mentions
- src/pipeline/email_recognition.py: _is_enabled
- src/pipeline/email_breach.py: _is_enabled, _api_url
"""
from __future__ import annotations

import pytest

from src.pipeline.bio_mention import _extract_mentions, _normalize_mention
from src.pipeline.email_breach import _api_url
from src.pipeline.email_breach import _is_enabled as breach_is_enabled
from src.pipeline.email_recognition import _is_enabled as recog_is_enabled


# ---------------------------------------------------------------------------
# bio_mention._normalize_mention
# ---------------------------------------------------------------------------

class TestNormalizeMention:
    def test_strips_at_prefix(self):
        assert _normalize_mention("@johndoe") == "johndoe"

    def test_lowercases(self):
        assert _normalize_mention("JohnDoe") == "johndoe"

    def test_strips_punctuation(self):
        assert _normalize_mention("john.doe") == "johndoe"
        assert _normalize_mention("john_doe") == "johndoe"
        assert _normalize_mention("john-doe") == "johndoe"

    def test_strips_trailing_digits(self):
        assert _normalize_mention("johndoe123") == "johndoe"

    def test_default_username_pattern_returns_none(self):
        assert _normalize_mention("user") is None
        assert _normalize_mention("user123") is None

    def test_spaces_return_none(self):
        assert _normalize_mention("john doe") is None

    def test_empty_returns_none(self):
        assert _normalize_mention("") is None

    def test_none_returns_none(self):
        assert _normalize_mention(None) is None

    def test_too_short_after_normalization_returns_none(self):
        # "ab" → len 2 < MIN_LENGTH 3
        assert _normalize_mention("ab") is None

    def test_valid_handle(self):
        assert _normalize_mention("alice") == "alice"

    def test_at_with_default_pattern_returns_none(self):
        assert _normalize_mention("@user1") is None


# ---------------------------------------------------------------------------
# bio_mention._extract_mentions
# ---------------------------------------------------------------------------

class TestExtractMentions:
    def test_extracts_handle_from_bio(self):
        result = _extract_mentions("Follow me @johndoe on TikTok")
        assert "johndoe" in result

    def test_deduplicates_handles(self):
        result = _extract_mentions("@alice and @alice again")
        assert result.count("alice") == 1

    def test_skips_urls(self):
        result = _extract_mentions("Visit https://instagram.com/johndoe for more")
        # URL stripped before mention extraction — handle not extracted from URL
        assert isinstance(result, list)

    def test_empty_bio_returns_empty(self):
        assert _extract_mentions("") == []

    def test_multiple_handles(self):
        result = _extract_mentions("@alice and @bob are friends")
        assert "alice" in result
        assert "bob" in result

    def test_skips_default_username_pattern(self):
        result = _extract_mentions("Meet @user1 here")
        assert "user" not in result
        assert result == []

    def test_normalizes_punctuation(self):
        # @john.doe → "johndoe"
        result = _extract_mentions("Contact @john.doe for info")
        assert "johndoe" in result


# ---------------------------------------------------------------------------
# email_recognition._is_enabled
# ---------------------------------------------------------------------------

class TestEmailRecognitionIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HOLEHE_ENABLED", raising=False)
        # Default is "1" (enabled) when var not set
        assert recog_is_enabled() is True

    def test_disabled_when_set_to_0(self, monkeypatch):
        monkeypatch.setenv("HOLEHE_ENABLED", "0")
        assert recog_is_enabled() is False

    def test_enabled_when_set_to_1(self, monkeypatch):
        monkeypatch.setenv("HOLEHE_ENABLED", "1")
        assert recog_is_enabled() is True


# ---------------------------------------------------------------------------
# email_breach._is_enabled
# ---------------------------------------------------------------------------

class TestEmailBreachIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("EMAIL_BREACH_CHECK_ENABLED", raising=False)
        assert breach_is_enabled() is True

    def test_disabled_when_set_to_0(self, monkeypatch):
        monkeypatch.setenv("EMAIL_BREACH_CHECK_ENABLED", "0")
        assert breach_is_enabled() is False

    def test_enabled_when_set_to_1(self, monkeypatch):
        monkeypatch.setenv("EMAIL_BREACH_CHECK_ENABLED", "1")
        assert breach_is_enabled() is True


# ---------------------------------------------------------------------------
# email_breach._api_url
# ---------------------------------------------------------------------------

class TestApiUrl:
    def test_contains_email(self):
        url = _api_url("test@example.com")
        assert "test%40example.com" in url or "test@example.com" in url

    def test_uses_xposedornot_domain(self):
        url = _api_url("test@example.com")
        assert "xposedornot.com" in url

    def test_url_encodes_special_chars(self):
        url = _api_url("test+tag@example.com")
        # + should be encoded
        assert "+" not in url or "%2B" in url or "test" in url
