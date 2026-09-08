"""
Pure-function tests — batch 13.

Covers previously untested modules with no DB or I/O:
- merge_candidates: merge_candidate_min_weight, merge_candidate_notify_min_confidence
- pipeline.identity_scorer: _pair_key, _is_uuid, _has_identity_evidence,
  _identity_score_contributions, _features_from_contributions, _feature_snapshot,
  _dismissal_suppresses_candidate, _TYPE_WEIGHT / _CONTEXT_ONLY_SIGNALS constants
- pipeline.run_timeline_subset: _csv_set
- pipeline.media_common: lookup_entity
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# merge_candidates
# ---------------------------------------------------------------------------

class TestMergeCandidates:
    def test_min_weight_default(self, monkeypatch):
        monkeypatch.delenv("NEW_IDENTITY_LINK_MIN_WEIGHT", raising=False)
        from src.merge_candidates import merge_candidate_min_weight, DEFAULT_MERGE_CANDIDATE_MIN_WEIGHT
        assert merge_candidate_min_weight() == DEFAULT_MERGE_CANDIDATE_MIN_WEIGHT

    def test_min_weight_custom(self, monkeypatch):
        monkeypatch.setenv("NEW_IDENTITY_LINK_MIN_WEIGHT", "70")
        from src.merge_candidates import merge_candidate_min_weight
        assert merge_candidate_min_weight() == 70

    def test_notify_min_confidence_default(self, monkeypatch):
        monkeypatch.delenv("MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE", raising=False)
        from src.merge_candidates import merge_candidate_notify_min_confidence, DEFAULT_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE
        assert merge_candidate_notify_min_confidence() == float(DEFAULT_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE)

    def test_notify_min_confidence_custom(self, monkeypatch):
        monkeypatch.setenv("MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE", "80.0")
        from src.merge_candidates import merge_candidate_notify_min_confidence
        assert merge_candidate_notify_min_confidence() == 80.0


# ---------------------------------------------------------------------------
# identity_scorer pure helpers
# ---------------------------------------------------------------------------

class TestIdentityScorerPairKey:
    def _p(self, a, b):
        from src.pipeline.identity_scorer import _pair_key
        return _pair_key(a, b)

    def test_already_sorted(self):
        assert self._p("aaa", "bbb") == ("aaa", "bbb")

    def test_reversed(self):
        assert self._p("bbb", "aaa") == ("aaa", "bbb")

    def test_equal(self):
        assert self._p("x", "x") == ("x", "x")

    def test_symmetric(self):
        assert self._p("a", "b") == self._p("b", "a")


class TestIsUuid:
    def _u(self, v):
        from src.pipeline.identity_scorer import _is_uuid
        return _is_uuid(v)

    def test_valid_uuid(self):
        assert self._u("12345678-1234-5678-1234-567812345678") is True

    def test_invalid_uuid(self):
        assert self._u("not-a-uuid") is False

    def test_none(self):
        assert self._u(None) is False

    def test_empty_string(self):
        assert self._u("") is False

    def test_numeric_string(self):
        assert self._u("12345") is False


class TestHasIdentityEvidence:
    def test_hard_signal_has_evidence(self):
        from src.pipeline.identity_scorer import _has_identity_evidence
        assert _has_identity_evidence([("email_match", 0.9)]) is True

    def test_context_only_no_evidence(self):
        from src.pipeline.identity_scorer import _has_identity_evidence
        assert _has_identity_evidence([("bio_mention", 0.5), ("topical_similarity", 0.3)]) is False

    def test_mixed_has_evidence(self):
        from src.pipeline.identity_scorer import _has_identity_evidence
        assert _has_identity_evidence([("bio_mention", 0.5), ("email_match", 0.9)]) is True

    def test_empty_contributions_no_evidence(self):
        from src.pipeline.identity_scorer import _has_identity_evidence
        assert _has_identity_evidence([]) is False


class TestIdentityScoreContributions:
    def _f(self, contributions):
        from src.pipeline.identity_scorer import _identity_score_contributions
        return _identity_score_contributions(contributions)

    def test_filters_context_only(self):
        contribs = [("email_match", 0.9), ("bio_mention", 0.4), ("topical_similarity", 0.2)]
        result = self._f(contribs)
        types = [t for t, _ in result]
        assert "email_match" in types
        assert "bio_mention" not in types
        assert "topical_similarity" not in types

    def test_empty_input(self):
        assert self._f([]) == []

    def test_all_context_filtered(self):
        contribs = [("bio_mention", 0.5), ("group_cooccurrence", 0.3)]
        assert self._f(contribs) == []


class TestFeaturesFromContributions:
    def _f(self, contributions):
        from src.pipeline.identity_scorer import _features_from_contributions
        return _features_from_contributions(contributions)

    def test_keeps_max_confidence_per_type(self):
        contribs = [("email_match", 0.7), ("email_match", 0.9), ("email_match", 0.5)]
        result = self._f(contribs)
        assert result["email_match"] == 0.9

    def test_multiple_types(self):
        contribs = [("email_match", 0.8), ("phone_match", 0.6)]
        result = self._f(contribs)
        assert result["email_match"] == 0.8
        assert result["phone_match"] == 0.6

    def test_empty(self):
        assert self._f([]) == {}

    def test_none_confidence_not_stored(self):
        # None coerces to 0.0 but 0.0 > 0.0 is False — key is never inserted
        result = self._f([("email_match", None)])
        assert "email_match" not in result


class TestFeatureSnapshot:
    def _s(self, raw):
        from src.pipeline.identity_scorer import _feature_snapshot
        return _feature_snapshot(raw)

    def test_dict_input(self):
        result = self._s({"email_match": 0.9, "phone_match": 0.6})
        assert result["email_match"] == 0.9

    def test_json_string(self):
        import json
        result = self._s(json.dumps({"email_match": 0.8}))
        assert result["email_match"] == 0.8

    def test_invalid_json(self):
        assert self._s("bad") == {}

    def test_none_input(self):
        assert self._s(None) == {}

    def test_non_float_values_skipped(self):
        result = self._s({"email_match": "bad", "phone_match": 0.6})
        assert "email_match" not in result
        assert result["phone_match"] == 0.6


class TestDismissalSuppressesCandidate:
    def _d(self, contributions, dismissed_features):
        from src.pipeline.identity_scorer import _dismissal_suppresses_candidate
        return _dismissal_suppresses_candidate(contributions, dismissed_features)

    def test_empty_dismissed_features_always_suppresses(self):
        contribs = [("email_match", 0.95)]
        assert self._d(contribs, {}) is True

    def test_new_hard_signal_above_delta_reopens(self):
        # previous dismissed at 0.7; now 0.76 >= 0.7 + 0.05 → not suppressed
        contribs = [("email_match", 0.76)]
        dismissed = {"email_match": 0.70}
        assert self._d(contribs, dismissed) is False

    def test_same_hard_signal_no_growth_suppresses(self):
        # same confidence — no delta
        contribs = [("email_match", 0.70)]
        dismissed = {"email_match": 0.70}
        assert self._d(contribs, dismissed) is True

    def test_context_signal_growth_never_reopens(self):
        # bio_mention is context-only — not in _HARD_IDENTITY_SIGNALS
        contribs = [("bio_mention", 0.99)]
        dismissed = {"bio_mention": 0.10}
        assert self._d(contribs, dismissed) is True


class TestIdentityScorerConstants:
    def test_type_weight_non_empty(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        assert len(_TYPE_WEIGHT) > 0

    def test_email_match_weight_in_range(self):
        from src.pipeline.identity_scorer import _TYPE_WEIGHT
        assert 0 < _TYPE_WEIGHT["email_match"] <= 1.0

    def test_context_only_signals_non_empty(self):
        from src.pipeline.identity_scorer import _CONTEXT_ONLY_SIGNALS
        assert len(_CONTEXT_ONLY_SIGNALS) > 0

    def test_bio_mention_context_only(self):
        from src.pipeline.identity_scorer import _CONTEXT_ONLY_SIGNALS
        assert "bio_mention" in _CONTEXT_ONLY_SIGNALS

    def test_email_match_not_context_only(self):
        from src.pipeline.identity_scorer import _CONTEXT_ONLY_SIGNALS
        assert "email_match" not in _CONTEXT_ONLY_SIGNALS


# ---------------------------------------------------------------------------
# run_timeline_subset: _csv_set
# ---------------------------------------------------------------------------

class TestCsvSet:
    def _c(self, raw):
        from src.pipeline.run_timeline_subset import _csv_set
        return _csv_set(raw)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_single_value(self):
        assert self._c("instagram") == {"instagram"}

    def test_multiple_values(self):
        assert self._c("instagram,telegram,github") == {"instagram", "telegram", "github"}

    def test_strips_whitespace(self):
        assert self._c(" instagram , telegram ") == {"instagram", "telegram"}

    def test_empty_string_returns_none(self):
        assert self._c("") is None

    def test_all_empty_parts_returns_none(self):
        assert self._c("  ,  ,  ") is None


# ---------------------------------------------------------------------------
# media_common: lookup_entity
# ---------------------------------------------------------------------------

class TestLookupEntity:
    def _l(self, lookup, source, entity_id):
        from src.pipeline.media_common import lookup_entity
        return lookup_entity(lookup, source, entity_id)

    def test_exact_match(self):
        lookup = {("instagram", "12345"): "eid-1"}
        assert self._l(lookup, "instagram", "12345") == "eid-1"

    def test_case_insensitive_fallback(self):
        lookup = {("instagram", "alice"): "eid-2"}
        assert self._l(lookup, "instagram", "ALICE") == "eid-2"

    def test_none_entity_id(self):
        assert self._l({}, "instagram", None) is None

    def test_empty_entity_id(self):
        assert self._l({}, "instagram", "") is None

    def test_wrong_source_no_match(self):
        lookup = {("instagram", "12345"): "eid-1"}
        assert self._l(lookup, "telegram", "12345") is None

    def test_not_found_returns_none(self):
        assert self._l({}, "instagram", "ghost") is None
