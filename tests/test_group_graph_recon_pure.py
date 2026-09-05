"""
QA-lane tests for pure functions in:
- src/pipeline/group_graph.py: _group_weight, _effective_group_size
- src/pipeline/recon_bridge.py: _extract_value
"""
from __future__ import annotations

import pytest

from src.pipeline.group_graph import _effective_group_size, _group_weight
from src.pipeline.recon_bridge import _extract_value


# ---------------------------------------------------------------------------
# group_graph._group_weight
# ---------------------------------------------------------------------------

class TestGroupWeight:
    def test_two_members_gives_1(self):
        assert _group_weight(2) == 1.0

    def test_ten_members_gives_one_ninth(self):
        assert abs(_group_weight(10) - 1.0 / 9) < 1e-9

    def test_one_member_gives_0(self):
        assert _group_weight(1) == 0.0

    def test_zero_members_gives_0(self):
        assert _group_weight(0) == 0.0

    def test_negative_gives_0(self):
        assert _group_weight(-5) == 0.0

    def test_large_group_gives_tiny_weight(self):
        result = _group_weight(12001)
        assert result is not None
        assert result < 0.001

    def test_weight_decreases_with_size(self):
        assert _group_weight(5) > _group_weight(10)


# ---------------------------------------------------------------------------
# group_graph._effective_group_size
# ---------------------------------------------------------------------------

class TestEffectiveGroupSize:
    def test_true_size_preferred_when_larger(self):
        assert _effective_group_size(100, 7) == 100

    def test_tracked_count_used_when_true_size_none(self):
        assert _effective_group_size(None, 7) == 7

    def test_tracked_count_used_when_true_size_smaller(self):
        # true_size < tracked_count → use tracked (stale data protection)
        assert _effective_group_size(5, 7) == 7

    def test_true_size_used_when_equal(self):
        assert _effective_group_size(7, 7) == 7

    def test_zero_true_size_uses_tracked(self):
        assert _effective_group_size(0, 7) == 7

    def test_zero_tracked_count(self):
        assert _effective_group_size(100, 0) == 100


# ---------------------------------------------------------------------------
# recon_bridge._extract_value
# ---------------------------------------------------------------------------

class TestExtractValue:
    def test_account_obs_extracts_sfurl(self):
        raw = "<SFURL>https://github.com/johndoe</SFURL>"
        result = _extract_value("ACCOUNT_EXTERNAL_OWNED", raw)
        assert result == "https://github.com/johndoe"

    def test_similar_account_extracts_sfurl(self):
        raw = "noise <SFURL>https://twitter.com/alice</SFURL> more"
        result = _extract_value("SIMILAR_ACCOUNT_EXTERNAL", raw)
        assert result == "https://twitter.com/alice"

    def test_non_account_type_returns_stripped_value(self):
        result = _extract_value("EMAIL_ADDRESS", "  alice@example.com  ")
        assert result == "alice@example.com"

    def test_account_type_without_sfurl_returns_stripped_value(self):
        result = _extract_value("ACCOUNT_EXTERNAL_OWNED", "bare value")
        assert result == "bare value"

    def test_empty_raw_returns_empty(self):
        assert _extract_value("EMAIL_ADDRESS", "") == ""

    def test_none_raw_returns_empty(self):
        assert _extract_value("EMAIL_ADDRESS", None) == ""

    def test_strips_whitespace_on_sfurl_match(self):
        raw = "<SFURL>  https://github.com/johndoe  </SFURL>"
        result = _extract_value("ACCOUNT_EXTERNAL_OWNED", raw)
        assert result == "https://github.com/johndoe"
