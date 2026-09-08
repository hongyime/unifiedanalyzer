"""
Pure-function tests — batch 19.

Covers previously untested modules with no DB or I/O:
- api.routes.entities: _decode_meta, _short, _decision_summary,
  _DECISION_ACTION_LABELS constants
- api.routes.intersections: _iso, _parse_json, _parse_latlng, _haversine_m
- api.routes.health: _env_int, _iso, _age_seconds
- api.routes.readiness: USER_STORIES constants
"""
from __future__ import annotations

import math
import pytest
from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# entities: _decode_meta, _short, _decision_summary, constants
# ---------------------------------------------------------------------------

class TestEntitiesDecodeMeta:
    def _d(self, raw):
        from src.api.routes.entities import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_invalid_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_array_json_returns_empty(self):
        assert self._d("[1, 2]") == {}


class TestEntitiesShort:
    def _s(self, value, max_len=96):
        from src.api.routes.entities import _short
        return _short(value, max_len=max_len)

    def test_short_string_unchanged(self):
        assert self._s("hello") == "hello"

    def test_none_returns_empty(self):
        assert self._s(None) == ""

    def test_truncates_long_string(self):
        long = "a" * 200
        result = self._s(long, max_len=96)
        assert len(result) <= 96
        assert result.endswith("...")

    def test_collapses_whitespace(self):
        assert self._s("hello   world") == "hello world"

    def test_exact_length_not_truncated(self):
        text = "a" * 96
        assert self._s(text, max_len=96) == text


class TestDecisionSummary:
    def _ds(self, action, payload):
        from src.api.routes.entities import _decision_summary
        return _decision_summary(action, payload)

    def test_merge_confirmed_with_target(self):
        result = self._ds("merge_confirmed", {"target_entity_id": "abcdef12-0000-0000-0000-000000000000", "merged_count": 2})
        assert "abcdef12" in result

    def test_merge_confirmed_no_payload(self):
        result = self._ds("merge_confirmed", {})
        assert result == "Merge confirmed"

    def test_split_person(self):
        result = self._ds("split_person", {"split_links": ["a", "b", "c"]})
        assert "3" in result

    def test_dismiss_match(self):
        result = self._ds("dismiss_match", {"entity_b": "12345678"})
        assert "same" in result.lower()

    def test_confirm_relationship(self):
        result = self._ds("confirm_relationship", {"relationship_type": "social_graph_overlap"})
        assert "confirmed" in result.lower()

    def test_reject_location(self):
        result = self._ds("reject_location", {"location_ref": {"source": "strava"}})
        assert "strava" in result or "rejected" in result.lower()

    def test_unknown_action_falls_through(self):
        from src.api.routes.entities import _DECISION_ACTION_LABELS
        # For unknown action, should return label or something
        from src.api.routes.entities import _decision_summary
        # Just verify it doesn't raise
        result = _decision_summary("unknown_action", {})
        assert isinstance(result, str)


class TestDecisionActionLabels:
    def test_non_empty(self):
        from src.api.routes.entities import _DECISION_ACTION_LABELS
        assert len(_DECISION_ACTION_LABELS) > 0

    def test_merge_entities_present(self):
        from src.api.routes.entities import _DECISION_ACTION_LABELS
        assert "merge_entities" in _DECISION_ACTION_LABELS

    def test_add_note_present(self):
        from src.api.routes.entities import _DECISION_ACTION_LABELS
        assert "add_note" in _DECISION_ACTION_LABELS


# ---------------------------------------------------------------------------
# intersections: _iso, _parse_json, _parse_latlng, _haversine_m
# ---------------------------------------------------------------------------

