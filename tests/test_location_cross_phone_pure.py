"""
QA-lane tests for pure functions in:
- src/pipeline/location_inference.py: _decode_meta, _parse_strava_timezone,
  _parse_latlng, _latlng_to_region
- src/pipeline/cross_source_signals.py: _is_identity_domain, _domain
- src/pipeline/phone_enrichment.py: _is_enabled, _parse_phone
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# location_inference
# ---------------------------------------------------------------------------

from src.pipeline.location_inference import (
    _decode_meta,
    _latlng_to_region,
    _parse_latlng,
    _parse_strava_timezone,
)


class TestLocationDecodeMetа:
    def test_dict_passthrough(self):
        assert _decode_meta({"a": 1}) == {"a": 1}

    def test_valid_json_string(self):
        assert _decode_meta('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert _decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert _decode_meta(None) == {}


class TestParseStravaTimezone:
    def test_extracts_timezone_from_parenthesised_format(self):
        # Strava format: "(GMT+08:00) Asia/Singapore"
        result = _parse_strava_timezone("(GMT+08:00) Asia/Singapore")
        assert result == "Asia/Singapore"

    def test_none_returns_none(self):
        assert _parse_strava_timezone(None) is None

    def test_empty_returns_none(self):
        assert _parse_strava_timezone("") is None

    def test_no_closing_paren_returns_none(self):
        # No matching pattern
        assert _parse_strava_timezone("Asia/Singapore") is None

    def test_strips_whitespace(self):
        result = _parse_strava_timezone("(GMT+00:00)  Europe/London  ")
        assert result == "Europe/London"


class TestParseLatlng:
    def test_list_input(self):
        assert _parse_latlng([1.3, 103.8]) == (1.3, 103.8)

    def test_tuple_input(self):
        assert _parse_latlng((1.3, 103.8)) == (1.3, 103.8)

    def test_string_input(self):
        assert _parse_latlng("[1.3, 103.8]") == (1.3, 103.8)

    def test_string_without_brackets(self):
        assert _parse_latlng("1.3, 103.8") == (1.3, 103.8)

    def test_none_returns_none(self):
        assert _parse_latlng(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_latlng("") is None

    def test_empty_bracket_string_returns_none(self):
        assert _parse_latlng("[]") is None

    def test_null_string_returns_none(self):
        assert _parse_latlng("null") is None

    def test_invalid_values_return_none(self):
        assert _parse_latlng(["not", "floats"]) is None

    def test_too_few_elements_returns_none(self):
        assert _parse_latlng([1.0]) is None


class TestLatlngToRegion:
    def test_singapore_is_sea(self):
        # Singapore: 1.35, 103.82
        assert _latlng_to_region(1.35, 103.82) == "SEA"

    def test_london_is_europe(self):
        # London: 51.5, -0.12
        assert _latlng_to_region(51.5, -0.12) == "Europe"

    def test_new_york_is_north_america(self):
        # NYC: 40.7, -74.0
        assert _latlng_to_region(40.7, -74.0) == "North America"

    def test_sydney_is_oceania(self):
        # Sydney: -33.87, 151.21
        assert _latlng_to_region(-33.87, 151.21) == "Oceania"

    def test_nairobi_is_africa(self):
        # Nairobi: -1.29, 36.82
        assert _latlng_to_region(-1.29, 36.82) == "Africa"

    def test_sao_paulo_is_south_america(self):
        # São Paulo: -23.55, -46.63
        assert _latlng_to_region(-23.55, -46.63) == "South America"

    def test_remote_ocean_returns_none(self):
        # Middle of Pacific: 0, -150 — outside all buckets
        result = _latlng_to_region(0.0, -150.0)
        assert result is None


# ---------------------------------------------------------------------------
# cross_source_signals
# ---------------------------------------------------------------------------

from src.pipeline.cross_source_signals import _domain, _is_identity_domain


class TestIsIdentityDomain:
    def test_telegram_is_identity_domain(self):
        assert _is_identity_domain("t.me") is True

    def test_instagram_is_identity_domain(self):
        assert _is_identity_domain("instagram.com") is True

    def test_personal_site_is_not_identity_domain(self):
        assert _is_identity_domain("johnsmith.com") is False

    def test_none_returns_false(self):
        assert _is_identity_domain(None) is False

    def test_empty_returns_false(self):
        assert _is_identity_domain("") is False

    def test_subdomain_of_identity_domain_matches(self):
        # e.g. "www.instagram.com" should match instagram.com
        assert _is_identity_domain("www.instagram.com") is True


class TestDomain:
    def test_extracts_domain_from_https_url(self):
        assert _domain("https://example.com/path") == "example.com"

    def test_strips_www(self):
        # _domain does NOT strip www — it returns the full hostname
        result = _domain("https://www.example.com")
        assert "example.com" in result

    def test_http_url(self):
        assert _domain("http://foo.bar/baz") == "foo.bar"

    def test_none_returns_none(self):
        assert _domain(None) is None

    def test_empty_returns_none(self):
        assert _domain("") is None

    def test_lowercases_domain(self):
        assert _domain("https://EXAMPLE.COM") == "example.com"

    def test_no_scheme_matches(self):
        result = _domain("example.com/path")
        assert result == "example.com"


# ---------------------------------------------------------------------------
# phone_enrichment
# ---------------------------------------------------------------------------

from src.pipeline.phone_enrichment import _is_enabled, _parse_phone


class TestPhoneEnrichmentIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("PHONE_ENRICHMENT_ENABLED", raising=False)
        assert _is_enabled() is True

    def test_enabled_when_set_to_1(self, monkeypatch):
        monkeypatch.setenv("PHONE_ENRICHMENT_ENABLED", "1")
        assert _is_enabled() is True

    def test_disabled_when_set_to_0(self, monkeypatch):
        monkeypatch.setenv("PHONE_ENRICHMENT_ENABLED", "0")
        assert _is_enabled() is False


class TestParsePhone:
    def test_phone_prefix_stripped(self):
        assert _parse_phone("phone:65912345") == "65912345"

    def test_plus_prefix_returned_as_is(self):
        assert _parse_phone("+6591234567") == "+6591234567"

    def test_bare_number_without_prefix_returns_none(self):
        # No "phone:" and no "+" prefix → None
        assert _parse_phone("6591234567") is None

    def test_empty_returns_none(self):
        assert _parse_phone("") is None

    def test_none_returns_none(self):
        assert _parse_phone(None) is None

    def test_phone_prefix_with_spaces_stripped(self):
        assert _parse_phone("phone:  65912345  ") == "65912345"
