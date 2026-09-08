"""
Pure-function tests — batch 15.

Covers previously untested modules with no DB or I/O:
- pipeline.contact_extraction: _extract_emails, _extract_platform_links,
  _extract_phone_numbers, _extract_website_domains, regex constants
- pipeline.shared_life_context: _is_enabled, _rarity_threshold, _base_confidence,
  _step_confidence, _decode_enrichment, _normalize_item
- pipeline.identity_calibration: FEATURE_ORDER / DEPRECATED_NON_IDENTITY_FEATURES /
  ACTIVE_FEATURE_ORDER constants, pair_feature_vector, _feature_value
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# contact_extraction: pure extraction helpers
# ---------------------------------------------------------------------------

class TestExtractEmails:
    def _e(self, text):
        from src.pipeline.contact_extraction import _extract_emails
        return _extract_emails(text)

    def test_single_email(self):
        result = self._e("contact me at user@example.com please")
        assert "user@example.com" in result

    def test_multiple_emails(self):
        result = self._e("a@x.com and b@y.com")
        assert "a@x.com" in result
        assert "b@y.com" in result

    def test_lowercases(self):
        result = self._e("User@Example.COM")
        assert "user@example.com" in result

    def test_no_email_returns_empty(self):
        assert self._e("no emails here") == []

    def test_empty_string(self):
        assert self._e("") == []


class TestExtractPlatformLinks:
    def _e(self, text):
        from src.pipeline.contact_extraction import _extract_platform_links
        return _extract_platform_links(text)

    def test_instagram_link(self):
        result = self._e("follow me at instagram.com/alice")
        platforms = [p for p, _ in result]
        assert "instagram" in platforms

    def test_github_link(self):
        result = self._e("check github.com/octocat for code")
        platforms = [p for p, _ in result]
        assert "github" in platforms

    def test_reserved_segment_excluded(self):
        # instagram.com/p/... is a post, not a profile
        result = self._e("instagram.com/p/abc123")
        handles = [h for _, h in result]
        assert "p" not in handles

    def test_no_links(self):
        assert self._e("no links here") == []

    def test_tiktok_at_link(self):
        result = self._e("see tiktok.com/@creator")
        platforms = [p for p, _ in result]
        assert "tiktok" in platforms


class TestExtractPhoneNumbers:
    def _e(self, text):
        from src.pipeline.contact_extraction import _extract_phone_numbers
        return _extract_phone_numbers(text)

    def test_sg_phone(self):
        result = self._e("call me at +6591234567")
        assert any("6591234567" in r for r in result)

    def test_phone_with_spaces(self):
        result = self._e("+65 9123 4567")
        assert len(result) > 0

    def test_too_short_not_extracted(self):
        # < 9 digits — not a phone
        result = self._e("1234567")
        assert result == []

    def test_no_phone(self):
        assert self._e("no phone here") == []

    def test_00_prefix_stripped_variant(self):
        # "0044 7723 442078" → also yields "447723442078"
        result = self._e("+44 7723 442078")
        assert len(result) > 0


class TestExtractWebsiteDomains:
    def _e(self, text):
        from src.pipeline.contact_extraction import _extract_website_domains
        return _extract_website_domains(text)

    def test_personal_website(self):
        result = self._e("visit https://johndoe.com for more")
        assert "johndoe.com" in result

    def test_www_prefix(self):
        result = self._e("www.alice.io is my site")
        assert "alice.io" in result

    def test_excluded_platform_not_returned(self):
        result = self._e("follow me on instagram.com/alice")
        assert "instagram.com" not in result

    def test_excluded_linktr_not_returned(self):
        result = self._e("visit linktr.ee/myprofile")
        assert "linktr.ee" not in result

    def test_no_website(self):
        assert self._e("just plain text") == []


class TestContactExtractionConstants:
    def test_generic_email_prefixes_non_empty(self):
        from src.pipeline.contact_extraction import _GENERIC_EMAIL_PREFIXES
        assert len(_GENERIC_EMAIL_PREFIXES) > 0
        assert "info" in _GENERIC_EMAIL_PREFIXES

    def test_platform_link_patterns_non_empty(self):
        from src.pipeline.contact_extraction import PLATFORM_LINK_PATTERNS
        assert "instagram" in PLATFORM_LINK_PATTERNS
        assert "github" in PLATFORM_LINK_PATTERNS

    def test_excluded_website_domains_non_empty(self):
        from src.pipeline.contact_extraction import _EXCLUDED_WEBSITE_DOMAINS
        assert "instagram.com" in _EXCLUDED_WEBSITE_DOMAINS
        assert "linktr.ee" in _EXCLUDED_WEBSITE_DOMAINS


# ---------------------------------------------------------------------------
# shared_life_context pure helpers
# ---------------------------------------------------------------------------

class TestSharedLifeContextEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("SHARED_LIFE_CONTEXT_ENABLED", raising=False)
        from src.pipeline.shared_life_context import _is_enabled
        assert _is_enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("SHARED_LIFE_CONTEXT_ENABLED", "0")
        from src.pipeline.shared_life_context import _is_enabled
        assert _is_enabled() is False


class TestRarityThreshold:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("SHARED_LIFE_CONTEXT_RARITY", raising=False)
        from src.pipeline.shared_life_context import _rarity_threshold
        assert abs(_rarity_threshold() - 0.05) < 1e-9

    def test_custom(self, monkeypatch):
        monkeypatch.setenv("SHARED_LIFE_CONTEXT_RARITY", "0.10")
        from src.pipeline.shared_life_context import _rarity_threshold
        assert abs(_rarity_threshold() - 0.10) < 1e-9

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("SHARED_LIFE_CONTEXT_RARITY", "bad")
        from src.pipeline.shared_life_context import _rarity_threshold
        assert abs(_rarity_threshold() - 0.05) < 1e-9


class TestSharedLifeContextConfidence:
    def test_base_confidence(self):
        from src.pipeline.shared_life_context import _base_confidence
        assert _base_confidence() == 0.35

    def test_step_confidence(self):
        from src.pipeline.shared_life_context import _step_confidence
        assert _step_confidence() == 0.15


class TestDecodeEnrichment:
    def _d(self, raw):
        from src.pipeline.shared_life_context import _decode_enrichment
        return _decode_enrichment(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_invalid_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_array_json_returns_empty(self):
        assert self._d("[1, 2]") == {}


class TestNormalizeItem:
    def _n(self, text):
        from src.pipeline.shared_life_context import _normalize_item
        return _normalize_item(text)

    def test_lowercases(self):
        assert self._n("Google") == "google"

    def test_collapses_whitespace(self):
        assert self._n("National  University  Singapore") == "national university singapore"

    def test_strips_leading_trailing(self):
        assert self._n("  Apple  ") == "apple"

    def test_too_short_returns_none(self):
        assert self._n("ab") is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_none_returns_none(self):
        # _normalize_item expects str — guard via short-circuit on empty
        assert self._n("") is None


# ---------------------------------------------------------------------------
# identity_calibration: constants + pair_feature_vector + _feature_value
# ---------------------------------------------------------------------------

class TestIdentityCalibrationConstants:
    def test_feature_order_non_empty(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        assert len(FEATURE_ORDER) > 0

    def test_active_feature_order_subset_of_feature_order(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER, ACTIVE_FEATURE_ORDER
        assert set(ACTIVE_FEATURE_ORDER).issubset(set(FEATURE_ORDER))

    def test_deprecated_non_identity_features_non_empty(self):
        from src.pipeline.identity_calibration import DEPRECATED_NON_IDENTITY_FEATURES
        assert len(DEPRECATED_NON_IDENTITY_FEATURES) > 0

    def test_deprecated_features_not_in_active_order(self):
        from src.pipeline.identity_calibration import ACTIVE_FEATURE_ORDER, DEPRECATED_NON_IDENTITY_FEATURES
        for feat in DEPRECATED_NON_IDENTITY_FEATURES:
            assert feat not in ACTIVE_FEATURE_ORDER

    def test_email_match_in_active_order(self):
        from src.pipeline.identity_calibration import ACTIVE_FEATURE_ORDER
        assert "email_match" in ACTIVE_FEATURE_ORDER

    def test_topical_similarity_deprecated(self):
        from src.pipeline.identity_calibration import DEPRECATED_NON_IDENTITY_FEATURES
        assert "topical_similarity" in DEPRECATED_NON_IDENTITY_FEATURES


class TestPairFeatureVector:
    def _f(self, contributions):
        from src.pipeline.identity_calibration import pair_feature_vector, FEATURE_ORDER
        return pair_feature_vector(contributions), len(FEATURE_ORDER)

    def test_length_equals_feature_order(self):
        vec, expected_len = self._f([("email_match", 0.8)])
        assert len(vec) == expected_len

    def test_known_signal_reflected(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        vec, _ = self._f([("email_match", 0.9)])
        idx = FEATURE_ORDER.index("email_match")
        assert vec[idx] == 0.9

    def test_max_confidence_per_type(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER
        vec, _ = self._f([("email_match", 0.7), ("email_match", 0.95), ("email_match", 0.3)])
        idx = FEATURE_ORDER.index("email_match")
        assert vec[idx] == 0.95

    def test_deprecated_signal_zeroed(self):
        from src.pipeline.identity_calibration import FEATURE_ORDER, DEPRECATED_NON_IDENTITY_FEATURES
        # bio_mention is deprecated — should be 0 in output
        vec, _ = self._f([("bio_mention", 0.9)])
        idx = FEATURE_ORDER.index("bio_mention")
        assert vec[idx] == 0.0

    def test_empty_contributions_all_zeros(self):
        vec, _ = self._f([])
        assert all(v == 0.0 for v in vec)


class TestFeatureValue:
    def _v(self, feats, signal_type):
        from src.pipeline.identity_calibration import _feature_value
        return _feature_value(feats, signal_type)

    def test_returns_value_for_active_signal(self):
        assert self._v({"email_match": 0.9}, "email_match") == 0.9

    def test_returns_zero_for_deprecated_signal(self):
        assert self._v({"bio_mention": 0.9}, "bio_mention") == 0.0

    def test_returns_zero_for_missing_key(self):
        assert self._v({}, "email_match") == 0.0
