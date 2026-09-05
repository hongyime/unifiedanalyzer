"""
QA-lane tests for pure (non-DB) functions in src/pipeline/entity_resolver.py.

Covers:
- normalize_username: stripping, length gate, digit-suffix stripping, common patterns
- normalize_username_strict: keeps trailing digits, same structure otherwise
- name_is_distinctive: token count + length gate
- name_block_keys: first-3-char block keys for fuzzy candidate pruning
- parse_whatsapp_phone: JID parsing
- compute_confidence: signal accumulation, strong-type counting, is_confirmed flag
"""
from __future__ import annotations

import pytest

from src.pipeline.entity_resolver import (
    SignalMatch,
    STRONG_SIGNAL_TYPES,
    compute_confidence,
    name_block_keys,
    name_is_distinctive,
    normalize_username,
    normalize_username_strict,
    parse_whatsapp_phone,
)


# ---------------------------------------------------------------------------
# normalize_username
# ---------------------------------------------------------------------------

class TestNormalizeUsername:
    def test_none_returns_none(self):
        assert normalize_username(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_username("") is None

    def test_strips_punctuation_and_lowercases(self):
        assert normalize_username("John.Smith") == "johnsmith"
        assert normalize_username("jane_doe") == "janedoe"
        assert normalize_username("alice-bob") == "alicebob"

    def test_strips_trailing_digits(self):
        assert normalize_username("hongyime123") == "hongyime"
        assert normalize_username("user42") is None  # after strip: "user" — hits DEFAULT_USERNAME_RE? no, but len check

    def test_default_username_pattern_returns_none(self):
        # "user", "user1", "USER99" all match ^user\d*$
        assert normalize_username("user") is None
        assert normalize_username("user1") is None
        assert normalize_username("USER99") is None

    def test_spaces_return_none(self):
        assert normalize_username("john smith") is None
        assert normalize_username("jane doe") is None
        # Leading/trailing whitespace only — no internal space — is valid
        assert normalize_username(" spaces ") == "spaces"

    def test_too_short_after_normalization_returns_none(self):
        # MIN_NORMALIZED_LENGTH = 3; "ab" is 2 chars
        assert normalize_username("ab") is None
        assert normalize_username("ab123") is None  # strip digits → "ab" → too short

    def test_valid_username_returned(self):
        assert normalize_username("johndoe") == "johndoe"
        assert normalize_username("JohnDoe") == "johndoe"

    def test_punctuation_only_returns_none(self):
        assert normalize_username("...") is None

    def test_digit_strip_leaves_valid_username(self):
        result = normalize_username("bryanseah99")
        assert result == "bryanseah"


# ---------------------------------------------------------------------------
# normalize_username_strict
# ---------------------------------------------------------------------------

class TestNormalizeUsernameStrict:
    def test_keeps_trailing_digits(self):
        result = normalize_username_strict("hongyime123")
        assert result == "hongyime123"

    def test_still_strips_punctuation(self):
        assert normalize_username_strict("john.doe99") == "johndoe99"

    def test_none_returns_none(self):
        assert normalize_username_strict(None) is None

    def test_default_pattern_returns_none(self):
        assert normalize_username_strict("user123") is None

    def test_spaces_return_none(self):
        assert normalize_username_strict("john smith") is None

    def test_strict_differs_from_loose_on_digit_suffix(self):
        loose = normalize_username("user42abc")
        strict = normalize_username_strict("user42abc")
        # loose strips trailing digits: "user42abc" has no trailing digit block — same
        # but for a pure-digit suffix they differ:
        loose2 = normalize_username("handles123")
        strict2 = normalize_username_strict("handles123")
        assert loose2 == "handles"
        assert strict2 == "handles123"


# ---------------------------------------------------------------------------
# name_is_distinctive
# ---------------------------------------------------------------------------

class TestNameIsDistinctive:
    def test_none_returns_false(self):
        assert name_is_distinctive(None) is False

    def test_empty_returns_false(self):
        assert name_is_distinctive("") is False

    def test_single_short_word_returns_false(self):
        # "Mike" — 4 chars, 1 token — fails both length and token gates
        assert name_is_distinctive("Mike") is False

    def test_single_token_too_short(self):
        # MIN_NAME_LENGTH = 5, MIN_NAME_TOKENS = 2
        assert name_is_distinctive("Ann") is False

    def test_full_name_two_tokens_passes(self):
        assert name_is_distinctive("Jane Halloran") is True

    def test_full_name_three_tokens_passes(self):
        assert name_is_distinctive("Bryan Tan Wei") is True

    def test_single_long_word_fails_token_gate(self):
        # "Bartholomew" — long enough but only 1 token
        assert name_is_distinctive("Bartholomew") is False

    def test_two_short_tokens_fails_length_gate(self):
        # "Jo Li" — 5 chars exactly, passes length; 2 tokens, passes token gate
        assert name_is_distinctive("Jo Li") is True  # len("Jo Li") = 5 == MIN_NAME_LENGTH

    def test_below_min_length_fails(self):
        # "Jo L" — 4 chars < MIN_NAME_LENGTH=5
        assert name_is_distinctive("Jo L") is False


# ---------------------------------------------------------------------------
# name_block_keys
# ---------------------------------------------------------------------------

class TestNameBlockKeys:
    def test_returns_first_three_chars_of_each_token(self):
        keys = name_block_keys("Jane Halloran")
        assert "jan" in keys
        assert "hal" in keys

    def test_short_tokens_excluded(self):
        # tokens < 3 chars are skipped
        keys = name_block_keys("Jo Li")
        assert keys == set()  # both tokens are 2 chars

    def test_lowercases(self):
        keys = name_block_keys("JOHN SMITH")
        assert "joh" in keys
        assert "smi" in keys

    def test_three_char_token_included(self):
        keys = name_block_keys("Tom Jones")
        assert "tom" in keys
        assert "jon" in keys

    def test_empty_string_returns_empty_set(self):
        assert name_block_keys("") == set()

    def test_duplicate_prefixes_deduplicated(self):
        # "Smith Smithson" → both give "smi"
        keys = name_block_keys("Smith Smithson")
        assert keys == {"smi"}


# ---------------------------------------------------------------------------
# parse_whatsapp_phone
# ---------------------------------------------------------------------------

class TestParseWhatsappPhone:
    def test_valid_jid_extracts_phone(self):
        assert parse_whatsapp_phone("6591234567@s.whatsapp.net") == "6591234567"

    def test_lid_jid_returns_none(self):
        assert parse_whatsapp_phone("12345@lid") is None

    def test_status_jid_returns_none(self):
        assert parse_whatsapp_phone("status@broadcast") is None

    def test_none_returns_none(self):
        assert parse_whatsapp_phone(None) is None

    def test_non_numeric_part_returns_none(self):
        assert parse_whatsapp_phone("notaphone@s.whatsapp.net") is None

    def test_too_short_number_returns_none(self):
        # fewer than 7 digits
        assert parse_whatsapp_phone("12345@s.whatsapp.net") is None

    def test_too_long_number_returns_none(self):
        # more than 15 digits
        assert parse_whatsapp_phone("1234567890123456@s.whatsapp.net") is None

    def test_exactly_7_digits_passes(self):
        assert parse_whatsapp_phone("1234567@s.whatsapp.net") == "1234567"

    def test_exactly_15_digits_passes(self):
        assert parse_whatsapp_phone("123456789012345@s.whatsapp.net") == "123456789012345"


# ---------------------------------------------------------------------------
# compute_confidence
# ---------------------------------------------------------------------------

def _sig(signal_type: str, confidence: float) -> SignalMatch:
    return SignalMatch(
        signal_type=signal_type,
        source_platform="instagram",
        target_platform="telegram",
        source_record_id="src-1",
        target_record_id="tgt-1",
        value="testvalue",
        confidence=confidence,
    )


class TestComputeConfidence:
    def test_empty_signals_returns_zero(self):
        score, strong_count, confirmed = compute_confidence([])
        assert score == 0.0
        assert strong_count == 0
        assert confirmed is False

    def test_single_strong_signal_confirms(self):
        signals = [_sig("username_exact", 90.0)]
        score, strong_count, confirmed = compute_confidence(signals)
        assert confirmed is True
        assert strong_count == 1
        assert score > 0.0

    def test_weak_signals_only_do_not_confirm(self):
        signals = [
            _sig("real_name_fuzzy", 40.0),
            _sig("username_similar", 35.0),
        ]
        _, strong_count, confirmed = compute_confidence(signals)
        assert confirmed is False
        assert strong_count == 0

    def test_score_is_capped_at_1(self):
        # Pile on signals to overflow max_possible
        signals = [_sig("username_exact", 200.0)] * 10
        score, _, _ = compute_confidence(signals)
        assert score <= 1.0

    def test_multiple_strong_types_deduplicated_in_count(self):
        # Two signals of the same strong type still count as 1 distinct strong type
        signals = [
            _sig("username_exact", 90.0),
            _sig("username_exact", 90.0),
        ]
        _, strong_count, confirmed = compute_confidence(signals)
        assert strong_count == 1

    def test_two_distinct_strong_types_counted(self):
        signals = [
            _sig("username_exact", 90.0),
            _sig("email_match", 90.0),
        ]
        _, strong_count, _ = compute_confidence(signals)
        assert strong_count == 2

    def test_instagram_threads_linked_is_strong(self):
        assert "instagram_threads_linked" in STRONG_SIGNAL_TYPES
        signals = [_sig("instagram_threads_linked", 100.0)]
        _, _, confirmed = compute_confidence(signals)
        assert confirmed is True

    def test_score_proportional_to_signal_sum(self):
        # max_possible = 195.0; two signals of 97.5 each → score = 195/195 = 1.0
        signals = [_sig("username_exact", 97.5), _sig("email_match", 97.5)]
        score, _, _ = compute_confidence(signals)
        assert abs(score - 1.0) < 0.001
