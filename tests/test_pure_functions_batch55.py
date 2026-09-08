"""
Pure-function tests — batch 55.

Covers previously untested pure functions:
- util.audit_log: _decision_event_idempotency_key
- api.routes.entities: _decision_summary additional cases, SORT_COLUMNS constant
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib


# ---------------------------------------------------------------------------
# util.audit_log: _decision_event_idempotency_key
# ---------------------------------------------------------------------------

class TestDecisionEventIdempotencyKey:
    def _k(self, action="merge_confirmed", actor="dashboard",
           entity_ids=None, payload=None):
        from src.util.audit_log import _decision_event_idempotency_key
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        return _decision_event_idempotency_key(
            action=action,
            actor=actor,
            entity_ids=entity_ids or [],
            payload=payload or {},
            created_at=dt,
        )

    def test_returns_64_hex(self):
        result = self._k()
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._k() == self._k()

    def test_different_actions_differ(self):
        assert self._k(action="merge_confirmed") != self._k(action="split_person")

    def test_entity_ids_sorted(self):
        assert self._k(entity_ids=["b", "a"]) == self._k(entity_ids=["a", "b"])

    def test_different_actors_differ(self):
        assert self._k(actor="alice") != self._k(actor="bob")

    def test_none_actor_stable(self):
        assert self._k(actor=None) == self._k(actor=None)

    def test_different_payloads_differ(self):
        assert self._k(payload={"k": "v1"}) != self._k(payload={"k": "v2"})


# ---------------------------------------------------------------------------
# api.routes.entities: _decision_summary remaining cases
# ---------------------------------------------------------------------------

class TestDecisionSummaryAdditional:
    def _d(self, action, payload):
        from src.api.routes.entities import _decision_summary
        return _decision_summary(action, payload)

    def test_assign_target_tier(self):
        result = self._d("assign_target_tier", {"watch_status": "priority"})
        assert "priority" in result

    def test_assign_target_tier_default(self):
        result = self._d("assign_target_tier", {})
        assert "default" in result or "Watch" in result

    def test_add_note_with_text(self):
        result = self._d("add_note", {"notes": "This is important"})
        assert "This is important" in result

    def test_add_note_empty_payload(self):
        result = self._d("add_note", {})
        assert "Note" in result or "note" in result.lower()

    def test_adjust_source_confidence_with_value(self):
        result = self._d("adjust_source_confidence", {"source": "instagram", "confidence": 0.8})
        assert "instagram" in result
        assert "0.8" in result

    def test_adjust_source_confidence_no_value(self):
        result = self._d("adjust_source_confidence", {"source": "telegram"})
        assert "telegram" in result
        assert "adjusted" in result.lower()

    def test_assign_media_owner(self):
        result = self._d("assign_media_owner", {"role": "owner"})
        assert "owner" in result.lower() or "assigned" in result.lower()

    def test_reject_person_in_photo(self):
        result = self._d("reject_person_in_photo", {})
        assert "person" in result.lower() or "rejected" in result.lower()

    def test_unknown_action_falls_through(self):
        result = self._d("unknown_action_xyz", {})
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# api.routes.entities: SORT_COLUMNS constant
# ---------------------------------------------------------------------------

class TestSortColumns:
    def test_non_empty(self):
        from src.api.routes.entities import SORT_COLUMNS
        assert len(SORT_COLUMNS) > 0

    def test_name_present(self):
        from src.api.routes.entities import SORT_COLUMNS
        assert "name" in SORT_COLUMNS

    def test_confidence_present(self):
        from src.api.routes.entities import SORT_COLUMNS
        assert "confidence" in SORT_COLUMNS

    def test_all_values_strings(self):
        from src.api.routes.entities import SORT_COLUMNS
        for col in SORT_COLUMNS.values():
            assert isinstance(col, str) and len(col) > 0

    def test_created_present(self):
        from src.api.routes.entities import SORT_COLUMNS
        assert "created" in SORT_COLUMNS


# ---------------------------------------------------------------------------
# util.audit_log: _decision_log_path edge cases
# ---------------------------------------------------------------------------

class TestDecisionLogPathEdgeCases:
    def test_december_path(self):
        from src.util.audit_log import _decision_log_path
        dt = datetime(2026, 12, 31, tzinfo=timezone.utc)
        assert "2026-12" in str(_decision_log_path(dt))

    def test_january_path(self):
        from src.util.audit_log import _decision_log_path
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert "2026-01" in str(_decision_log_path(dt))

    def test_different_months_different_paths(self):
        from src.util.audit_log import _decision_log_path
        jan = _decision_log_path(datetime(2026, 1, 15, tzinfo=timezone.utc))
        feb = _decision_log_path(datetime(2026, 2, 15, tzinfo=timezone.utc))
        assert jan != feb
