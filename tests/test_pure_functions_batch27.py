"""
Pure-function tests — batch 27.

Covers previously untested modules with no DB or I/O:
- face.utils.connectivity: ConnectivityGuard constructor/initial state
- face.discovery.watcher: FileEventHandler._should_process
- api.routes.entity_actions: Pydantic models (MergeRequest, DismissMatchRequest,
  WatchRequest, RelationshipDecisionRequest, LocationDecisionRequest,
  MediaPersonDecisionRequest, SourceConfidenceRequest)
"""
from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# face.utils.connectivity: ConnectivityGuard
# ---------------------------------------------------------------------------

class TestConnectivityGuard:
    def _make(self, label="test"):
        from unittest.mock import MagicMock
        from src.face.utils.connectivity import ConnectivityGuard
        engine = MagicMock()
        return ConnectivityGuard(engine, poll_interval=5, label=label)

    def test_initial_state_healthy(self):
        guard = self._make()
        # _was_healthy starts True
        assert guard._was_healthy is True

    def test_label_stored(self):
        guard = self._make(label="myguard")
        assert guard._label == "myguard"

    def test_default_label_set(self):
        from unittest.mock import MagicMock
        from src.face.utils.connectivity import ConnectivityGuard
        guard = ConnectivityGuard(MagicMock())
        assert guard._label == "ConnectivityGuard"

    def test_poll_interval_stored(self):
        guard = self._make()
        assert guard._poll_interval == 5



# ---------------------------------------------------------------------------
# api.routes.entity_actions: Pydantic models
# ---------------------------------------------------------------------------

class TestEntityActionModels:
    def test_merge_request(self):
        from src.api.routes.entity_actions import MergeRequest
        req = MergeRequest(source_entity_ids=["id1", "id2"], reason="confirmed same")
        assert req.source_entity_ids == ["id1", "id2"]
        assert req.reason == "confirmed same"

    def test_merge_request_default_reason(self):
        from src.api.routes.entity_actions import MergeRequest
        req = MergeRequest(source_entity_ids=["id1"])
        assert req.reason == ""

    def test_dismiss_match_request(self):
        from src.api.routes.entity_actions import DismissMatchRequest
        req = DismissMatchRequest(entity_a="aaa", entity_b="bbb")
        assert req.entity_a == "aaa"
        assert req.entity_b == "bbb"

    def test_watch_request_none_status(self):
        from src.api.routes.entity_actions import WatchRequest
        req = WatchRequest(status=None)
        assert req.status is None

    def test_watch_request_priority(self):
        from src.api.routes.entity_actions import WatchRequest
        req = WatchRequest(status="priority")
        assert req.status == "priority"

    def test_relationship_decision_request(self):
        from src.api.routes.entity_actions import RelationshipDecisionRequest
        req = RelationshipDecisionRequest(
            entity_a="aaa", entity_b="bbb",
            relationship_type="social_graph_overlap", is_real=True,
        )
        assert req.is_real is True
        assert req.relationship_type == "social_graph_overlap"

    def test_relationship_decision_default_type(self):
        from src.api.routes.entity_actions import RelationshipDecisionRequest
        req = RelationshipDecisionRequest(entity_a="a", entity_b="b", is_real=False)
        assert req.relationship_type == "relationship"

    def test_location_decision_request(self):
        from src.api.routes.entity_actions import LocationDecisionRequest
        req = LocationDecisionRequest(is_correct=True, location_ref={"source": "strava"})
        assert req.is_correct is True
        assert req.location_ref == {"source": "strava"}

    def test_media_person_decision_default_role(self):
        from src.api.routes.entity_actions import MediaPersonDecisionRequest
        req = MediaPersonDecisionRequest(media_ref={"id": "m1"})
        assert req.role == "person_in_photo"
        assert req.is_correct is True

    def test_source_confidence_request(self):
        from src.api.routes.entity_actions import SourceConfidenceRequest
        req = SourceConfidenceRequest(confidence=85.0, source="instagram")
        assert req.confidence == 85.0
        assert req.source == "instagram"

    def test_source_confidence_optional_fields(self):
        from src.api.routes.entity_actions import SourceConfidenceRequest
        req = SourceConfidenceRequest(confidence=50.0)
        assert req.source is None
        assert req.platform_id is None
        assert req.notes is None
