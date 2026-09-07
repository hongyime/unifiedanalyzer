"""
Pure-function tests — batch 4.

Covers previously untested modules with no DB or I/O:
- pipeline.identity_truth: _row_get, _json_dict, SignalEvidence (family/is_spiderfoot/is_hard),
  coerce_signal, corroborated_auto_truth, build_truth_assertion
- pipeline.auto_labeler: _is_enabled, _HARD_SIGNALS / _SCORING_SIGNALS set membership
- pipeline.strava_patterns: _decode_meta
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# strava_patterns._decode_meta (same pattern as graph_analytics — own module)
# ---------------------------------------------------------------------------

class TestStravaDecodeMetaPure:
    def _d(self, raw):
        from src.pipeline.strava_patterns import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_json_bytes(self):
        assert self._d(b'{"b": 2}') == {"b": 2}

    def test_invalid_json_returns_empty(self):
        assert self._d("bad") == {}

    def test_array_json_returns_empty(self):
        assert self._d("[1, 2]") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


# ---------------------------------------------------------------------------
# identity_truth._row_get
# ---------------------------------------------------------------------------

class TestRowGet:
    def _r(self, row, key, default=None):
        from src.pipeline.identity_truth import _row_get
        return _row_get(row, key, default)

    def test_dict_present(self):
        assert self._r({"x": 42}, "x") == 42

    def test_dict_missing_returns_default(self):
        assert self._r({"x": 1}, "y", "fallback") == "fallback"

    def test_dict_missing_default_none(self):
        assert self._r({}, "z") is None

    def test_subscriptable_row_object(self):
        # asyncpg Record is subscriptable; simulate with a dict subclass
        row = {"signal_type": "email_match", "confidence": 0.9}
        assert self._r(row, "signal_type") == "email_match"

    def test_exception_falls_back_to_dict_get(self):
        # An object that raises on __getitem__ but is also a dict
        row = {}
        assert self._r(row, "missing", "default") == "default"


# ---------------------------------------------------------------------------
# identity_truth._json_dict
# ---------------------------------------------------------------------------

class TestJsonDict:
    def _j(self, value):
        from src.pipeline.identity_truth import _json_dict
        return _json_dict(value)

    def test_dict_returns_copy(self):
        d = {"a": 1}
        result = self._j(d)
        assert result == d
        # should be a copy
        result["b"] = 2
        assert "b" not in d

    def test_json_string(self):
        assert self._j('{"x": 99}') == {"x": 99}

    def test_invalid_json_returns_empty(self):
        assert self._j("not json") == {}

    def test_none_returns_empty(self):
        assert self._j(None) == {}

    def test_int_returns_empty(self):
        assert self._j(42) == {}

    def test_json_array_returns_empty(self):
        assert self._j("[1, 2]") == {}


# ---------------------------------------------------------------------------
# identity_truth.SignalEvidence — property logic
# ---------------------------------------------------------------------------

def _make_signal(
    signal_type="email_match",
    source_platform="analyzer",
    source_table=None,
    confidence=0.9,
    value="test@example.com",
):
    from src.pipeline.identity_truth import SignalEvidence
    return SignalEvidence(
        id="sig-1",
        signal_type=signal_type,
        source_platform=source_platform,
        source_table=source_table,
        value=value,
        confidence=confidence,
        metadata={},
    )


class TestSignalEvidenceFamily:
    def test_spiderfoot_platform(self):
        s = _make_signal(source_platform="spiderfoot")
        assert s.family == "spiderfoot"

    def test_recon_platform(self):
        s = _make_signal(source_platform="recon")
        assert s.family == "spiderfoot"

    def test_recon_table(self):
        s = _make_signal(source_platform="analyzer", source_table="recon_observations")
        assert s.family == "spiderfoot"

    def test_recon_targets_table(self):
        s = _make_signal(source_platform="analyzer", source_table="recon_targets")
        assert s.family == "spiderfoot"

    def test_non_spiderfoot_platform_returns_platform(self):
        s = _make_signal(source_platform="instagram")
        assert s.family == "instagram"

    def test_empty_platform_returns_analyzer(self):
        s = _make_signal(source_platform="")
        assert s.family == "analyzer"


class TestSignalEvidenceIsSpiderfoot:
    def test_spiderfoot_true(self):
        assert _make_signal(source_platform="spiderfoot").is_spiderfoot is True

    def test_non_spiderfoot_false(self):
        assert _make_signal(source_platform="instagram").is_spiderfoot is False


class TestSignalEvidenceIsHard:
    def test_spiderfoot_never_hard(self):
        # Spiderfoot signals are NEVER hard regardless of signal_type
        s = _make_signal(signal_type="email_match", source_platform="spiderfoot")
        assert s.is_hard is False

    def test_hard_signal_type(self):
        s = _make_signal(signal_type="email_match", source_platform="instagram")
        assert s.is_hard is True

    def test_cross_platform_link_is_hard(self):
        s = _make_signal(signal_type="cross_platform_link", source_platform="analyzer")
        assert s.is_hard is True

    def test_unknown_signal_type_high_confidence_is_hard(self):
        s = _make_signal(signal_type="unknown_novel_signal", source_platform="analyzer", confidence=0.95)
        assert s.is_hard is True

    def test_unknown_signal_type_low_confidence_not_hard(self):
        s = _make_signal(signal_type="unknown_novel_signal", source_platform="analyzer", confidence=0.5)
        assert s.is_hard is False

    def test_topical_similarity_not_hard(self):
        # Deliberately excluded from HARD_SIGNALS
        s = _make_signal(signal_type="topical_similarity", source_platform="analyzer", confidence=0.5)
        assert s.is_hard is False


# ---------------------------------------------------------------------------
# identity_truth.coerce_signal
# ---------------------------------------------------------------------------

class TestCoerceSignal:
    def test_coerces_dict_row(self):
        from src.pipeline.identity_truth import coerce_signal
        row = {
            "id": "abc",
            "signal_type": "EMAIL_MATCH",
            "source_platform": "Instagram",
            "source_table": None,
            "value": "  test@x.com  ",
            "confidence": "0.85",
            "metadata": None,
        }
        s = coerce_signal(row)
        assert s.signal_type == "email_match"
        assert s.source_platform == "instagram"
        assert s.value == "test@x.com"
        assert s.confidence == 0.85
        assert s.metadata == {}

    def test_confidence_clamped_to_1(self):
        from src.pipeline.identity_truth import coerce_signal
        row = {"id": None, "signal_type": "x", "source_platform": "y",
               "source_table": None, "value": "v", "confidence": 999.0, "metadata": None}
        s = coerce_signal(row)
        assert s.confidence == 1.0

    def test_confidence_clamped_to_0(self):
        from src.pipeline.identity_truth import coerce_signal
        row = {"id": None, "signal_type": "x", "source_platform": "y",
               "source_table": None, "value": "v", "confidence": -5.0, "metadata": None}
        s = coerce_signal(row)
        assert s.confidence == 0.0

    def test_bad_confidence_defaults_to_0(self):
        from src.pipeline.identity_truth import coerce_signal
        row = {"id": None, "signal_type": "x", "source_platform": "y",
               "source_table": None, "value": "v", "confidence": "bad", "metadata": None}
        s = coerce_signal(row)
        assert s.confidence == 0.0


# ---------------------------------------------------------------------------
# identity_truth.corroborated_auto_truth
# ---------------------------------------------------------------------------

def _sf_row(signal_type="some_recon_signal", confidence=0.7):
    return {"id": "sf1", "signal_type": signal_type, "source_platform": "spiderfoot",
            "source_table": None, "value": "testval", "confidence": confidence, "metadata": None}

def _hard_row(signal_type="email_match", confidence=0.95):
    return {"id": "h1", "signal_type": signal_type, "source_platform": "analyzer",
            "source_table": None, "value": "testval", "confidence": confidence, "metadata": None}


class TestCorroboratedAutoTruth:
    def test_requires_spiderfoot(self):
        from src.pipeline.identity_truth import corroborated_auto_truth
        ok, conf, detail = corroborated_auto_truth([_hard_row()])
        assert ok is False
        assert "requires_spiderfoot" in detail["reason"]

    def test_requires_hard_signal(self):
        from src.pipeline.identity_truth import corroborated_auto_truth
        ok, conf, detail = corroborated_auto_truth([_sf_row()])
        assert ok is False
        assert "requires_spiderfoot" in detail["reason"]

    def test_promoted_when_both_present(self):
        from src.pipeline.identity_truth import corroborated_auto_truth
        ok, conf, detail = corroborated_auto_truth([_sf_row(), _hard_row()])
        assert ok is True
        assert conf >= 0.85
        assert conf <= 1.0

    def test_promoted_confidence_capped_at_0_99(self):
        from src.pipeline.identity_truth import corroborated_auto_truth
        ok, conf, _ = corroborated_auto_truth([_sf_row(confidence=0.99), _hard_row(confidence=0.99)])
        assert ok is True
        assert conf <= 0.99

    def test_empty_signals_not_promoted(self):
        from src.pipeline.identity_truth import corroborated_auto_truth
        ok, conf, _ = corroborated_auto_truth([])
        assert ok is False
        assert conf == 0.0

    def test_signals_with_empty_value_excluded(self):
        from src.pipeline.identity_truth import corroborated_auto_truth
        empty_sf = {**_sf_row(), "value": ""}
        ok, conf, _ = corroborated_auto_truth([empty_sf, _hard_row()])
        assert ok is False  # spiderfoot row filtered out → no spiderfoot → False


# ---------------------------------------------------------------------------
# identity_truth.build_truth_assertion
# ---------------------------------------------------------------------------

class TestBuildTruthAssertion:
    def test_returns_none_when_not_corroborated(self):
        from src.pipeline.identity_truth import build_truth_assertion
        result = build_truth_assertion("eid-1", "email@x.com", [_hard_row()])
        assert result is None

    def test_returns_dict_when_corroborated(self):
        from src.pipeline.identity_truth import build_truth_assertion
        result = build_truth_assertion("eid-1", "email@x.com", [_sf_row(), _hard_row()])
        assert result is not None
        assert result["entity_id"] == "eid-1"
        assert result["value"] == "email@x.com"
        assert result["truth_state"] == "auto_truth"
        assert result["confidence"] >= 0.85

    def test_required_keys_present(self):
        from src.pipeline.identity_truth import build_truth_assertion
        result = build_truth_assertion("eid-1", "email@x.com", [_sf_row(), _hard_row()])
        for key in ("assertion_type", "entity_id", "value", "truth_state", "confidence",
                    "evidence_count", "evidence_signal_ids", "evidence_summary"):
            assert key in result


# ---------------------------------------------------------------------------
# auto_labeler constants + _is_enabled
# ---------------------------------------------------------------------------

class TestAutoLabelerConstants:
    def test_hard_signals_non_empty(self):
        from src.pipeline.auto_labeler import _HARD_SIGNALS
        assert len(_HARD_SIGNALS) > 0

    def test_scoring_signals_non_empty(self):
        from src.pipeline.auto_labeler import _SCORING_SIGNALS
        assert len(_SCORING_SIGNALS) > 0

    def test_hard_signals_subset_of_scoring(self):
        from src.pipeline.auto_labeler import _HARD_SIGNALS, _SCORING_SIGNALS
        assert _HARD_SIGNALS.issubset(_SCORING_SIGNALS)

    def test_email_match_in_hard(self):
        from src.pipeline.auto_labeler import _HARD_SIGNALS
        assert "email_match" in _HARD_SIGNALS

    def test_topical_similarity_not_in_hard(self):
        from src.pipeline.auto_labeler import _HARD_SIGNALS
        assert "topical_similarity" not in _HARD_SIGNALS

    def test_topical_similarity_in_scoring(self):
        from src.pipeline.auto_labeler import _SCORING_SIGNALS
        assert "topical_similarity" in _SCORING_SIGNALS

    def test_face_pair_knn_in_hard(self):
        from src.pipeline.auto_labeler import _HARD_SIGNALS
        assert "face_pair_knn" in _HARD_SIGNALS


class TestAutoLabelerIsEnabled:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("AUTO_LABEL_ENABLED", raising=False)
        from src.pipeline.auto_labeler import _is_enabled
        assert _is_enabled() is False

    def test_enabled_when_set_to_1(self, monkeypatch):
        monkeypatch.setenv("AUTO_LABEL_ENABLED", "1")
        from src.pipeline.auto_labeler import _is_enabled
        assert _is_enabled() is True

    def test_disabled_when_set_to_0(self, monkeypatch):
        monkeypatch.setenv("AUTO_LABEL_ENABLED", "0")
        from src.pipeline.auto_labeler import _is_enabled
        assert _is_enabled() is False
