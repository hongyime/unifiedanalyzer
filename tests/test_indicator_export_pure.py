"""
QA-lane tests for pure functions in src/pipeline/indicator_export.py.

Covers:
- _valid_ipv4: valid/invalid IPv4 addresses
- _normalize_domain: stripping, dot-only, IP-like, long labels
- _normalize_email: valid/invalid email normalization
- normalize_phone_e164: +prefix, SG region, US region, digit-count gating
- normalize_indicator: type dispatch, confidence, None on bad input
- extract_indicators_from_text: email/IP/phone extraction from text
"""
from __future__ import annotations

import pytest

from src.pipeline.indicator_export import (
    NormalizedIndicator,
    _normalize_domain,
    _normalize_email,
    _valid_ipv4,
    extract_indicators_from_text,
    normalize_indicator,
    normalize_phone_e164,
)


# ---------------------------------------------------------------------------
# _valid_ipv4
# ---------------------------------------------------------------------------

class TestValidIpv4:
    def test_valid_ip(self):
        assert _valid_ipv4("192.168.1.1") == "192.168.1.1"

    def test_loopback(self):
        assert _valid_ipv4("127.0.0.1") == "127.0.0.1"

    def test_invalid_returns_none(self):
        assert _valid_ipv4("999.999.999.999") is None

    def test_hostname_returns_none(self):
        assert _valid_ipv4("example.com") is None

    def test_partial_returns_none(self):
        assert _valid_ipv4("192.168.1") is None

    def test_empty_returns_none(self):
        assert _valid_ipv4("") is None

    def test_ipv6_returns_none(self):
        assert _valid_ipv4("::1") is None


# ---------------------------------------------------------------------------
# _normalize_domain
# ---------------------------------------------------------------------------

class TestNormalizeDomain:
    def test_valid_domain(self):
        assert _normalize_domain("example.com") == "example.com"

    def test_lowercases(self):
        assert _normalize_domain("Example.COM") == "example.com"

    def test_strips_leading_trailing_dots(self):
        assert _normalize_domain(".example.com.") == "example.com"

    def test_no_dot_returns_none(self):
        assert _normalize_domain("localhost") is None

    def test_empty_returns_none(self):
        assert _normalize_domain("") is None

    def test_pure_ip_returns_none(self):
        # All-numeric labels → treated as IP, not domain
        assert _normalize_domain("192.168.1.1") is None

    def test_valid_subdomain(self):
        assert _normalize_domain("api.example.com") == "api.example.com"

    def test_strips_whitespace(self):
        assert _normalize_domain("  example.com  ") == "example.com"


# ---------------------------------------------------------------------------
# _normalize_email
# ---------------------------------------------------------------------------

class TestNormalizeEmail:
    def test_valid_email(self):
        assert _normalize_email("alice@example.com") == "alice@example.com"

    def test_lowercases(self):
        assert _normalize_email("ALICE@EXAMPLE.COM") == "alice@example.com"

    def test_no_at_returns_none(self):
        assert _normalize_email("notanemail") is None

    def test_no_domain_returns_none(self):
        assert _normalize_email("alice@") is None

    def test_empty_returns_none(self):
        assert _normalize_email("") is None

    def test_strips_whitespace(self):
        assert _normalize_email("  alice@example.com  ") == "alice@example.com"


# ---------------------------------------------------------------------------
# normalize_phone_e164
# ---------------------------------------------------------------------------

class TestNormalizePhoneE164:
    def test_plus_prefix_preserved(self):
        result = normalize_phone_e164("+6591234567")
        assert result == "+6591234567"

    def test_sg_region_8_digit_starting_9(self, monkeypatch):
        monkeypatch.setenv("ANALYZER_DEFAULT_PHONE_REGION", "SG")
        result = normalize_phone_e164("91234567", default_region="SG")
        assert result == "+6591234567"

    def test_sg_region_8_digit_starting_8(self):
        result = normalize_phone_e164("81234567", default_region="SG")
        assert result == "+6581234567"

    def test_us_region_10_digit(self):
        result = normalize_phone_e164("2125551234", default_region="US")
        assert result == "+12125551234"

    def test_11_digit_no_region_gets_plus(self):
        result = normalize_phone_e164("65912345678")
        assert result is not None
        assert result.startswith("+")

    def test_empty_returns_none(self):
        assert normalize_phone_e164("") is None

    def test_too_short_returns_none(self):
        # 6 digits, no region match
        assert normalize_phone_e164("123456", default_region="US") is None

    def test_strips_separators(self):
        result = normalize_phone_e164("+65 9123-4567")
        assert result == "+6591234567"


# ---------------------------------------------------------------------------
# normalize_indicator
# ---------------------------------------------------------------------------

class TestNormalizeIndicator:
    def test_domain_type(self):
        ind = normalize_indicator("domain", "example.com")
        assert ind is not None
        assert ind.indicator_type == "domain"
        assert ind.normalized_value == "example.com"

    def test_ipv4_type(self):
        ind = normalize_indicator("ip", "1.2.3.4")
        assert ind is not None
        assert ind.indicator_type == "ipv4"
        assert ind.confidence == 0.8

    def test_email_type(self):
        ind = normalize_indicator("email", "alice@example.com")
        assert ind is not None
        assert ind.indicator_type == "email"
        assert ind.confidence == 0.8

    def test_phone_type(self):
        ind = normalize_indicator("phone", "+6591234567")
        assert ind is not None
        assert ind.indicator_type == "phone_e164"
        assert ind.confidence == 0.8

    def test_username_type(self):
        ind = normalize_indicator("username", "@johndoe")
        assert ind is not None
        assert ind.indicator_type == "username"
        assert ind.normalized_value == "johndoe"

    def test_full_name_type(self):
        ind = normalize_indicator("full_name", '"Jane Smith"')
        assert ind is not None
        assert ind.indicator_type == "full_name"
        assert "Jane" in ind.display_value

    def test_invalid_type_returns_none(self):
        assert normalize_indicator("nonsense", "value") is None

    def test_empty_value_returns_none(self):
        assert normalize_indicator("email", "") is None
        assert normalize_indicator("email", None) is None

    def test_invalid_ipv4_returns_none(self):
        assert normalize_indicator("ip", "999.999.999.999") is None

    def test_single_name_returns_none(self):
        # full_name requires a space
        assert normalize_indicator("full_name", "Alice") is None


# ---------------------------------------------------------------------------
# extract_indicators_from_text
# ---------------------------------------------------------------------------

class TestExtractIndicatorsFromText:
    def test_none_returns_empty(self):
        assert extract_indicators_from_text(None) == []

    def test_empty_returns_empty(self):
        assert extract_indicators_from_text("") == []

    def test_extracts_email(self):
        result = extract_indicators_from_text("Contact alice@example.com for info")
        types = [i.indicator_type for i in result]
        assert "email" in types

    def test_extracts_ipv4(self):
        result = extract_indicators_from_text("Server at 192.168.1.100 is down")
        types = [i.indicator_type for i in result]
        assert "ipv4" in types

    def test_deduplicates_same_indicator(self):
        result = extract_indicators_from_text("alice@example.com and alice@example.com")
        emails = [i for i in result if i.indicator_type == "email"]
        assert len(emails) == 1

    def test_multiple_types(self):
        result = extract_indicators_from_text("Email alice@example.com, IP 1.2.3.4")
        types = {i.indicator_type for i in result}
        assert "email" in types
        assert "ipv4" in types
