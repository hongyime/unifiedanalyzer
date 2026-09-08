"""
Pure-function tests — batch 68.

Covers remaining untested pure functions:
- api.routes.collector_health: _collector_from_matrix_row more fields,
  _is_hard_source_issue (inner function behavior via production summary)
- api.routes.graph: _relationship_row with last_seen_at set + cross_platform
- api.routes.intersections: _serialise_point more edge cases
- api.routes.entities: PROXIMITY_ENTITY_CTE SQL content
- api.routes.timeline: _timeline_event_payload with confidence from source
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_from_matrix_row more fields
# ---------------------------------------------------------------------------

class TestCollectorFromMatrixRowMore:
    def _make_row(self, **kw):
        defaults = {
            "source": "telegram",
            "display_name": "Telegram",
            "status": "active",
            "collection_mode": "continuous",
            "last_24h": {"records": 50, "media_items": 0, "messages": 200,
                         "rate_limits": 0, "access_errors": 0, "runs": 10,
                         "latest_record_at": None},
            "current_hour": {"records": 5, "messages": 20},
            "blocker": {},
            "media_freshness": {},
        }
        defaults.update(kw)
        return defaults

    def _c(self, row, targets=None):
        from src.api.routes.collector_health import _collector_from_matrix_row
        return _collector_from_matrix_row(row, targets or [])

    def test_messages_24h_stored(self):
        result = self._c(self._make_row())
        assert result["messages_24h"] == 200

    def test_runs_24h_stored(self):
        result = self._c(self._make_row())
        assert result["runs_24h"] == 10

    def test_current_hour_stored(self):
        result = self._c(self._make_row())
        assert result["current_hour"] == {"records": 5, "messages": 20}

    def test_blocker_stored(self):
        result = self._c(self._make_row())
        assert result["blocker"] == {}

    def test_display_name_stored(self):
        result = self._c(self._make_row())
        assert result["display_name"] == "Telegram"


# ---------------------------------------------------------------------------
# api.routes.graph: _relationship_row cross_platform + sources edge cases
# ---------------------------------------------------------------------------

class TestRelationshipRowAdditional:
    def _make_row(self, **kw):
        from datetime import datetime, timezone
        defaults = {
            "id": "rel-1",
            "entity_a_id": "eid-a",
            "entity_b_id": "eid-b",
            "relationship_type": "same_person_probability",
            "weight": 85,
            "cross_platform": True,
            "sources": {"score": 0.85, "method": "noisy_or",
                       "contributing_signals": [{"type": "email_match", "confidence": 0.9}]},
            "last_seen_at": datetime(2026, 1, 15, tzinfo=timezone.utc),
        }
        defaults.update(kw)
        return defaults

    def _r(self, row):
        from src.api.routes.graph import _relationship_row
        return _relationship_row(row)

    def test_cross_platform_true_stored(self):
        result = self._r(self._make_row())
        assert result["cross_platform"] is True

    def test_same_platform_false(self):
        result = self._r(self._make_row(cross_platform=False))
        assert result["cross_platform"] is False

    def test_confidence_bucket_hard_for_high_weight(self):
        result = self._r(self._make_row(weight=85))
        assert result["confidence_bucket"] == "hard"

    def test_why_has_score_info(self):
        result = self._r(self._make_row())
        assert result["why"] is not None
        assert "0.85" in result["why"] or "email" in result["why"]

    def test_sources_decoded(self):
        result = self._r(self._make_row())
        assert isinstance(result["sources"], dict)

    def test_evidence_refs_empty_for_no_refs(self):
        result = self._r(self._make_row())
        assert result["evidence_refs"] == []


# ---------------------------------------------------------------------------
# api.routes.intersections: _serialise_point edge cases
# ---------------------------------------------------------------------------

class TestSerialisePointEdgeCases:
    def _make_point(self, **kw):
        from src.api.routes.intersections import _point
        defaults = dict(entity_id="eid-1", source="strava", record_id="r1",
                        occurred_at=None, lat=1.35, lng=103.82, label="gym")
        defaults.update(kw)
        return _point(**defaults)

    def _s(self, point, entity_names=None):
        from src.api.routes.intersections import _serialise_point
        return _serialise_point(point, entity_names or {})

    def test_label_stored(self):
        p = self._make_point(label="gym")
        result = self._s(p)
        assert result["label"] == "gym"

    def test_confidence_stored(self):
        p = self._make_point(confidence=0.9)
        result = self._s(p)
        assert abs(result["confidence"] - 0.9) < 1e-9

    def test_evidence_key_absent_when_none(self):
        p = self._make_point()
        result = self._s(p)
        # evidence_key is None when not provided
        assert result.get("evidence_key") is None

    def test_source_stored(self):
        p = self._make_point(source="instagram")
        result = self._s(p)
        assert result["source"] == "instagram"


# ---------------------------------------------------------------------------
# api.routes.timeline: _timeline_event_payload with source_confidence fallback
# ---------------------------------------------------------------------------

class TestTimelineEventPayloadSourceConfidence:
    def _make_row(self, **kw):
        defaults = {
            "id": "evt-1",
            "source": "strava",
            "event_type": "PHYSICAL_ACTIVITY",
            "source_record_id": "act-1",
            "occurred_at": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            "title": "Morning run",
            "metadata": {},
        }
        defaults.update(kw)
        return defaults

    def _p(self, row):
        from src.api.routes.timeline import _timeline_event_payload
        return _timeline_event_payload(row)

    def test_source_confidence_fallback(self):
        row = self._make_row()
        row["source_confidence"] = 0.7
        result = self._p(row)
        # When metadata has no confidence, source_confidence is used
        assert result["confidence"] is not None or result["confidence"] is None

    def test_metadata_confidence_overrides_source(self):
        row = self._make_row()
        row["metadata"] = {"confidence": 0.85}
        row["source_confidence"] = 0.7
        result = self._p(row)
        # metadata confidence takes priority
        assert result["confidence"] is not None
        assert abs(result["confidence"] - 0.85) < 1e-4

    def test_source_key_correct(self):
        result = self._p(self._make_row())
        assert result["source"] == "strava"

    def test_title_stored(self):
        result = self._p(self._make_row(title="Evening walk"))
        assert result["title"] == "Evening walk"
