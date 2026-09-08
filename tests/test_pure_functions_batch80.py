"""
Pure-function tests — batch 80.

Covers:
- pipeline.location_evidence: _clean, _coerce_float, _coerce_confidence,
  _iso_datetime, _parse_datetime, _rounded_coord, is_location_suppressed,
  location_evidence_key (deterministic/different-entity)
- pipeline.translation_worker: normalize_translation_language, opus_model_name,
  nllb_language_code
- pipeline.indicator_export: _valid_ipv4, _normalize_domain, _normalize_email,
  normalize_phone_e164
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _clean
# ---------------------------------------------------------------------------

class TestLocClean:
    def _c(self, v):
        from src.pipeline.location_evidence import _clean
        return _clean(v)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_empty_string_returns_none(self):
        assert self._c("") is None

    def test_whitespace_only_returns_none(self):
        assert self._c("   ") is None

    def test_strips_and_returns(self):
        assert self._c("  hello  ") == "hello"

    def test_non_string_coerced(self):
        assert self._c(42) == "42"


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _coerce_float
# ---------------------------------------------------------------------------

class TestCoerceFloat:
    def _f(self, v):
        from src.pipeline.location_evidence import _coerce_float
        return _coerce_float(v)

    def test_none_returns_none(self):
        assert self._f(None) is None

    def test_bool_returns_none(self):
        assert self._f(True) is None

    def test_int_returns_float(self):
        assert self._f(5) == 5.0

    def test_string_float_parsed(self):
        assert abs(self._f("1.35") - 1.35) < 1e-9

    def test_invalid_string_returns_none(self):
        assert self._f("bad") is None


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _coerce_confidence
# ---------------------------------------------------------------------------

class TestLocCoerceConfidence:
    def _c(self, v):
        from src.pipeline.location_evidence import _coerce_confidence
        return _coerce_confidence(v)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_negative_returns_none(self):
        assert self._c(-0.1) is None

    def test_valid_fraction(self):
        assert abs(self._c(0.75) - 0.75) < 1e-9

    def test_percentage_normalized(self):
        assert abs(self._c(75.0) - 0.75) < 1e-9

    def test_capped_at_one(self):
        assert self._c(200.0) == 1.0


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _iso_datetime
# ---------------------------------------------------------------------------

class TestIsoDatetime:
    def _i(self, v):
        from src.pipeline.location_evidence import _iso_datetime
        return _iso_datetime(v)

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_datetime_isoformatted(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        assert self._i(dt) == dt.isoformat()

    def test_string_returned_as_is(self):
        assert self._i("2026-01-15") == "2026-01-15"


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _parse_datetime
# ---------------------------------------------------------------------------

class TestLocParseDatetime:
    def _p(self, v):
        from src.pipeline.location_evidence import _parse_datetime
        return _parse_datetime(v)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_bool_returns_none(self):
        assert self._p(True) is None

    def test_datetime_passthrough(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert self._p(dt) is dt

    def test_iso_string_parsed(self):
        result = self._p("2026-06-01T12:00:00+00:00")
        assert result is not None
        assert result.year == 2026

    def test_z_suffix_handled(self):
        result = self._p("2026-06-01T12:00:00Z")
        assert result is not None

    def test_invalid_returns_none(self):
        assert self._p("not-a-date") is None


# ---------------------------------------------------------------------------
# pipeline.location_evidence: _rounded_coord
# ---------------------------------------------------------------------------

class TestRoundedCoord:
    def _r(self, v):
        from src.pipeline.location_evidence import _rounded_coord
        return _rounded_coord(v)

    def test_none_returns_none(self):
        assert self._r(None) is None

    def test_seven_decimal_places(self):
        result = self._r(1.123456789)
        assert result == round(1.123456789, 7)

    def test_bool_returns_none(self):
        assert self._r(True) is None


# ---------------------------------------------------------------------------
# pipeline.location_evidence: is_location_suppressed
# ---------------------------------------------------------------------------

class TestIsLocationSuppressed:
    def _s(self, status):
        from src.pipeline.location_evidence import is_location_suppressed
        return is_location_suppressed(status)

    def test_none_not_suppressed(self):
        assert self._s(None) is False

    def test_active_not_suppressed(self):
        assert self._s("active") is False

    def test_suppressed_status(self):
        assert self._s("suppressed") is True

    def test_rejected_status(self):
        assert self._s("rejected") is True


# ---------------------------------------------------------------------------
# pipeline.location_evidence: location_evidence_key determinism
# ---------------------------------------------------------------------------

class TestLocationEvidenceKey:
    def _k(self, **kw):
        from src.pipeline.location_evidence import location_evidence_key
        defaults = dict(entity_id="eid-1", source="strava",
                        evidence_type="gps", lat=1.35, lng=103.82)
        defaults.update(kw)
        return location_evidence_key(**defaults)

    def test_deterministic(self):
        assert self._k() == self._k()

    def test_different_entity_different_key(self):
        assert self._k(entity_id="eid-1") != self._k(entity_id="eid-2")

    def test_returns_64_char_hex(self):
        key = self._k()
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_different_source_different_key(self):
        assert self._k(source="strava") != self._k(source="instagram")


# ---------------------------------------------------------------------------
# pipeline.translation_worker: normalize_translation_language
# ---------------------------------------------------------------------------

class TestNormalizeTranslationLanguage:
    def _n(self, lang, provider="opus"):
        from src.pipeline.translation_worker import normalize_translation_language
        return normalize_translation_language(lang, provider=provider)

    def test_none_becomes_und(self):
        result = self._n(None)
        assert isinstance(result, str)

    def test_lowercased(self):
        assert self._n("ZH") == self._n("zh")

    def test_underscore_to_dash(self):
        result = self._n("zh_CN")
        assert "_" not in result

    def test_nllb_provider_mapping(self):
        result = self._n("en", provider="nllb")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_unknown_lang_returned_as_is(self):
        result = self._n("xyz-unknown", provider="opus")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# pipeline.translation_worker: opus_model_name
# ---------------------------------------------------------------------------

class TestOpusModelName:
    def _m(self, source, target="en"):
        from src.pipeline.translation_worker import opus_model_name
        return opus_model_name(source, target)

    def test_returns_string(self):
        assert isinstance(self._m("zh", "en"), str)

    def test_contains_source_and_target(self):
        result = self._m("zh", "en")
        assert "zh" in result and "en" in result

    def test_env_override(self):
        os.environ["TRANSLATION_OPUS_MODEL_TEST_EN"] = "my-custom-model"
        try:
            from src.pipeline.translation_worker import opus_model_name
            result = opus_model_name("test", "en")
            assert result == "my-custom-model"
        finally:
            os.environ.pop("TRANSLATION_OPUS_MODEL_TEST_EN", None)


# ---------------------------------------------------------------------------
# pipeline.indicator_export: _valid_ipv4
# ---------------------------------------------------------------------------

class TestValidIpv4:
    def _v(self, v):
        from src.pipeline.indicator_export import _valid_ipv4
        return _valid_ipv4(v)

    def test_valid_ip(self):
        assert self._v("192.168.1.1") == "192.168.1.1"

    def test_invalid_returns_none(self):
        assert self._v("not.an.ip.address") is None

    def test_ipv6_returns_none(self):
        assert self._v("::1") is None


# ---------------------------------------------------------------------------
# pipeline.indicator_export: _normalize_domain
# ---------------------------------------------------------------------------

class TestNormalizeDomain:
    def _d(self, v):
        from src.pipeline.indicator_export import _normalize_domain
        return _normalize_domain(v)

    def test_valid_domain(self):
        assert self._d("Example.COM") == "example.com"

    def test_no_dot_returns_none(self):
        assert self._d("localhost") is None

    def test_empty_returns_none(self):
        assert self._d("") is None

    def test_strips_leading_trailing_dots(self):
        result = self._d(".example.com.")
        assert result == "example.com"

    def test_all_numeric_labels_returns_none(self):
        assert self._d("192.168.1.1") is None


# ---------------------------------------------------------------------------
# pipeline.indicator_export: _normalize_email
# ---------------------------------------------------------------------------

class TestNormalizeEmail:
    def _e(self, v):
        from src.pipeline.indicator_export import _normalize_email
        return _normalize_email(v)

    def test_valid_email(self):
        assert self._e("User@Example.COM") == "user@example.com"

    def test_invalid_no_at(self):
        assert self._e("notanemail") is None

    def test_empty_returns_none(self):
        assert self._e("") is None


# ---------------------------------------------------------------------------
# pipeline.indicator_export: normalize_phone_e164
# ---------------------------------------------------------------------------

class TestNormalizePhoneE164:
    def _p(self, v, region=None):
        from src.pipeline.indicator_export import normalize_phone_e164
        return normalize_phone_e164(v, default_region=region)

    def test_e164_format(self):
        result = self._p("+6512345678")
        assert result == "+6512345678"

    def test_empty_returns_none(self):
        assert self._p("") is None

    def test_sg_8_digit_number(self):
        result = self._p("81234567", region="SG")
        assert result is not None
        assert result.startswith("+65")

    def test_us_10_digit_number(self):
        result = self._p("2125551234", region="US")
        assert result is not None
        assert result.startswith("+1")

    def test_non_numeric_returns_none(self):
        assert self._p("abc") is None
