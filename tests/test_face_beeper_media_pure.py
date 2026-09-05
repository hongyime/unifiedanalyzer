"""
QA-lane tests for pure functions in:
- src/pipeline/face_pair_signals.py: _pair_key
- src/pipeline/beeper_bridge.py: _digits, parse_native_sender
- src/pipeline/media_common.py: lookup_entity
- src/pipeline/text_embedder.py: text_sha1
"""
from __future__ import annotations

import hashlib

import pytest

from src.pipeline.face_pair_signals import _pair_key
from src.pipeline.beeper_bridge import _digits, parse_native_sender
from src.pipeline.media_common import lookup_entity
from src.pipeline.text_embedder import text_sha1


# ---------------------------------------------------------------------------
# face_pair_signals._pair_key
# ---------------------------------------------------------------------------

class TestFacePairKey:
    def test_smaller_first(self):
        a = "00000000-0000-0000-0000-000000000001"
        b = "00000000-0000-0000-0000-000000000002"
        assert _pair_key(a, b) == (a, b)
        assert _pair_key(b, a) == (a, b)

    def test_equal_stays_equal(self):
        x = "00000000-0000-0000-0000-000000000001"
        assert _pair_key(x, x) == (x, x)

    def test_commutative(self):
        a, b = "aaa", "bbb"
        assert _pair_key(a, b) == _pair_key(b, a)


# ---------------------------------------------------------------------------
# beeper_bridge._digits
# ---------------------------------------------------------------------------

class TestDigits:
    def test_strips_non_digits(self):
        assert _digits("+65 9123-4567") == "6591234567"

    def test_pure_digits_unchanged(self):
        assert _digits("6591234567") == "6591234567"

    def test_empty_returns_empty(self):
        assert _digits("") == ""

    def test_all_non_digits_returns_empty(self):
        assert _digits("abc-xyz") == ""


# ---------------------------------------------------------------------------
# beeper_bridge.parse_native_sender
# ---------------------------------------------------------------------------

class TestParseNativeSender:
    def test_telegram_sender(self):
        result = parse_native_sender("Telegram", "@telegram_12345:beeper.local", "John")
        assert result is not None
        source, native_id, username, name = result
        assert source == "telegram"
        assert native_id == "12345"

    def test_instagram_sender(self):
        result = parse_native_sender("Instagram", "@instagram_67890:beeper.local", "alice")
        assert result is not None
        assert result[0] == "instagram"
        assert result[1] == "67890"

    def test_whatsapp_lid_with_phone_display(self):
        result = parse_native_sender("WhatsApp", "@whatsapp_lid-99999:beeper.local", "+6591234567")
        assert result is not None
        assert result[0] == "whatsapp"
        assert "6591234567" in result[1]

    def test_whatsapp_lid_non_phone_display(self):
        result = parse_native_sender("WhatsApp", "@whatsapp_lid-99999:beeper.local", "John Smith")
        assert result is not None
        assert result[0] == "__lid__"

    def test_whatsapp_direct(self):
        result = parse_native_sender("WhatsApp", "@whatsapp_6591234567:beeper.local", None)
        assert result is not None
        assert result[0] == "whatsapp"
        assert "6591234567" in result[1]

    def test_unknown_network_returns_none(self):
        assert parse_native_sender("Discord", "some_id", None) is None

    def test_telegram_no_match_returns_none(self):
        assert parse_native_sender("Telegram", "not_a_match", None) is None


# ---------------------------------------------------------------------------
# media_common.lookup_entity
# ---------------------------------------------------------------------------

class TestLookupEntity:
    def _lookup(self):
        return {
            ("instagram", "alice"): "eid-1",
            ("telegram", "12345"): "eid-2",
        }

    def test_exact_match(self):
        assert lookup_entity(self._lookup(), "instagram", "alice") == "eid-1"

    def test_case_insensitive_fallback(self):
        # "Alice" not in lookup, but "alice" is → lowercase fallback
        assert lookup_entity(self._lookup(), "instagram", "Alice") == "eid-1"

    def test_none_entity_id_returns_none(self):
        assert lookup_entity(self._lookup(), "instagram", None) is None

    def test_missing_key_returns_none(self):
        assert lookup_entity(self._lookup(), "github", "bob") is None

    def test_wrong_source_returns_none(self):
        assert lookup_entity(self._lookup(), "github", "alice") is None


# ---------------------------------------------------------------------------
# text_embedder.text_sha1
# ---------------------------------------------------------------------------

class TestTextSha1Embedder:
    def test_known_value(self):
        expected = hashlib.sha1("hello".encode()).hexdigest()
        assert text_sha1("hello") == expected

    def test_empty_string(self):
        expected = hashlib.sha1(b"").hexdigest()
        assert text_sha1("") == expected

    def test_none_treated_as_empty(self):
        assert text_sha1(None) == text_sha1("")

    def test_different_inputs_differ(self):
        assert text_sha1("abc") != text_sha1("def")

    def test_returns_40_char_hex(self):
        assert len(text_sha1("hello")) == 40
