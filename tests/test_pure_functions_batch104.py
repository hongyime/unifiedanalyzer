"""
Pure-function tests — batch 104.

Covers:
- pipeline.decision_replay: summarize_payload, stable_refs_from_event
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.decision_replay: summarize_payload
# ---------------------------------------------------------------------------

class TestSummarizePayload:
    def _s(self, payload):
        from src.pipeline.decision_replay import summarize_payload
        return summarize_payload(payload)

    def test_empty_returns_empty(self):
        assert self._s({}) == {}

    def test_known_keys_included(self):
        result = self._s({"watch_status": "active", "confidence": 0.9})
        assert result["watch_status"] == "active"
        assert abs(result["confidence"] - 0.9) < 1e-9

    def test_unknown_keys_excluded(self):
        result = self._s({"unknown_key": "value", "confidence": 0.5})
        assert "unknown_key" not in result
        assert "confidence" in result

    def test_long_string_truncated(self):
        result = self._s({"notes": "x" * 200})
        assert len(result["notes"]) == 160
        assert result["notes"].endswith("...")

    def test_short_string_not_truncated(self):
        result = self._s({"notes": "short note"})
        assert result["notes"] == "short note"

    def test_all_known_keys_extracted(self):
        payload = {
            "watch_status": "active", "relationship_type": "colleague",
            "is_real": True, "is_correct": False, "role": "analyst",
            "confidence": 0.8, "source": "manual", "platform_id": "p1",
            "notes": "ok", "reason": "merge",
        }
        result = self._s(payload)
        for k in payload:
            assert k in result

    def test_none_value_included(self):
        result = self._s({"confidence": None})
        assert "confidence" in result
        assert result["confidence"] is None


# ---------------------------------------------------------------------------
# pipeline.decision_replay: stable_refs_from_event
# ---------------------------------------------------------------------------

class TestStableRefsFromEvent:
    def _r(self, event):
        from src.pipeline.decision_replay import stable_refs_from_event
        return stable_refs_from_event(event)

    def test_empty_event_returns_empty(self):
        assert self._r({}) == []

    def test_stable_refs_used_when_present(self):
        event = {
            "stable_refs": [
                {"source": "instagram", "platform_id": "alice", "platform_username": None}
            ]
        }
        result = self._r(event)
        assert len(result) == 1
        assert result[0]["source"] == "instagram"

    def test_ref_without_source_skipped(self):
        event = {
            "stable_refs": [
                {"source": None, "platform_id": "alice"}
            ]
        }
        assert self._r(event) == []

    def test_ref_without_platform_id_or_username_skipped(self):
        event = {
            "stable_refs": [
                {"source": "instagram", "platform_id": None, "platform_username": None}
            ]
        }
        assert self._r(event) == []

    def test_deduplicates_refs(self):
        event = {
            "stable_refs": [
                {"source": "instagram", "platform_id": "alice", "platform_username": None},
                {"source": "instagram", "platform_id": "alice", "platform_username": None},
            ]
        }
        result = self._r(event)
        assert len(result) == 1

    def test_payload_platform_links_fallback(self):
        event = {
            "payload": {
                "platform_links": [
                    {"source": "telegram", "platform_id": "123", "platform_username": "bob"}
                ]
            }
        }
        result = self._r(event)
        assert len(result) == 1
        assert result[0]["source"] == "telegram"

    def test_stable_refs_takes_priority_over_payload(self):
        event = {
            "stable_refs": [
                {"source": "instagram", "platform_id": "alice", "platform_username": None}
            ],
            "payload": {
                "platform_links": [
                    {"source": "telegram", "platform_id": "123", "platform_username": "bob"}
                ]
            }
        }
        result = self._r(event)
        # stable_refs are found → payload not traversed
        assert all(r["source"] == "instagram" for r in result)
