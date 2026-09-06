"""
QA-lane tests for pure helper functions in:
- src/api/routes/entities.py: _decode_meta, _short, _decision_summary
- src/api/routes/graph.py: _relationship_why, _decode_sources, confidence_bucket
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# entities._decode_meta, _short, _decision_summary
# ---------------------------------------------------------------------------

from src.api.routes.entities import (
    _decision_summary,
    _decode_meta,
    _short,
)


class TestEntitiesDecodeMeta:
    def test_dict_passthrough(self):
        assert _decode_meta({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert _decode_meta('{"x": 2}') == {"x": 2}

    def test_json_array_returns_empty(self):
        assert _decode_meta("[1]") == {}

    def test_invalid_json_returns_empty(self):
        assert _decode_meta("bad{") == {}

    def test_none_returns_empty(self):
        assert _decode_meta(None) == {}


class TestShort:
    def test_short_string_unchanged(self):
        assert _short("hello") == "hello"

    def test_none_returns_empty_string(self):
        assert _short(None) == ""

    def test_truncates_at_max_len(self):
        result = _short("a" * 200, max_len=50)
        assert len(result) <= 50
        assert result.endswith("...")

    def test_exactly_max_len_not_truncated(self):
        text = "a" * 96
        assert _short(text, max_len=96) == text

    def test_collapses_whitespace(self):
        result = _short("hello   world")
        assert result == "hello world"

    def test_integer_coerced(self):
        assert _short(42) == "42"


class TestDecisionSummary:
    def test_merge_with_count(self):
        result = _decision_summary("merge_confirmed", {"merged_count": 3, "target_entity_id": "abc12345"})
        assert "3" in result and "abc1234" in result

    def test_merge_single_entity(self):
        result = _decision_summary("merge_entities", {"merged_count": 1})
        assert "1" in result

    def test_split_person(self):
        result = _decision_summary("split_person", {"split_links": ["a", "b", "c"]})
        assert "3" in result or "Split" in result

    def test_dismiss_match(self):
        result = _decision_summary("dismiss_match", {})
        assert "not same" in result.lower() or "dismiss" in result.lower()

    def test_confirm_relationship(self):
        result = _decision_summary("confirm_relationship", {"relationship_type": "social_graph"})
        assert "confirmed" in result.lower()

    def test_reject_relationship(self):
        result = _decision_summary("reject_relationship", {"relationship_type": "interaction"})
        assert "rejected" in result.lower()

    def test_unknown_action_handled(self):
        # Should not raise for unknown actions
        result = _decision_summary("unknown_action", {})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# graph._relationship_why, _decode_sources, confidence_bucket
# ---------------------------------------------------------------------------

from src.api.routes.graph import (
    _decode_sources,
    _relationship_why,
    confidence_bucket,
)


class TestRelationshipWhy:
    def test_explicit_why_returned(self):
        result = _relationship_why("any_type", {"why": "explicit reason"})
        assert result == "explicit reason"

    def test_interaction_by_type(self):
        result = _relationship_why("interaction", {"by_type": {"message": 5, "reply": 3}})
        assert "interaction" in result.lower() or "message" in result.lower()

    def test_group_co_member(self):
        result = _relationship_why("telegram_group_co_member", {"groups": ["Group A", "Group B"]})
        assert "Group A" in result

    def test_temporal_hour_similarity(self):
        result = _relationship_why("temporal_hour_similarity", {"similarity": 0.87})
        assert "0.87" in result

    def test_temporal_copost(self):
        result = _relationship_why("temporal_copost", {"coincident_events": 5, "copost_days": 3})
        assert "5" in result and "3" in result

    def test_same_person_probability(self):
        result = _relationship_why("same_person_probability", {
            "score": 0.95,
            "contributing_signals": [{"type": "email_match"}]
        })
        assert "0.95" in result or "email" in result

    def test_none_for_unknown_type_no_sources(self):
        result = _relationship_why("unknown_type", {})
        assert result is None

    def test_invalid_json_string_returns_none(self):
        result = _relationship_why("any", "not-json{")
        assert result is None

    def test_social_graph_overlap(self):
        result = _relationship_why("social_graph_overlap", {"shared": 5, "jaccard": 0.42})
        assert "5" in result and "0.42" in result


class TestDecodeSources:
    def test_dict_passthrough(self):
        assert _decode_sources({"a": 1}) == {"a": 1}

    def test_valid_json_string_parsed(self):
        assert _decode_sources('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert _decode_sources("bad{") == {}

    def test_non_dict_json_returns_parsed_value(self):
        # _decode_sources returns whatever json.loads gives — lists included
        result = _decode_sources("[1, 2]")
        assert result == [1, 2]

    def test_none_returns_empty(self):
        assert _decode_sources(None) == {}


class TestConfidenceBucket:
    def test_same_person_high_weight_is_hard(self):
        assert confidence_bucket("same_person_probability", 90.0) == "hard"

    def test_same_person_low_weight_is_strong(self):
        assert confidence_bucket("same_person_probability", 50.0) == "strong"

    def test_cross_platform_shared_phone_is_strong(self):
        assert confidence_bucket("shared_phone", 1.0, cross_platform=True) == "strong"

    def test_interaction_high_weight_is_weak(self):
        assert confidence_bucket("interaction", 5.0) == "weak"

    def test_interaction_low_weight_is_context_only(self):
        assert confidence_bucket("interaction", 1.0) == "context-only"

    def test_temporal_is_always_context_only(self):
        assert confidence_bucket("temporal_hour_similarity", 100.0) == "context-only"
        assert confidence_bucket("temporal_copost", 50.0) == "context-only"

    def test_unknown_type_high_weight_is_weak(self):
        assert confidence_bucket("some_other_type", 5.0) == "weak"

    def test_unknown_type_low_weight_is_context_only(self):
        assert confidence_bucket("some_other_type", 1.0) == "context-only"

    def test_none_type_and_none_weight(self):
        result = confidence_bucket(None, None)
        assert isinstance(result, str)