class TestIntersectionsIso:
    def _i(self, value):
        from src.api.routes.intersections import _iso
        return _iso(value)

    def test_datetime_returns_isoformat(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        assert "2026-01-15" in self._i(dt)

    def test_none_returns_none(self):
        assert self._i(None) is None


class TestIntersectionsParseJson:
    def _p(self, raw):
        from src.api.routes.intersections import _parse_json
        return _parse_json(raw)

    def test_dict_passthrough(self):
        assert self._p({"a": 1}) == {"a": 1}

    def test_list_passthrough(self):
        assert self._p([1, 2]) == [1, 2]

    def test_json_string_parsed(self):
        assert self._p('{"k": "v"}') == {"k": "v"}

    def test_invalid_json_returns_raw(self):
        assert self._p("bad") == "bad"

    def test_int_passthrough(self):
        assert self._p(42) == 42


class TestIntersectionsLatlng:
    def _p(self, raw):
        from src.api.routes.intersections import _parse_latlng
        return _parse_latlng(raw)

    def test_comma_string(self):
        result = self._p("1.3,103.8")
        assert result is not None
        assert abs(result[0] - 1.3) < 1e-9

    def test_bracket_string(self):
        result = self._p("[1.3, 103.8]")
        assert result is not None

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_empty_returns_none(self):
        assert self._p("") is None

    def test_invalid_returns_none(self):
        assert self._p("not-a-coord") is None


class TestHaversineM:
    def _h(self, a_lat, a_lng, b_lat, b_lng):
        from src.api.routes.intersections import _haversine_m
        return _haversine_m(a_lat, a_lng, b_lat, b_lng)

    def test_same_point_zero(self):
        assert self._h(1.3, 103.8, 1.3, 103.8) == pytest.approx(0.0, abs=0.1)

    def test_one_degree_latitude_approx_111km(self):
        dist = self._h(0.0, 0.0, 1.0, 0.0)
        assert 110000 < dist < 112000

    def test_singapore_to_kl_approx_350km(self):
        # Singapore ~1.35, 103.82 / KL ~3.14, 101.69
        dist = self._h(1.35, 103.82, 3.14, 101.69)
        assert 300000 < dist < 400000

    def test_positive_distance(self):
        assert self._h(0.0, 0.0, 0.0, 1.0) > 0


# ---------------------------------------------------------------------------
# health: _env_int, _iso, _age_seconds
# ---------------------------------------------------------------------------

class TestHealthEnvInt:
    def _e(self, name, default, minimum=None):
        from src.api.routes.health import _env_int
        return _env_int(name, default, minimum=minimum)

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("_TEST_HEALTH_INT", "42")
        assert self._e("_TEST_HEALTH_INT", 10) == 42

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_HEALTH_INT", raising=False)
        assert self._e("_TEST_HEALTH_INT", 7) == 7

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("_TEST_HEALTH_INT", "1")
        assert self._e("_TEST_HEALTH_INT", 10, minimum=5) == 5

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("_TEST_HEALTH_INT", "bad")
        assert self._e("_TEST_HEALTH_INT", 3) == 3


class TestHealthIso:
    def _i(self, v):
        from src.api.routes.health import _iso
        return _iso(v)

    def test_datetime_isoformat(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert "2026" in self._i(dt)

    def test_none_returns_none(self):
        assert self._i(None) is None


class TestHealthAgeSeconds:
    def _a(self, v):
        from src.api.routes.health import _age_seconds
        return _age_seconds(v)

    def test_none_returns_none(self):
        assert self._a(None) is None

    def test_recent_non_negative(self):
        recent = datetime.now(timezone.utc) - timedelta(seconds=10)
        assert self._a(recent) >= 0

    def test_iso_string(self):
        old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = self._a(old)
        assert result is not None and result >= 0

    def test_invalid_string_returns_none(self):
        assert self._a("not-a-date") is None

    def test_z_suffix_iso(self):
        old_z = (datetime.now(timezone.utc) - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = self._a(old_z)
        assert result is not None and result >= 0


# ---------------------------------------------------------------------------
# readiness: USER_STORIES constants
# ---------------------------------------------------------------------------

class TestReadinessUserStories:
    def test_non_empty(self):
        from src.api.routes.readiness import USER_STORIES
        assert len(USER_STORIES) > 0

    def test_databases_connected_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "databases_connected" in USER_STORIES

    def test_each_story_has_required_keys(self):
        from src.api.routes.readiness import USER_STORIES
        for key, story in USER_STORIES.items():
            assert "actor" in story, f"{key} missing 'actor'"
            assert "story" in story, f"{key} missing 'story'"
            assert "proves" in story, f"{key} missing 'proves'"

    def test_scheduler_self_healing_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "scheduler_self_healing" in USER_STORIES

    def test_face_identity_safety_present(self):
        from src.api.routes.readiness import USER_STORIES
        assert "face_identity_safety" in USER_STORIES
