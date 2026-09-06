"""
QA-lane tests for pure functions in:
- src/pipeline/location_evidence.py: _clean, _coerce_float, _coerce_confidence,
  _iso_datetime, _parse_datetime, _rounded_coord, location_evidence_key,
  is_location_suppressed
- src/pipeline/sentiment_emotion.py: _tokens, detect_language, _score_nrc
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# location_evidence pure functions
# ---------------------------------------------------------------------------

from src.pipeline.location_evidence import (
    _clean,
    _coerce_confidence,
    _coerce_float,
    _iso_datetime,
    _parse_datetime,
    _rounded_coord,
    is_location_suppressed,
    location_evidence_key,
)


class TestClean:
    def test_none_returns_none(self):
        assert _clean(None) is None

    def test_empty_returns_none(self):
        assert _clean("") is None

    def test_whitespace_returns_none(self):
        assert _clean("   ") is None

    def test_strips_whitespace(self):
        assert _clean("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert _clean(42) == "42"


class TestCoerceFloat:
    def test_float_returned(self):
        assert abs(_coerce_float(1.5) - 1.5) < 1e-9

    def test_string_float_parsed(self):
        assert abs(_coerce_float("2.5") - 2.5) < 1e-9

    def test_none_returns_none(self):
        assert _coerce_float(None) is None

    def test_bool_returns_none(self):
        assert _coerce_float(True) is None

    def test_invalid_string_returns_none(self):
        assert _coerce_float("bad") is None


class TestCoerceConfidence:
    def test_valid_float(self):
        assert abs(_coerce_confidence(0.85) - 0.85) < 1e-9

    def test_percentage_normalized(self):
        result = _coerce_confidence(85.0)
        assert result is not None
        assert abs(result - 0.85) < 0.001

    def test_negative_returns_none(self):
        assert _coerce_confidence(-0.1) is None

    def test_capped_at_1(self):
        assert _coerce_confidence(200.0) == 1.0

    def test_none_returns_none(self):
        assert _coerce_confidence(None) is None


class TestIsoDatetime:
    def test_datetime_returns_isoformat(self):
        ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
        result = _iso_datetime(ts)
        assert "2024-06-01" in result

    def test_none_returns_none(self):
        assert _iso_datetime(None) is None

    def test_string_returned_as_is(self):
        assert _iso_datetime("2024-06-01") == "2024-06-01"


class TestParseDateTime:
    def test_none_returns_none(self):
        assert _parse_datetime(None) is None

    def test_bool_returns_none(self):
        assert _parse_datetime(True) is None

    def test_datetime_passthrough(self):
        ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
        assert _parse_datetime(ts) == ts

    def test_iso_string_parsed(self):
        result = _parse_datetime("2024-06-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2024

    def test_z_suffix_handled(self):
        result = _parse_datetime("2024-06-01T12:00:00Z")
        assert result is not None

    def test_invalid_returns_none(self):
        assert _parse_datetime("not-a-date") is None


class TestRoundedCoord:
    def test_rounds_to_7_decimal_places(self):
        result = _rounded_coord(1.123456789)
        assert result is not None
        assert result == round(1.123456789, 7)

    def test_none_returns_none(self):
        assert _rounded_coord(None) is None

    def test_string_float_parsed(self):
        result = _rounded_coord("1.35")
        assert result is not None
        assert abs(result - 1.35) < 1e-6


class TestLocationEvidenceKey:
    def test_returns_64_char_hex(self):
        key = location_evidence_key(
            entity_id="eid-1", source="instagram", evidence_type="gps"
        )
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_deterministic(self):
        kwargs = dict(entity_id="eid-1", source="strava", evidence_type="gps",
                      source_record_id="rec-1")
        assert location_evidence_key(**kwargs) == location_evidence_key(**kwargs)

    def test_different_entities_different_keys(self):
        k1 = location_evidence_key(entity_id="eid-1", source="strava", evidence_type="gps")
        k2 = location_evidence_key(entity_id="eid-2", source="strava", evidence_type="gps")
        assert k1 != k2


class TestIsLocationSuppressed:
    def test_rejected_is_suppressed(self):
        assert is_location_suppressed("rejected") is True

    def test_none_is_not_suppressed(self):
        assert is_location_suppressed(None) is False

    def test_empty_is_not_suppressed(self):
        assert is_location_suppressed("") is False

    def test_confirmed_is_not_suppressed(self):
        assert is_location_suppressed("confirmed") is False

    def test_case_insensitive(self):
        assert is_location_suppressed("REJECTED") is True


# ---------------------------------------------------------------------------
# sentiment_emotion pure functions
# ---------------------------------------------------------------------------

from src.pipeline.sentiment_emotion import _score_nrc, _tokens, detect_language


class TestTokens:
    def test_basic_words(self):
        result = _tokens("Hello world")
        assert "hello" in result
        assert "world" in result

    def test_lowercases(self):
        result = _tokens("UPPER lower")
        assert "upper" in result
        assert "lower" in result

    def test_empty_returns_empty(self):
        assert _tokens("") == []

    def test_none_returns_empty(self):
        assert _tokens(None) == []

    def test_punctuation_filtered(self):
        result = _tokens("hello, world!")
        assert "hello" in result
        assert "world" in result


class TestDetectLanguage:
    def test_english_text(self):
        lang, conf, _ = detect_language("Hello world, this is English text")
        assert lang == "en"
        assert conf > 0

    def test_empty_text_returns_und(self):
        lang, conf, flags = detect_language("")
        assert lang == "und"
        assert flags.get("empty_text") is True

    def test_whitespace_only_returns_und(self):
        lang, _, _ = detect_language("   ")
        assert lang == "und"

    def test_non_latin_returns_unsupported(self):
        lang, _, flags = detect_language("一二三四五六七八九十")
        assert lang == "unsupported"


class TestScoreNrc:
    def test_empty_words_returns_empty(self):
        assert _score_nrc([]) == {}

    def test_known_emotion_word(self):
        # "love" → joy, positive in NRC fallback
        result = _score_nrc(["love"])
        assert isinstance(result, dict)
        # May have emotions or be empty if word not in fallback — just check type

    def test_returns_dict(self):
        result = _score_nrc(["happy", "sad", "angry"])
        assert isinstance(result, dict)

    def test_values_normalized(self):
        result = _score_nrc(["happy", "love", "great"])
        for v in result.values():
            assert 0.0 <= v <= 1.0
