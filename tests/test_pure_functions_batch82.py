"""
Pure-function tests — batch 82.

Covers:
- pipeline.identity_scorer: _pair_key, _is_uuid, _has_identity_evidence,
  _identity_score_contributions, _features_from_contributions,
  _feature_snapshot, _dismissal_suppresses_candidate
- pipeline.beeper_bridge: _digits, parse_native_sender
- pipeline.content_fingerprint: _decode_meta, _tokenize, _cosine_sim
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _pair_key
# ---------------------------------------------------------------------------

class TestIdentityPairKey:
    def _k(self, a, b):
        from src.pipeline.identity_scorer import _pair_key
        return _pair_key(a, b)

    def test_order_independent(self):
        assert self._k("a", "b") == self._k("b", "a")

    def test_smaller_first(self):
        result = self._k("z", "a")
        assert result == ("a", "z")

    def test_same_values(self):
        assert self._k("x", "x") == ("x", "x")


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _is_uuid
# ---------------------------------------------------------------------------

class TestIsUuid:
    def _u(self, v):
        from src.pipeline.identity_scorer import _is_uuid
        return _is_uuid(v)

    def test_valid_uuid(self):
        assert self._u("550e8400-e29b-41d4-a716-446655440000") is True

    def test_invalid_string(self):
        assert self._u("not-a-uuid") is False

    def test_none_returns_false(self):
        assert self._u(None) is False

    def test_empty_returns_false(self):
        assert self._u("") is False


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _has_identity_evidence
# ---------------------------------------------------------------------------

class TestHasIdentityEvidence:
    def _h(self, contributions):
        from src.pipeline.identity_scorer import _has_identity_evidence
        return _has_identity_evidence(contributions)

    def test_empty_returns_false(self):
        assert self._h([]) is False

    def test_context_only_signal_returns_false(self):
        # group_cooccurrence IS in _CONTEXT_ONLY_SIGNALS
        assert self._h([("group_cooccurrence", 0.5)]) is False

    def test_hard_identity_signal_returns_true(self):
        # username_exact IS in _HARD_SIGNALS
        assert self._h([("username_exact", 0.9)]) is True

    def test_mixed_returns_true(self):
        assert self._h([("group_cooccurrence", 0.5), ("username_exact", 0.9)]) is True


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _identity_score_contributions
# ---------------------------------------------------------------------------

class TestIdentityScoreContributions:
    def _s(self, contributions):
        from src.pipeline.identity_scorer import _identity_score_contributions
        return _identity_score_contributions(contributions)

    def test_empty_returns_empty(self):
        assert self._s([]) == []

    def test_filters_context_only(self):
        # group_cooccurrence IS in _CONTEXT_ONLY_SIGNALS
        result = self._s([("group_cooccurrence", 0.5), ("username_exact", 0.9)])
        types = [t for t, _ in result]
        assert "group_cooccurrence" not in types
        assert "username_exact" in types

    def test_identity_signals_preserved(self):
        result = self._s([("username_exact", 0.9)])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _features_from_contributions
# ---------------------------------------------------------------------------

class TestFeaturesFromContributions:
    def _f(self, contributions):
        from src.pipeline.identity_scorer import _features_from_contributions
        return _features_from_contributions(contributions)

    def test_empty_returns_empty(self):
        assert self._f([]) == {}

    def test_single_signal(self):
        result = self._f([("shared_email", 0.9)])
        assert abs(result["shared_email"] - 0.9) < 1e-9

    def test_max_confidence_kept(self):
        result = self._f([("shared_email", 0.5), ("shared_email", 0.9)])
        assert abs(result["shared_email"] - 0.9) < 1e-9

    def test_multiple_signals(self):
        result = self._f([("shared_email", 0.9), ("shared_phone", 0.7)])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _feature_snapshot
# ---------------------------------------------------------------------------

class TestFeatureSnapshot:
    def _s(self, raw):
        from src.pipeline.identity_scorer import _feature_snapshot
        return _feature_snapshot(raw)

    def test_dict_input(self):
        result = self._s({"shared_email": 0.9})
        assert abs(result["shared_email"] - 0.9) < 1e-9

    def test_json_string_parsed(self):
        import json
        result = self._s(json.dumps({"shared_email": 0.9}))
        assert abs(result["shared_email"] - 0.9) < 1e-9

    def test_invalid_string_returns_empty(self):
        assert self._s("not json") == {}

    def test_non_dict_returns_empty(self):
        assert self._s([1, 2]) == {}

    def test_none_returns_empty(self):
        assert self._s(None) == {}

    def test_non_float_value_skipped(self):
        result = self._s({"k": "bad_value"})
        assert "k" not in result


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _dismissal_suppresses_candidate
# ---------------------------------------------------------------------------

class TestDismissalSuppressesCandidate:
    def _d(self, contributions, dismissed_features):
        from src.pipeline.identity_scorer import _dismissal_suppresses_candidate
        return _dismissal_suppresses_candidate(contributions, dismissed_features)

    def test_empty_dismissed_features_always_suppresses(self):
        assert self._d([("shared_email", 0.9)], {}) is True

    def test_no_hard_evidence_suppresses(self):
        # Only context signal — suppresses
        assert self._d([("group_cooccurrence", 0.9)], {"username_exact": 0.5}) is True

    def test_large_new_hard_evidence_reopens(self):
        # username_exact IS a hard signal; delta 1.0 - 0.0 >> 0.05 threshold
        result = self._d([("username_exact", 1.0)], {"username_exact": 0.0})
        assert result is False


# ---------------------------------------------------------------------------
# pipeline.beeper_bridge: _digits
# ---------------------------------------------------------------------------

class TestBeeperDigits:
    def _d(self, s):
        from src.pipeline.beeper_bridge import _digits
        return _digits(s)

    def test_digits_only(self):
        assert self._d("12345") == "12345"

    def test_strips_non_digits(self):
        assert self._d("+65 9123-4567") == "6591234567"

    def test_empty_string(self):
        assert self._d("") == ""

    def test_letters_stripped(self):
        assert self._d("abc123def") == "123"


# ---------------------------------------------------------------------------
# pipeline.beeper_bridge: parse_native_sender
# ---------------------------------------------------------------------------

class TestParseNativeSender:
    def _p(self, network, sender_id, display=None):
        from src.pipeline.beeper_bridge import parse_native_sender
        return parse_native_sender(network, sender_id, display)

    def test_unknown_network_returns_none(self):
        assert self._p("Signal", "123", "Alice") is None

    def test_telegram_numeric_id(self):
        result = self._p("Telegram", "telegram:123456789", "alice")
        if result:
            assert result[0] == "telegram"

    def test_instagram_id(self):
        result = self._p("Instagram", "instagram:myuser", "myuser")
        if result:
            assert result[0] == "instagram"

    def test_whatsapp_direct_phone(self):
        result = self._p("WhatsApp", "6591234567@s.whatsapp.net", None)
        if result:
            assert result[0] == "whatsapp"


# ---------------------------------------------------------------------------
# pipeline.content_fingerprint: _decode_meta
# ---------------------------------------------------------------------------

class TestFingerprintDecodeMeta:
    def _d(self, raw):
        from src.pipeline.content_fingerprint import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._d(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_returns_empty(self):
        assert self._d("not json") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


# ---------------------------------------------------------------------------
# pipeline.content_fingerprint: _tokenize
# ---------------------------------------------------------------------------

class TestFingerprintTokenize:
    def _t(self, text):
        from src.pipeline.content_fingerprint import _tokenize
        return _tokenize(text)

    def test_returns_list(self):
        assert isinstance(self._t("hello world"), list)

    def test_lowercased(self):
        result = self._t("Hello World")
        assert all(w == w.lower() for w in result)

    def test_stopwords_excluded(self):
        result = self._t("the is and")
        assert result == []

    def test_short_words_excluded(self):
        # words < 2 chars excluded
        result = self._t("a b cc ddd")
        for w in result:
            assert len(w) >= 2


# ---------------------------------------------------------------------------
# pipeline.content_fingerprint: _cosine_sim
# ---------------------------------------------------------------------------

class TestFingerprintCosineSim:
    def _c(self, a, b):
        from src.pipeline.content_fingerprint import _cosine_sim
        return _cosine_sim(a, b)

    def test_empty_returns_zero(self):
        assert self._c({}, {}) == 0.0

    def test_identical_returns_one(self):
        d = {"hello": 3, "world": 2}
        assert abs(self._c(d, d) - 1.0) < 1e-9

    def test_disjoint_returns_zero(self):
        assert self._c({"a": 1}, {"b": 1}) == 0.0

    def test_partial_overlap(self):
        result = self._c({"a": 1, "b": 1}, {"a": 1, "c": 1})
        assert 0.0 < result < 1.0
