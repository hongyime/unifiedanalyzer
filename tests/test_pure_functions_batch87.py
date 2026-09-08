"""
Pure-function tests — batch 87.

Covers:
- pipeline.account_proximity: _platform, _account, _reason,
  _split_env_set, _telegram_t1_max_group_size
- pipeline.face_bridge_audit: _as_list, _iso, _face_sample, _cluster_sample
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _platform
# ---------------------------------------------------------------------------

class TestPlatform:
    def _p(self, value):
        from src.pipeline.account_proximity import _platform
        return _platform(value)

    def test_twitter_aliased_to_x(self):
        assert self._p("twitter") == "x"

    def test_ig_aliased_to_instagram(self):
        assert self._p("ig") == "instagram"

    def test_known_platform_passthrough(self):
        assert self._p("telegram") == "telegram"

    def test_none_returns_empty(self):
        assert self._p(None) == ""

    def test_lowercased(self):
        assert self._p("INSTAGRAM") == "instagram"

    def test_spaces_replaced_with_underscore(self):
        result = self._p("some platform")
        assert " " not in result


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _account
# ---------------------------------------------------------------------------

class TestAccount:
    def _a(self, value, platform=None):
        from src.pipeline.account_proximity import _account
        return _account(value, platform)

    def test_none_returns_empty(self):
        assert self._a(None) == ""

    def test_strips_whitespace(self):
        assert self._a("  alice  ") == "alice"

    def test_github_strips_at_and_lowercases(self):
        assert self._a("@Alice", "github") == "alice"

    def test_instagram_strips_at(self):
        assert self._a("@bob", "instagram") == "bob"

    def test_non_social_platform_preserves_case(self):
        result = self._a("Alice", "strava")
        assert result == "Alice"

    def test_empty_string_returns_empty(self):
        assert self._a("") == ""


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _reason
# ---------------------------------------------------------------------------

class TestReason:
    def _r(self, reason_type, **detail):
        from src.pipeline.account_proximity import _reason
        return _reason(reason_type, **detail)

    def test_type_in_result(self):
        result = self._r("shared_group")
        assert result["type"] == "shared_group"

    def test_none_values_excluded(self):
        result = self._r("test", key=None, other="val")
        assert "key" not in result
        assert result["other"] == "val"

    def test_empty_string_excluded(self):
        result = self._r("test", key="", other="val")
        assert "key" not in result

    def test_empty_list_excluded(self):
        result = self._r("test", items=[])
        assert "items" not in result

    def test_valid_values_included(self):
        result = self._r("group_co_member", group="crypto_chat", count=5)
        assert result["group"] == "crypto_chat"
        assert result["count"] == 5


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _split_env_set
# ---------------------------------------------------------------------------

class TestSplitEnvSet:
    def _s(self, name, value=None):
        from src.pipeline.account_proximity import _split_env_set
        if value is not None:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
        try:
            return _split_env_set(name)
        finally:
            os.environ.pop(name, None)

    def test_unset_returns_empty(self):
        assert self._s("__AP_UNSET__") == set()

    def test_comma_separated(self):
        result = self._s("__AP_A__", "alice,bob,charlie")
        assert result == {"alice", "bob", "charlie"}

    def test_lowercased(self):
        result = self._s("__AP_B__", "Alice,BOB")
        assert "alice" in result
        assert "bob" in result

    def test_whitespace_stripped(self):
        result = self._s("__AP_C__", " alice , bob ")
        assert "alice" in result


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _telegram_t1_max_group_size
# ---------------------------------------------------------------------------

class TestTelegramT1MaxGroupSize:
    def _t(self, value=None):
        from src.pipeline.account_proximity import _telegram_t1_max_group_size
        key = "PROXIMITY_TELEGRAM_T1_MAX_GROUP_SIZE"
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _telegram_t1_max_group_size()
        finally:
            os.environ.pop(key, None)

    def test_default_50(self):
        assert self._t() == 50

    def test_custom_value(self):
        assert self._t(100) == 100

    def test_minimum_2_enforced(self):
        assert self._t(1) == 2

    def test_invalid_returns_50(self):
        assert self._t("bad") == 50


# ---------------------------------------------------------------------------
# pipeline.face_bridge_audit: _as_list
# ---------------------------------------------------------------------------

class TestFaceAuditAsList:
    def _l(self, value):
        from src.pipeline.face_bridge_audit import _as_list
        return _as_list(value)

    def test_none_returns_empty(self):
        assert self._l(None) == []

    def test_list_returns_str_list(self):
        result = self._l([1, 2, 3])
        assert result == ["1", "2", "3"]

    def test_tuple_works(self):
        result = self._l((1, 2))
        assert result == ["1", "2"]

    def test_scalar_wrapped(self):
        result = self._l("hello")
        assert result == ["hello"]

    def test_none_items_excluded(self):
        result = self._l([1, None, 3])
        assert None not in result
        assert "1" in result
        assert "3" in result


# ---------------------------------------------------------------------------
# pipeline.face_bridge_audit: _iso
# ---------------------------------------------------------------------------

class TestFaceAuditIso:
    def _i(self, value):
        from src.pipeline.face_bridge_audit import _iso
        return _iso(value)

    def test_datetime_isoformatted(self):
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        assert self._i(dt) == dt.isoformat()

    def test_none_returns_none(self):
        assert self._i(None) is None

    def test_string_returned_as_is(self):
        assert self._i("2026-01-15") == "2026-01-15"

    def test_zero_returns_none(self):
        assert self._i(0) is None


# ---------------------------------------------------------------------------
# pipeline.face_bridge_audit: _face_sample
# ---------------------------------------------------------------------------

class TestFaceSample:
    def _make(self, **kw):
        defaults = {
            "face_id": 42, "entity_count": 2,
            "entity_ids": ["eid-1", "eid-2"],
            "entity_names": ["Alice", "Bob"],
            "methods": ["direct"],
            "latest_created_at": None,
        }
        defaults.update(kw)
        return defaults

    def _f(self, **kw):
        from src.pipeline.face_bridge_audit import _face_sample
        return _face_sample(self._make(**kw))

    def test_all_keys_present(self):
        result = self._f()
        for k in ("face_id", "entity_count", "entity_ids",
                  "entity_names", "methods", "latest_created_at"):
            assert k in result

    def test_face_id_int(self):
        assert self._f(face_id=42)["face_id"] == 42

    def test_entity_count_int(self):
        assert self._f(entity_count="3")["entity_count"] == 3


# ---------------------------------------------------------------------------
# pipeline.face_bridge_audit: _cluster_sample
# ---------------------------------------------------------------------------

class TestClusterSample:
    def _make(self, **kw):
        defaults = {
            "cluster_id": 7, "entity_count": 1, "face_count": 3,
            "entity_ids": ["eid-1"],
            "entity_names": ["Alice"],
            "methods": ["propagated"],
            "latest_created_at": None,
        }
        defaults.update(kw)
        return defaults

    def _c(self, **kw):
        from src.pipeline.face_bridge_audit import _cluster_sample
        return _cluster_sample(self._make(**kw))

    def test_all_keys_present(self):
        result = self._c()
        for k in ("cluster_id", "entity_count", "face_count",
                  "entity_ids", "entity_names", "methods", "latest_created_at"):
            assert k in result

    def test_cluster_id_int(self):
        assert self._c(cluster_id=7)["cluster_id"] == 7

    def test_face_count_int(self):
        assert self._c(face_count="5")["face_count"] == 5
