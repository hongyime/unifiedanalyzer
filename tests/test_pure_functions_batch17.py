"""
Pure-function tests — batch 17.

Covers previously untested modules with no DB or I/O:
- pipeline.face_bridge_audit: _as_list, _iso
- pipeline.location_evidence: _clean, _coerce_float, _coerce_confidence,
  _iso_datetime, _parse_datetime, _rounded_coord, is_location_suppressed,
  location_evidence_key (deterministic / stable)
- pipeline.language_id: _env_float, _token_count
- pipeline.sentiment_emotion: _tokens, detect_language, _score_nrc
- pipeline.timeline_text_features: _env_bool, _env_int
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# face_bridge_audit: _as_list, _iso
# ---------------------------------------------------------------------------

class TestFaceBridgeAuditAsLst:
    def _a(self, v):
        from src.pipeline.face_bridge_audit import _as_list
        return _as_list(v)

    def test_none_returns_empty(self):
        assert self._a(None) == []

    def test_list_converted_to_strings(self):
        assert self._a([1, 2, 3]) == ["1", "2", "3"]

    def test_tuple_converted(self):
        assert self._a((1, 2)) == ["1", "2"]

    def test_set_converted(self):
        result = self._a({42})
        assert result == ["42"]

    def test_scalar_wrapped_in_list(self):
        assert self._a(99) == ["99"]

    def test_none_elements_excluded(self):
        assert self._a([1, None, 3]) == ["1", "3"]


class TestFaceBridgeAuditIso:
    def _i(self, v):
        from src.pipeline.face_bridge_audit import _iso
        return _iso(v)

    def test_datetime_returns_isoformat(self):
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = self._i(dt)
        assert "2026-01-15" in result

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_empty_string_returns_none(self):
        assert self._i("") is None

    def test_string_returns_string(self):
        assert self._i("2026-01-01") == "2026-01-01"


# ---------------------------------------------------------------------------
# location_evidence: pure helpers
# ---------------------------------------------------------------------------

class TestLocationEvidenceClean:
    def _c(self, v):
        from src.pipeline.location_evidence import _clean
        return _clean(v)

    def test_plain_string(self):
        assert self._c("hello") == "hello"

    def test_strips_whitespace(self):
        assert self._c("  hello  ") == "hello"

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_string_returns_none(self):
        assert self._c("") is None

    def test_whitespace_only_returns_none(self):
        assert self._c("   ") is None


class TestCoerceFloat:
    def _c(self, v):
        from src.pipeline.location_evidence import _coerce_float
        return _coerce_float(v)

    def test_float_input(self):
        assert self._c(1.5) == 1.5

    def test_string_float(self):
        assert self._c("3.14") == 3.14

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_bool_returns_none(self):
        assert self._c(True) is None

    def test_invalid_string_returns_none(self):
        assert self._c("bad") is None


class TestCoerceConfidence:
    def _c(self, v):
        from src.pipeline.location_evidence import _coerce_confidence
        return _coerce_confidence(v)

    def test_valid_zero_to_one(self):
        assert abs(self._c(0.7) - 0.7) < 1e-9

    def test_percentage_divided(self):
        # 75 → 0.75
        assert abs(self._c(75) - 0.75) < 1e-9

    def test_negative_returns_none(self):
        assert self._c(-1) is None

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_capped_at_1(self):
        assert self._c(200) == 1.0


class TestParseDatetime:
    def _p(self, v):
        from src.pipeline.location_evidence import _parse_datetime
        return _parse_datetime(v)

    def test_datetime_passthrough(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert self._p(dt) == dt

    def test_iso_string(self):
        result = self._p("2026-01-15T12:00:00+00:00")
        assert isinstance(result, datetime)

    def test_z_suffix_normalized(self):
        result = self._p("2026-01-15T12:00:00Z")
        assert isinstance(result, datetime)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_invalid_returns_none(self):
        assert self._p("not-a-date") is None

    def test_bool_returns_none(self):
        assert self._p(True) is None


class TestRoundedCoord:
    def _r(self, v):
        from src.pipeline.location_evidence import _rounded_coord
        return _rounded_coord(v)

    def test_rounds_to_7_decimal(self):
        result = self._r(1.23456789012345)
        assert result == round(1.23456789012345, 7)

    def test_none_returns_none(self):
        assert self._r(None) is None

    def test_string_float(self):
        result = self._r("103.8")
        assert abs(result - 103.8) < 1e-6


class TestIsLocationSuppressed:
    def _s(self, v):
        from src.pipeline.location_evidence import is_location_suppressed
        return is_location_suppressed(v)

    def test_rejected_suppressed(self):
        assert self._s("rejected") is True

    def test_suppressed_suppressed(self):
        assert self._s("suppressed") is True

    def test_active_not_suppressed(self):
        assert self._s("active") is False

    def test_none_not_suppressed(self):
        assert self._s(None) is False

    def test_empty_not_suppressed(self):
        assert self._s("") is False

    def test_case_insensitive(self):
        assert self._s("REJECTED") is True


class TestLocationEvidenceKey:
    def test_deterministic(self):
        from src.pipeline.location_evidence import location_evidence_key
        key1 = location_evidence_key(
            entity_id="eid-1", source="strava", evidence_type="route",
            source_record_id="rec123"
        )
        key2 = location_evidence_key(
            entity_id="eid-1", source="strava", evidence_type="route",
            source_record_id="rec123"
        )
        assert key1 == key2

    def test_different_entities_differ(self):
        from src.pipeline.location_evidence import location_evidence_key
        k1 = location_evidence_key(entity_id="a", source="s", evidence_type="t", source_record_id="r")
        k2 = location_evidence_key(entity_id="b", source="s", evidence_type="t", source_record_id="r")
        assert k1 != k2

    def test_returns_64_char_hex(self):
        from src.pipeline.location_evidence import location_evidence_key
        key = location_evidence_key(entity_id="e", source="s", evidence_type="t")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
# language_id: _env_float, _token_count
# ---------------------------------------------------------------------------

class TestLanguageIdEnvFloat:
    def test_reads_float(self, monkeypatch):
        monkeypatch.setenv("_TEST_LID_FLOAT", "0.75")
        from src.pipeline.language_id import _env_float
        assert abs(_env_float("_TEST_LID_FLOAT", 0.5) - 0.75) < 1e-9

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_LID_FLOAT", raising=False)
        from src.pipeline.language_id import _env_float
        assert abs(_env_float("_TEST_LID_FLOAT", 0.3) - 0.3) < 1e-9

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("_TEST_LID_FLOAT", "bad")
        from src.pipeline.language_id import _env_float
        assert abs(_env_float("_TEST_LID_FLOAT", 0.6) - 0.6) < 1e-9


class TestTokenCount:
    def _t(self, text):
        from src.pipeline.language_id import _token_count
        return _token_count(text)

    def test_basic_words(self):
        assert self._t("hello world") == 2

    def test_empty_string(self):
        assert self._t("") == 0

    def test_none(self):
        assert self._t(None) == 0

    def test_punctuation_not_counted(self):
        assert self._t("...") == 0


# ---------------------------------------------------------------------------
# sentiment_emotion: _tokens, detect_language, _score_nrc
# ---------------------------------------------------------------------------

class TestSentimentTokens:
    def _t(self, text):
        from src.pipeline.sentiment_emotion import _tokens
        return _tokens(text)

    def test_basic(self):
        assert self._t("Hello World") == ["hello", "world"]

    def test_empty(self):
        assert self._t("") == []

    def test_none(self):
        assert self._t(None) == []

    def test_lowercases(self):
        tokens = self._t("Python JavaScript")
        assert "python" in tokens


class TestDetectLanguage:
    def _d(self, text):
        from src.pipeline.sentiment_emotion import detect_language
        return detect_language(text)

    def test_empty_returns_und(self):
        lang, conf, flags = self._d("")
        assert lang == "und"

    def test_latin_text_returns_en(self):
        lang, conf, flags = self._d("Hello this is English text")
        assert lang == "en"

    def test_confidence_in_range(self):
        _, conf, _ = self._d("Hello world this is text")
        assert 0.0 <= conf <= 1.0

    def test_whitespace_only_returns_und(self):
        lang, _, _ = self._d("   ")
        assert lang == "und"


class TestScoreNrc:
    def _s(self, words):
        from src.pipeline.sentiment_emotion import _score_nrc
        return _score_nrc(words)

    def test_empty_returns_empty(self):
        assert self._s([]) == {}

    def test_known_emotion_word(self):
        result = self._s(["happy"])
        assert "joy" in result

    def test_multiple_emotions(self):
        result = self._s(["hate"])
        assert "anger" in result or "disgust" in result

    def test_values_between_0_and_1(self):
        result = self._s(["happy", "love", "great"])
        for v in result.values():
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# timeline_text_features: _env_bool, _env_int
# ---------------------------------------------------------------------------

class TestTimelineTextFeaturesEnvBool:
    def test_true_values(self, monkeypatch):
        from src.pipeline.timeline_text_features import _env_bool
        for val in ("1", "true", "yes", "on"):
            monkeypatch.setenv("_TEST_TTF_BOOL", val)
            assert _env_bool("_TEST_TTF_BOOL", False) is True

    def test_false_values(self, monkeypatch):
        from src.pipeline.timeline_text_features import _env_bool
        for val in ("0", "false", "no", "off"):
            monkeypatch.setenv("_TEST_TTF_BOOL", val)
            assert _env_bool("_TEST_TTF_BOOL", True) is False

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_TTF_BOOL", raising=False)
        from src.pipeline.timeline_text_features import _env_bool
        assert _env_bool("_TEST_TTF_BOOL", True) is True
        assert _env_bool("_TEST_TTF_BOOL", False) is False


class TestTimelineTextFeaturesEnvInt:
    def test_reads_int(self, monkeypatch):
        monkeypatch.setenv("_TEST_TTF_INT", "42")
        from src.pipeline.timeline_text_features import _env_int
        assert _env_int("_TEST_TTF_INT", 10) == 42

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_TTF_INT", raising=False)
        from src.pipeline.timeline_text_features import _env_int
        assert _env_int("_TEST_TTF_INT", 99) == 99

    def test_empty_returns_default(self, monkeypatch):
        monkeypatch.setenv("_TEST_TTF_INT", "  ")
        from src.pipeline.timeline_text_features import _env_int
        assert _env_int("_TEST_TTF_INT", 5) == 5
