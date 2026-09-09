"""
Pure-function tests — batch 114. FINAL BATCH.

Closing the last 1% to reach the 100% pure-function coverage floor.
Covers remaining edge cases across all major modules.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import json


# ---------------------------------------------------------------------------
# pipeline.entity_resolver: name_block_keys additional edge cases
# ---------------------------------------------------------------------------

class TestNameBlockKeysAdditional:
    def _b(self, name):
        from src.pipeline.entity_resolver import name_block_keys
        return name_block_keys(name)

    def test_three_char_token_included(self):
        result = self._b("abc")
        assert "abc" in result

    def test_longer_token_uses_first_3(self):
        result = self._b("university")
        assert "uni" in result

    def test_multiple_tokens(self):
        result = self._b("alice johnson")
        assert "ali" in result
        assert "joh" in result

    def test_two_char_token_excluded(self):
        result = self._b("of")
        assert result == set()


# ---------------------------------------------------------------------------
# pipeline.temporal_correlation: _weighted_cosine with IDF weights
# ---------------------------------------------------------------------------

class TestWeightedCosineWithIdf:
    def test_idf_weights_shape_similarity(self):
        from src.pipeline.temporal_correlation import _hour_idf_weights, _weighted_cosine
        # Two entities with identical posting patterns should score 1.0
        hour_dists = {
            "e1": [1.0 if h in (9, 18) else 0.0 for h in range(24)],
            "e2": [1.0 if h in (9, 18) else 0.0 for h in range(24)],
        }
        weights = _hour_idf_weights(hour_dists)
        a = hour_dists["e1"]
        b = hour_dists["e2"]
        result = _weighted_cosine(a, b, weights)
        assert abs(result - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# pipeline.stream_alerts: alert_detail_json with datetime values
# ---------------------------------------------------------------------------

class TestAlertDetailJsonDatetime:
    def test_datetime_serialized(self):
        from src.pipeline.stream_alerts import alert_detail_json
        dt = datetime(2026, 6, 1, tzinfo=timezone.utc)
        result = alert_detail_json(occurred_at=dt, count=5)
        parsed = json.loads(result)
        assert "occurred_at" in parsed
        assert "count" in parsed


# ---------------------------------------------------------------------------
# pipeline.location_evidence: location_evidence_key with record_id
# ---------------------------------------------------------------------------

class TestLocationEvidenceKeyWithRecordId:
    def _k(self, **kw):
        from src.pipeline.location_evidence import location_evidence_key
        return location_evidence_key(**kw)

    def test_with_source_record_id_excludes_coords(self):
        # When source_record_id is set, lat/lng/occurred_at are excluded from hash
        k1 = self._k(entity_id="e1", source="strava", evidence_type="gps",
                     source_record_id="activity-123", lat=1.35, lng=103.82)
        k2 = self._k(entity_id="e1", source="strava", evidence_type="gps",
                     source_record_id="activity-123", lat=0.0, lng=0.0)
        # Same record_id → same key regardless of coords
        assert k1 == k2

    def test_without_source_record_id_includes_coords(self):
        k1 = self._k(entity_id="e1", source="strava", evidence_type="gps",
                     lat=1.35, lng=103.82)
        k2 = self._k(entity_id="e1", source="strava", evidence_type="gps",
                     lat=0.0, lng=0.0)
        # Different coords → different key
        assert k1 != k2


# ---------------------------------------------------------------------------
# pipeline.identity_scorer: _features_from_contributions dedup
# ---------------------------------------------------------------------------

class TestFeaturesFromContributionsDedup:
    def _f(self, contributions):
        from src.pipeline.identity_scorer import _features_from_contributions
        return _features_from_contributions(contributions)

    def test_same_type_multiple_sources_max_kept(self):
        result = self._f([
            ("username_exact", 0.3),
            ("username_exact", 0.9),
            ("username_exact", 0.6),
        ])
        assert abs(result["username_exact"] - 0.9) < 1e-9

    def test_zero_confidence_treated_as_zero(self):
        result = self._f([("username_exact", 0)])
        assert result.get("username_exact", 0) == 0.0


# ---------------------------------------------------------------------------
# pipeline.notifications.intelligence: intelligence_status_lines
# ---------------------------------------------------------------------------

class TestIntelligenceStatusLines:
    def _s(self, intel):
        from src.notifications.intelligence import intelligence_status_lines
        return intelligence_status_lines(intel)

    def test_none_returns_list(self):
        assert isinstance(self._s(None), list)

    def test_empty_dict_returns_list(self):
        assert isinstance(self._s({}), list)

    def test_with_intel_returns_list(self):
        result = self._s({"entities": 10, "resolved_pairs": 5})
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_from_live_row basics
# ---------------------------------------------------------------------------

class TestCollectorFromLiveRow:
    def _c(self, row, targets=None):
        from src.api.routes.collector_health import _collector_from_live_row
        return _collector_from_live_row(row, targets or [])

    def _make(self, **kw):
        defaults = {
            "source": "instagram", "display_name": "Instagram",
            "status": "fresh", "collection_mode": "browser",
            "records_24h": 100, "media_items_24h": 50,
            "rate_limits_24h": 0, "access_errors_24h": 0,
            "latest_record_at": None, "latest_run_at": None,
            "blocker_kind": None, "blocker_severity": None,
            "blocker_summary": None, "blocker_next_action": None,
        }
        defaults.update(kw)
        return defaults

    def test_returns_dict(self):
        result = self._c(self._make())
        assert isinstance(result, dict)

    def test_source_preserved(self):
        result = self._c(self._make(source="telegram"))
        assert result["source"] == "telegram"

    def test_status_preserved(self):
        result = self._c(self._make(status="stale"))
        assert result["status"] == "stale"
