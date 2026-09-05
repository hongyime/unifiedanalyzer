"""
QA-lane tests for pure functions in src/pipeline/account_proximity.py.

Covers:
- _platform: alias normalization, whitespace stripping
- _account: @ stripping for case-normalized platforms
- _reason: None/empty exclusion, type + detail dict
- _split_env_set: CSV/semicolon splitting, lowercasing
- _parse_beeper_native_id: prefix extraction, WhatsApp phone resolution
"""
from __future__ import annotations

import pytest

from src.pipeline.account_proximity import (
    _account,
    _parse_beeper_native_id,
    _platform,
    _reason,
    _split_env_set,
)


# ---------------------------------------------------------------------------
# _platform
# ---------------------------------------------------------------------------

class TestPlatform:
    def test_twitter_aliased_to_x(self):
        assert _platform("twitter") == "x"

    def test_ig_aliased_to_instagram(self):
        assert _platform("ig") == "instagram"

    def test_telegramgo_aliased_to_telegram(self):
        assert _platform("telegramgo") == "telegram"

    def test_whatsappgo_aliased_to_whatsapp(self):
        assert _platform("whatsappgo") == "whatsapp"

    def test_beeper_matrix_aliased(self):
        assert _platform("beeper (matrix)") == "beeper"

    def test_lowercases_input(self):
        assert _platform("Instagram") == "instagram"

    def test_strips_whitespace(self):
        assert _platform("  telegram  ") == "telegram"

    def test_spaces_replaced_with_underscore(self):
        # Any unaliased multi-word value gets spaces → underscores
        result = _platform("my platform")
        assert " " not in result

    def test_none_returns_empty_string(self):
        assert _platform(None) == ""

    def test_unknown_platform_returned_as_is(self):
        assert _platform("strava") == "strava"


# ---------------------------------------------------------------------------
# _account
# ---------------------------------------------------------------------------

class TestAccount:
    def test_github_strips_at_and_lowercases(self):
        assert _account("@JohnDoe", "github") == "johndoe"

    def test_instagram_strips_at_and_lowercases(self):
        assert _account("@Alice", "instagram") == "alice"

    def test_x_strips_at_and_lowercases(self):
        assert _account("@BryanSeah", "x") == "bryanseah"

    def test_telegram_preserves_at_and_case(self):
        # telegram NOT in the at-strip set — @ preserved, case preserved
        assert _account("@UserName", "telegram") == "@UserName"

    def test_whatsapp_preserves_case(self):
        # WhatsApp not in the at-strip set — case preserved, @ not stripped
        result = _account("+6591234567@s.whatsapp.net", "whatsapp")
        assert result == "+6591234567@s.whatsapp.net"

    def test_empty_value_returns_empty(self):
        assert _account("", "github") == ""

    def test_none_returns_empty(self):
        assert _account(None, "github") == ""

    def test_strips_surrounding_whitespace(self):
        assert _account("  johndoe  ", "github") == "johndoe"


# ---------------------------------------------------------------------------
# _reason
# ---------------------------------------------------------------------------

class TestReason:
    def test_basic_reason(self):
        r = _reason("shared_phone", phone="6591234567")
        assert r["type"] == "shared_phone"
        assert r["phone"] == "6591234567"

    def test_none_values_excluded(self):
        r = _reason("test", value=None, other="kept")
        assert "value" not in r
        assert r["other"] == "kept"

    def test_empty_string_excluded(self):
        r = _reason("test", empty="", kept="yes")
        assert "empty" not in r
        assert r["kept"] == "yes"

    def test_empty_list_excluded(self):
        r = _reason("test", lst=[], kept="yes")
        assert "lst" not in r

    def test_empty_dict_excluded(self):
        r = _reason("test", d={}, kept="yes")
        assert "d" not in r

    def test_type_always_present(self):
        r = _reason("my_type")
        assert r["type"] == "my_type"
        assert len(r) == 1


# ---------------------------------------------------------------------------
# _split_env_set
# ---------------------------------------------------------------------------

class TestSplitEnvSet:
    def test_comma_separated(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", "a,b,c")
        result = _split_env_set("TEST_SET")
        assert result == {"a", "b", "c"}

    def test_semicolon_separated(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", "x;y;z")
        result = _split_env_set("TEST_SET")
        assert result == {"x", "y", "z"}

    def test_whitespace_separated(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", "foo bar baz")
        result = _split_env_set("TEST_SET")
        assert result == {"foo", "bar", "baz"}

    def test_lowercased(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", "Alice,BOB")
        result = _split_env_set("TEST_SET")
        assert result == {"alice", "bob"}

    def test_empty_env_returns_empty_set(self, monkeypatch):
        monkeypatch.delenv("TEST_SET", raising=False)
        result = _split_env_set("TEST_SET")
        assert result == set()

    def test_trailing_comma_ignored(self, monkeypatch):
        monkeypatch.setenv("TEST_SET", "a,b,")
        result = _split_env_set("TEST_SET")
        assert "" not in result
        assert result == {"a", "b"}


# ---------------------------------------------------------------------------
# _parse_beeper_native_id
# ---------------------------------------------------------------------------

class TestParseBeeperNativeId:
    def test_instagram_prefix_extracted(self):
        platform, account = _parse_beeper_native_id(
            "instagram", "@instagramgo_johndoe:beeper.com"
        )
        assert platform == "instagram"
        assert account == "johndoe"

    def test_telegram_prefix_extracted(self):
        platform, account = _parse_beeper_native_id(
            "telegram", "@telegramgo_12345:beeper.com"
        )
        assert platform == "telegram"

    def test_whatsapp_uses_phone_from_full_name(self):
        platform, account = _parse_beeper_native_id(
            "whatsapp", "@whatsappgo_12345:beeper.com", full_name="+6591234567"
        )
        assert platform == "whatsapp"
        assert "6591234567" in account

    def test_no_match_falls_back_to_network(self):
        platform, account = _parse_beeper_native_id(
            "discord", "regular_user_id"
        )
        assert platform == "discord"
        assert account == "regular_user_id"

    def test_none_participant_id(self):
        platform, account = _parse_beeper_native_id("telegram", None)
        assert platform == "telegram"
        assert account == ""
