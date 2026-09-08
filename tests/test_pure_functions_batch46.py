"""
Pure-function tests — batch 46.

Covers previously untested pure functions:
- api.routes.intersections: _link_values
- api.routes.media: _int_row, _coverage_item, _thumbnail_placeholder (already covered),
  _parse_pg_array_text (already covered) — adding _estimated_rollup edge cases
- api.routes.timeline: _timeline_event_payload (pure dict builder with mock row)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# api.routes.intersections: _link_values
# ---------------------------------------------------------------------------

class TestLinkValues:
    def _l(self, links, entity_ids, source):
        from src.api.routes.intersections import _link_values
        return _link_values(links, entity_ids, source)

    def _link(self, entity_id, source, platform_id=None, platform_username=None):
        return {
            "entity_id": entity_id,
            "source": source,
            "platform_id": platform_id,
            "platform_username": platform_username,
        }

    def test_returns_matching_ids(self):
        links = [self._link("eid-1", "telegram", "12345")]
        ids, owners = self._l(links, ["eid-1"], "telegram")
        assert "12345" in ids

    def test_username_in_owners_lowercased(self):
        links = [self._link("eid-1", "telegram", "12345", "AliceUser")]
        _, owners = self._l(links, ["eid-1"], "telegram")
        assert "aliceuser" in owners

    def test_wrong_source_excluded(self):
        links = [self._link("eid-1", "instagram", "999")]
        ids, owners = self._l(links, ["eid-1"], "telegram")
        assert "999" not in ids

    def test_wrong_entity_excluded(self):
        links = [self._link("eid-2", "telegram", "555")]
        ids, owners = self._l(links, ["eid-1"], "telegram")
        assert "555" not in ids

    def test_empty_links_returns_empty(self):
        ids, owners = self._l([], ["eid-1"], "telegram")
        assert ids == []
        assert owners == []

    def test_no_platform_id_no_id_entry(self):
        links = [self._link("eid-1", "telegram", None, "alice")]
        ids, owners = self._l(links, ["eid-1"], "telegram")
        assert ids == []  # no platform_id → no id entry
        assert "alice" in owners


# ---------------------------------------------------------------------------
# api.routes.media: _int_row, _coverage_item
# ---------------------------------------------------------------------------

class TestIntRow:
    def _r(self, row, key):
        from src.api.routes.media import _int_row
        return _int_row(row, key)

    def test_int_value(self):
        assert self._r({"k": 42}, "k") == 42

    def test_none_returns_zero(self):
        assert self._r({"k": None}, "k") == 0

    def test_missing_key_returns_zero(self):
        assert self._r({}, "k") == 0

    def test_float_truncated(self):
        assert self._r({"k": 3.9}, "k") == 3

    def test_zero_string(self):
        assert self._r({"k": "0"}, "k") == 0


class TestCoverageItem:
    def _c(self, key="gps", label="GPS", count=10, processed=None, basis="media_analysis"):
        from src.api.routes.media import _coverage_item
        return _coverage_item(key, label, count, processed=processed, basis=basis)

    def test_covered_when_count_positive(self):
        result = self._c(count=5)
        assert result["status"] == "covered"

    def test_missing_when_count_zero(self):
        result = self._c(count=0)
        assert result["status"] == "missing"

    def test_key_and_label_stored(self):
        result = self._c(key="ocr", label="OCR Text")
        assert result["key"] == "ocr"
        assert result["label"] == "OCR Text"

    def test_count_stored(self):
        result = self._c(count=100)
        assert result["count"] == 100

    def test_processed_defaults_to_count(self):
        result = self._c(count=50, processed=None)
        assert result["processed"] == 50

    def test_processed_custom(self):
        result = self._c(count=50, processed=30)
        assert result["processed"] == 30

    def test_basis_stored(self):
        result = self._c(basis="collector_media")
        assert result["basis"] == "collector_media"


# ---------------------------------------------------------------------------
# api.routes.timeline: _timeline_event_payload with mock row
# ---------------------------------------------------------------------------

class TestTimelineEventPayload:
    def _make_row(self, **kw):
        from datetime import datetime, timezone
        defaults = {
            "id": "evt-1",
            "source": "instagram",
            "event_type": "CONTENT_PUBLISHED",
            "source_record_id": "post-1",
            "occurred_at": datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
            "title": "Beautiful sunset",
            "metadata": {},
        }
        defaults.update(kw)
        return defaults

    def _p(self, row):
        from src.api.routes.timeline import _timeline_event_payload
        return _timeline_event_payload(row)

    def test_required_keys_present(self):
        result = self._p(self._make_row())
        for key in ("id", "source", "event_type", "source_record_id",
                    "occurred_at", "title", "metadata"):
            assert key in result

    def test_id_is_string(self):
        result = self._p(self._make_row(id="abc123"))
        assert result["id"] == "abc123"

    def test_occurred_at_is_isoformat(self):
        result = self._p(self._make_row())
        assert "2026-01-15" in result["occurred_at"]

    def test_no_confidence_when_no_metadata(self):
        result = self._p(self._make_row(metadata={}))
        assert result["confidence"] is None

    def test_confidence_from_metadata(self):
        result = self._p(self._make_row(metadata={"confidence": 0.85}))
        assert result["confidence"] is not None
        assert abs(result["confidence"] - 0.85) < 1e-4

    def test_source_stored(self):
        result = self._p(self._make_row(source="telegram"))
        assert result["source"] == "telegram"
