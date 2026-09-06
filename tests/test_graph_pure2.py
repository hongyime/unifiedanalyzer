"""
QA-lane tests for remaining pure row-mapper functions in src/api/routes/graph.py:
- _relationship_row: row → structured relationship dict
- _geo_events: merge routes + points, sort by time, limit
- _conversation_thread_payload: row → conversation thread dict
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.api.routes.graph import (
    _conversation_thread_payload,
    _geo_event,
    _geo_events,
    _relationship_row,
)

_NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
_LATER = datetime(2024, 6, 2, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _relationship_row
# ---------------------------------------------------------------------------

class TestRelationshipRow:
    def _row(self, **overrides):
        base = {
            "id": "rel-1",
            "entity_a_id": "00000000-0000-0000-0000-000000000001",
            "entity_b_id": "00000000-0000-0000-0000-000000000002",
            "relationship_type": "same_person_probability",
            "weight": 85,
            "cross_platform": True,
            "sources": {"score": 0.85},
            "last_seen_at": _NOW,
        }
        base.update(overrides)
        return base

    def test_basic_fields_mapped(self):
        result = _relationship_row(self._row())
        assert result["id"] == "rel-1"
        assert result["relationship_type"] == "same_person_probability"
        assert result["weight"] == 85
        assert result["cross_platform"] is True

    def test_confidence_bucket_computed(self):
        result = _relationship_row(self._row())
        assert result["confidence_bucket"] in ("hard", "strong", "weak", "context-only")

    def test_last_seen_at_isoformatted(self):
        result = _relationship_row(self._row())
        assert "2024-06-01" in result["last_seen_at"]

    def test_none_last_seen_at_becomes_none(self):
        result = _relationship_row(self._row(last_seen_at=None))
        assert result["last_seen_at"] is None

    def test_none_entity_ids_become_none(self):
        result = _relationship_row(self._row(entity_a_id=None, entity_b_id=None))
        assert result["from_entity_id"] is None
        assert result["to_entity_id"] is None

    def test_sources_dict_passthrough(self):
        result = _relationship_row(self._row(sources={"score": 0.9}))
        assert isinstance(result["sources"], dict)


# ---------------------------------------------------------------------------
# _geo_events
# ---------------------------------------------------------------------------

class TestGeoEvents:
    def _route(self, occurred_at="2024-06-01"):
        return {"occurred_at": occurred_at, "points": [[1.0, 103.0], [1.1, 103.1]]}

    def _point(self, occurred_at="2024-06-02"):
        return {"occurred_at": occurred_at, "lat": 1.0, "lng": 103.0}

    def test_empty_returns_empty(self):
        assert _geo_events([], []) == []

    def test_routes_and_points_combined(self):
        result = _geo_events([self._route()], [self._point()])
        assert len(result) == 2

    def test_sorted_by_occurred_at_descending(self):
        result = _geo_events(
            [self._route("2024-01-01")],
            [self._point("2024-06-01")]
        )
        # Later date (2024-06-01) should come first (reverse=True)
        assert result[0]["occurred_at"] >= result[1]["occurred_at"]

    def test_limit_applied(self):
        routes = [self._route(f"2024-0{i+1}-01") for i in range(3)]
        points = [self._point(f"2024-0{i+4}-01") for i in range(3)]
        result = _geo_events(routes, points, limit=4)
        assert len(result) <= 4

    def test_route_kind_set(self):
        result = _geo_events([self._route()], [])
        assert result[0]["kind"] == "route"

    def test_point_kind_set(self):
        result = _geo_events([], [self._point()])
        assert result[0]["kind"] == "point"


# ---------------------------------------------------------------------------
# _conversation_thread_payload
# ---------------------------------------------------------------------------

class TestConversationThreadPayload:
    def _row(self, **overrides):
        base = {
            "thread_id": "thread-1",
            "source": "telegram",
            "entity_id": "eid-1",
            "peer_entity_id": "eid-2",
            "title": "Chat with Alice",
            "started_at": _NOW,
            "last_message_at": _LATER,
            "message_count": 10,
            "reply_count": 5,
            "reaction_count": 3,
            "forwarded_count": 1,
            "avg_response_seconds": 120.0,
            "sentiment_summary": {"positive": 7, "negative": 2},
            "preview": [{"text": "hello"}],
        }
        base.update(overrides)
        return base

    def test_basic_fields_mapped(self):
        result = _conversation_thread_payload(self._row())
        assert result["thread_id"] == "thread-1"
        assert result["source"] == "telegram"
        assert result["message_count"] == 10

    def test_datetime_fields_isoformatted(self):
        result = _conversation_thread_payload(self._row())
        assert "2024-06-01" in result["started_at"]
        assert "2024-06-02" in result["last_message_at"]

    def test_none_datetimes_become_none(self):
        result = _conversation_thread_payload(self._row(started_at=None, last_message_at=None))
        assert result["started_at"] is None
        assert result["last_message_at"] is None

    def test_non_dict_sentiment_becomes_empty(self):
        result = _conversation_thread_payload(self._row(sentiment_summary="positive"))
        assert result["sentiment_summary"] == {}

    def test_non_list_preview_becomes_empty(self):
        result = _conversation_thread_payload(self._row(preview="text"))
        assert result["preview"] == []

    def test_dict_sentiment_passthrough(self):
        summary = {"positive": 5}
        result = _conversation_thread_payload(self._row(sentiment_summary=summary))
        assert result["sentiment_summary"] == summary
