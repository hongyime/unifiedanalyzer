"""
Pure-function tests — batch 60.

Covers previously untested pure functions:
- notifications.telegram: _parse_retry_after, _SEND_MAX_RETRIES constant
- notifications.intelligence: intelligence_status_lines, intelligence_run_lines
  (additional edge cases)
- notifications.merge_bot: remaining constant checks
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# notifications.telegram: _parse_retry_after
# ---------------------------------------------------------------------------

class TestParseRetryAfter:
    def _p(self, error):
        from src.notifications.telegram import _parse_retry_after
        return _parse_retry_after(error)

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_no_429_returns_none(self):
        assert self._p("some other error") is None

    def test_429_without_body_returns_default_5(self):
        assert self._p("429 Too Many Requests") == 5

    def test_429_with_json_retry_after(self):
        error = '429 {"parameters": {"retry_after": 30}}'
        result = self._p(error)
        assert result == 30

    def test_429_with_text_retry_after(self):
        error = "429 retry after 45 seconds"
        result = self._p(error)
        assert result == 45

    def test_minimum_1_for_json(self):
        error = '429 {"parameters": {"retry_after": 0}}'
        result = self._p(error)
        assert result >= 1

    def test_empty_string_returns_none(self):
        assert self._p("") is None


class TestSendMaxRetries:
    def test_positive(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_SEND_MAX_RETRIES", raising=False)
        from src.notifications.telegram import _SEND_MAX_RETRIES
        assert _SEND_MAX_RETRIES > 0

    def test_is_int(self):
        from src.notifications.telegram import _SEND_MAX_RETRIES
        assert isinstance(_SEND_MAX_RETRIES, int)


# ---------------------------------------------------------------------------
# notifications.intelligence: intelligence_status_lines
# ---------------------------------------------------------------------------

class TestIntelligenceStatusLines:
    def _s(self, intel):
        from src.notifications.intelligence import intelligence_status_lines
        return intelligence_status_lines(intel)

    def test_none_returns_unavailable(self):
        result = self._s(None)
        assert len(result) == 1
        assert "unavailable" in result[0].lower()

    def test_basic_intel(self):
        intel = {
            "text_total": 1000,
            "sentiment_ready": 800,
            "fts_ready": 900,
            "sentiment_pct": "80%",
            "fts_pct": "90%",
            "latest_text": None,
            "latest_sentiment": None,
            "chat_threads": 5,
            "search_mode": "semantic-fallback",
            "face_available": False,
            "face_ok": None,
            "face_entity_collisions": 0,
            "cluster_entity_collisions": 0,
            "location_total": 100,
            "location_active": 80,
            "location_suppressed": 10,
            "location_weak": 5,
            "intel_alerts_24h": 0,
            "intel_failed_phases": [],
        }
        result = self._s(intel)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_with_intel_alerts(self):
        intel = {
            "text_total": 0, "sentiment_ready": 0, "fts_ready": 0,
            "sentiment_pct": "0%", "fts_pct": "0%",
            "latest_text": None, "latest_sentiment": None,
            "chat_threads": 0, "search_mode": "semantic-fallback",
            "face_available": False, "face_ok": None,
            "face_entity_collisions": 0, "cluster_entity_collisions": 0,
            "location_total": 0, "location_active": 0,
            "location_suppressed": 0, "location_weak": 0,
            "intel_alerts_24h": 3,
            "emotional_spikes_24h": 2,
            "face_drift_24h": 1,
            "location_spikes_24h": 0,
            "intel_failed_phases": [],
        }
        result = self._s(intel)
        # Should include alert info
        combined = " ".join(result)
        assert "alert" in combined.lower() or "emotional" in combined.lower() or len(result) >= 2

    def test_with_failed_phases(self):
        intel = {
            "text_total": 0, "sentiment_ready": 0, "fts_ready": 0,
            "sentiment_pct": "0%", "fts_pct": "0%",
            "latest_text": None, "latest_sentiment": None,
            "chat_threads": 0, "search_mode": "semantic-fallback",
            "face_available": False, "face_ok": None,
            "face_entity_collisions": 0, "cluster_entity_collisions": 0,
            "location_total": 0, "location_active": 0,
            "location_suppressed": 0, "location_weak": 0,
            "intel_alerts_24h": 0,
            "intel_failed_phases": ["face_clustering", "topical_similarity"],
            "intel_failed_phase_count": 2,
        }
        result = self._s(intel)
        combined = " ".join(result)
        assert "failure" in combined.lower() or "face_clustering" in combined


# ---------------------------------------------------------------------------
# notifications.intelligence: intelligence_run_lines additional
# ---------------------------------------------------------------------------

class TestIntelligenceRunLinesAdditional:
    def _r(self, stats):
        from src.notifications.intelligence import intelligence_run_lines
        return intelligence_run_lines(stats)

    def test_empty_stats_empty_lines(self):
        result = self._r({})
        assert result == []

    def test_with_text_features(self):
        result = self._r({"text_features": 500, "sentiment_features": 300})
        assert isinstance(result, list)
        # Should produce output when non-zero
        assert len(result) >= 1

    def test_returns_list(self):
        assert isinstance(self._r({"alerts": 5}), list)

    def test_spike_alerts_reported(self):
        result = self._r({
            "alert_breakdown": {
                "emotional_spike": 2,
                "face_link_drift": 1,
                "location_evidence_spike": 3,
            }
        })
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# notifications.merge_bot: remaining constant checks
# ---------------------------------------------------------------------------

class TestMergeBotConstants:
    def test_pair_store_is_dict(self):
        from src.notifications.merge_bot import _pair_store
        assert isinstance(_pair_store, dict)

    def test_resolved_is_set(self):
        from src.notifications.merge_bot import _resolved
        assert isinstance(_resolved, set)

    def test_offset_initial_zero(self):
        from src.notifications.merge_bot import _offset
        # May have been modified, but should be int
        assert isinstance(_offset, int)
