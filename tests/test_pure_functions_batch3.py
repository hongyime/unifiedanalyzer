"""
Pure-function tests — batch 3.

Covers previously untested modules with no DB or I/O:
- pipeline.graph_overlap: _norm
- pipeline.route_similarity: _cluster
- pipeline.account_proximity: _platform, _account, _reason,
  _parse_beeper_native_id, _split_env_set, _telegram_t1_max_group_size,
  _best_owner_by_platform, _owner_for_peer_link,
  ProximityRow, ProximityAccumulator.add / entity_tier / records
- pipeline.conversation_analytics: _iso, _decode, _thread_key
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# graph_overlap._norm
# ---------------------------------------------------------------------------

class TestNormGraphOverlap:
    def _norm(self, s):
        from src.pipeline.graph_overlap import _norm
        return _norm(s)

    def test_strips_and_lowercases(self):
        assert self._norm("  Hello ") == "hello"

    def test_none_returns_none(self):
        assert self._norm(None) is None

    def test_empty_string_returns_none(self):
        assert self._norm("") is None

    def test_already_lower(self):
        assert self._norm("abc") == "abc"

    def test_mixed_case(self):
        assert self._norm("FooBar") == "foobar"


# ---------------------------------------------------------------------------
# route_similarity._cluster
# ---------------------------------------------------------------------------

class TestClusterRouteSimilarity:
    def _cluster(self, latlng):
        from src.pipeline.route_similarity import _cluster
        return _cluster(latlng)

    def test_valid_latlng(self):
        result = self._cluster("1.23456,103.98765")
        assert result == (1.235, 103.988)

    def test_precision_3_decimal_places(self):
        lat, lng = self._cluster("37.123456,121.987654")
        assert lat == round(37.123456, 3)
        assert lng == round(121.987654, 3)

    def test_none_returns_none(self):
        assert self._cluster(None) is None

    def test_empty_returns_none(self):
        assert self._cluster("") is None

    def test_invalid_format_returns_none(self):
        assert self._cluster("not-a-coord") is None

    def test_single_value_returns_none(self):
        assert self._cluster("1.234") is None

    def test_negative_coords(self):
        result = self._cluster("-33.8688,151.2093")
        assert result is not None
        assert result[0] < 0


# ---------------------------------------------------------------------------
# account_proximity pure helpers
# ---------------------------------------------------------------------------

class TestPlatformNorm:
    def _p(self, v):
        from src.pipeline.account_proximity import _platform
        return _platform(v)

    def test_twitter_alias(self):
        assert self._p("twitter") == "x"

    def test_twitter_x_alias(self):
        assert self._p("twitter/x") == "x"

    def test_ig_alias(self):
        assert self._p("ig") == "instagram"

    def test_instagramgo_alias(self):
        assert self._p("instagramgo") == "instagram"

    def test_telegramgo_alias(self):
        assert self._p("telegramgo") == "telegram"

    def test_whatsappgo_alias(self):
        assert self._p("whatsappgo") == "whatsapp"

    def test_passthrough(self):
        assert self._p("github") == "github"

    def test_spaces_become_underscores(self):
        assert self._p("some platform") == "some_platform"

    def test_none_becomes_empty(self):
        assert self._p(None) == ""

    def test_uppercase_lowercased(self):
        assert self._p("GitHub") == "github"


class TestAccountNorm:
    def _a(self, v, platform=None):
        from src.pipeline.account_proximity import _account
        return _account(v, platform)

    def test_strips_at_for_github(self):
        assert self._a("@octocat", "github") == "octocat"

    def test_strips_at_for_instagram(self):
        assert self._a("@user", "instagram") == "user"

    def test_strips_at_for_tiktok(self):
        assert self._a("@tikuser", "tiktok") == "tikuser"

    def test_no_at_strip_for_telegram(self):
        # telegram platform_ids are numeric strings — no @ stripping
        assert self._a("123456", "telegram") == "123456"

    def test_no_at_strip_for_whatsapp(self):
        assert self._a("6591234567@s.whatsapp.net", "whatsapp") == "6591234567@s.whatsapp.net"

    def test_empty_returns_empty(self):
        assert self._a("") == ""

    def test_none_returns_empty(self):
        assert self._a(None) == ""

    def test_lowercases_github(self):
        assert self._a("OctoCAT", "github") == "octocat"


class TestReasonHelper:
    def _r(self, rtype, **kw):
        from src.pipeline.account_proximity import _reason
        return _reason(rtype, **kw)

    def test_basic_reason(self):
        r = self._r("mutual_follow", username="alice")
        assert r["type"] == "mutual_follow"
        assert r["username"] == "alice"

    def test_none_values_excluded(self):
        r = self._r("dm_contact", messages=5, username=None)
        assert "username" not in r
        assert r["messages"] == 5

    def test_empty_string_excluded(self):
        r = self._r("tag", username="")
        assert "username" not in r

    def test_empty_list_excluded(self):
        r = self._r("group", members=[])
        assert "members" not in r

    def test_type_always_present(self):
        r = self._r("some_type")
        assert r["type"] == "some_type"


class TestSplitEnvSet:
    def test_comma_separated(self, monkeypatch):
        monkeypatch.setenv("_TEST_SPLIT", "alice,bob,charlie")
        from src.pipeline.account_proximity import _split_env_set
        result = _split_env_set("_TEST_SPLIT")
        assert result == {"alice", "bob", "charlie"}

    def test_empty_env_returns_empty(self, monkeypatch):
        monkeypatch.delenv("_TEST_SPLIT", raising=False)
        from src.pipeline.account_proximity import _split_env_set
        assert _split_env_set("_TEST_SPLIT") == set()

    def test_whitespace_separated(self, monkeypatch):
        monkeypatch.setenv("_TEST_SPLIT", "alice bob")
        from src.pipeline.account_proximity import _split_env_set
        result = _split_env_set("_TEST_SPLIT")
        assert "alice" in result and "bob" in result

    def test_lowercased(self, monkeypatch):
        monkeypatch.setenv("_TEST_SPLIT", "ALICE")
        from src.pipeline.account_proximity import _split_env_set
        assert "alice" in _split_env_set("_TEST_SPLIT")


class TestTelegramT1MaxGroupSize:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("PROXIMITY_TELEGRAM_T1_MAX_GROUP_SIZE", raising=False)
        from src.pipeline.account_proximity import _telegram_t1_max_group_size
        assert _telegram_t1_max_group_size() == 50

    def test_custom(self, monkeypatch):
        monkeypatch.setenv("PROXIMITY_TELEGRAM_T1_MAX_GROUP_SIZE", "20")
        from src.pipeline.account_proximity import _telegram_t1_max_group_size
        assert _telegram_t1_max_group_size() == 20

    def test_invalid_returns_50(self, monkeypatch):
        monkeypatch.setenv("PROXIMITY_TELEGRAM_T1_MAX_GROUP_SIZE", "bad")
        from src.pipeline.account_proximity import _telegram_t1_max_group_size
        assert _telegram_t1_max_group_size() == 50

    def test_minimum_is_2(self, monkeypatch):
        monkeypatch.setenv("PROXIMITY_TELEGRAM_T1_MAX_GROUP_SIZE", "1")
        from src.pipeline.account_proximity import _telegram_t1_max_group_size
        assert _telegram_t1_max_group_size() == 2


class TestParseBeeperNativeId:
    def _parse(self, network, participant_id, full_name=None):
        from src.pipeline.account_proximity import _parse_beeper_native_id
        return _parse_beeper_native_id(network, participant_id, full_name)

    def test_instagram_prefix(self):
        # Regex prefix group captures the literal word before '_'; must be 'instagram' (no 'go')
        platform, account = self._parse("beeper", "@instagram_octocat:beeper.com")
        assert platform == "instagram"
        assert account == "octocat"

    def test_telegram_prefix(self):
        platform, account = self._parse("beeper", "@telegram_123456:beeper.com")
        assert platform == "telegram"
        assert account == "123456"

    def test_whatsapp_prefix_uses_full_name_phone(self):
        platform, account = self._parse("beeper", "@whatsapp_xyz:beeper.com", full_name="6591234567")
        assert platform == "whatsapp"
        assert "6591234567" in account

    def test_no_match_fallback(self):
        platform, account = self._parse("telegram", "123456", None)
        assert platform == "telegram"
        assert account == "123456"

    def test_empty_participant_id(self):
        platform, account = self._parse("telegram", "", None)
        assert isinstance(platform, str)
        assert isinstance(account, str)


class TestProximityRow:
    def test_initial_tier(self):
        from src.pipeline.account_proximity import ProximityRow
        row = ProximityRow(tier=2)
        assert row.tier == 2

    def test_add_lower_tier_promotes(self):
        from src.pipeline.account_proximity import ProximityRow
        row = ProximityRow(tier=3)
        row.add(1, {"type": "mutual_follow"})
        assert row.tier == 1

    def test_add_same_reason_deduped(self):
        from src.pipeline.account_proximity import ProximityRow
        row = ProximityRow(tier=1)
        reason = {"type": "dm_contact", "messages": 5}
        row.add(1, reason)
        row.add(1, reason)
        assert len(row.reasons) == 1

    def test_add_different_reasons_both_stored(self):
        from src.pipeline.account_proximity import ProximityRow
        row = ProximityRow(tier=1)
        row.add(1, {"type": "dm_contact", "messages": 5})
        row.add(1, {"type": "mutual_follow"})
        assert len(row.reasons) == 2


class TestProximityAccumulator:
    def test_add_creates_row(self):
        from src.pipeline.account_proximity import ProximityAccumulator, _reason
        acc = ProximityAccumulator()
        acc.add("instagram", "user123", "ownerhandle", 1, _reason("mutual_follow"))
        assert len(acc.rows) == 1

    def test_add_self_skipped(self):
        from src.pipeline.account_proximity import ProximityAccumulator, _reason
        acc = ProximityAccumulator()
        acc.add("instagram", "self", "self", 1, _reason("mutual_follow"))
        assert len(acc.rows) == 0

    def test_add_empty_account_skipped(self):
        from src.pipeline.account_proximity import ProximityAccumulator, _reason
        acc = ProximityAccumulator()
        acc.add("instagram", "", "owner", 1, _reason("dm"))
        assert len(acc.rows) == 0

    def test_records_returns_list(self):
        from src.pipeline.account_proximity import ProximityAccumulator, _reason
        acc = ProximityAccumulator()
        acc.add("instagram", "alice", "owner", 1, _reason("mutual_follow"))
        records = acc.records()
        assert isinstance(records, list)
        assert len(records) == 1
        platform, account, owner, tier, reasons_json = records[0]
        assert platform == "instagram"
        assert account == "alice"
        assert tier == 1
        parsed = json.loads(reasons_json)
        assert isinstance(parsed, list)

    def test_best_tier_wins_on_repeated_add(self):
        from src.pipeline.account_proximity import ProximityAccumulator, _reason
        acc = ProximityAccumulator()
        acc.add("instagram", "alice", "owner", 3, _reason("discovered_only"))
        acc.add("instagram", "alice", "owner", 1, _reason("mutual_follow"))
        record = acc.records()[0]
        assert record[3] == 1  # tier


class TestBestOwnerByPlatform:
    def test_returns_lowest_tier_owner(self):
        from src.pipeline.account_proximity import ProximityAccumulator, _best_owner_by_platform, _reason
        acc = ProximityAccumulator()
        acc.add("instagram", "alice", "owner_a", 3, _reason("discovered_only"))
        acc.add("instagram", "bob", "owner_b", 1, _reason("mutual_follow"))
        result = _best_owner_by_platform(acc)
        assert result.get("instagram") == "owner_b"

    def test_empty_accumulator(self):
        from src.pipeline.account_proximity import ProximityAccumulator, _best_owner_by_platform
        acc = ProximityAccumulator()
        assert _best_owner_by_platform(acc) == {}


class TestOwnerForPeerLink:
    def test_matching_platform(self):
        from src.pipeline.account_proximity import _owner_for_peer_link
        owner_by_platform = {"instagram": "myhandle"}
        link = {"source": "instagram", "platform_id": "alice"}
        assert _owner_for_peer_link(owner_by_platform, link) == "myhandle"

    def test_missing_platform_returns_empty(self):
        from src.pipeline.account_proximity import _owner_for_peer_link
        link = {"source": "github", "platform_id": "alice"}
        assert _owner_for_peer_link({}, link) == ""


# ---------------------------------------------------------------------------
# conversation_analytics._iso / _decode / _thread_key
# ---------------------------------------------------------------------------

class TestIsoConversationAnalytics:
    def _iso(self, value):
        from src.pipeline.conversation_analytics import _iso
        return _iso(value)

    def test_datetime_returns_isoformat(self):
        dt = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = self._iso(dt)
        assert "2026-01-15" in result

    def test_none_returns_none(self):
        assert self._iso(None) is None

    def test_string_returns_str(self):
        assert self._iso("already a string") == "already a string"

    def test_int_returns_str(self):
        assert self._iso(42) == "42"


class TestDecodeConversationAnalytics:
    def _decode(self, raw):
        from src.pipeline.conversation_analytics import _decode
        return _decode(raw)

    def test_dict_passthrough(self):
        assert self._decode({"a": 1}) == {"a": 1}

    def test_json_string(self):
        assert self._decode('{"x": 2}') == {"x": 2}

    def test_json_bytes(self):
        assert self._decode(b'{"y": 3}') == {"y": 3}

    def test_invalid_json_returns_empty(self):
        assert self._decode("bad") == {}

    def test_json_array_returns_empty(self):
        assert self._decode("[1, 2]") == {}

    def test_none_returns_empty(self):
        assert self._decode(None) == {}


class TestThreadKeyConversationAnalytics:
    def _make_row(self, source, actor, target, occurred_at, metadata=None, source_record_id=None):
        return {
            "source": source,
            "actor_entity_id": actor,
            "target_entity_id": target,
            "occurred_at": occurred_at,
            "metadata": metadata or {},
            "source_record_id": source_record_id or "rec1",
        }

    def test_telegram_key_structure(self):
        from src.pipeline.conversation_analytics import _thread_key
        dt = datetime(2026, 1, 15, tzinfo=timezone.utc)
        row = self._make_row("telegram", "entity_a", "entity_b", dt,
                             metadata={"target_message_id": "chat123:msg456"})
        key = _thread_key(row)
        assert key.startswith("telegram:")
        assert "entity_a" in key
        assert "2026-01-15" in key

    def test_non_telegram_key_structure(self):
        from src.pipeline.conversation_analytics import _thread_key
        dt = datetime(2026, 3, 10, tzinfo=timezone.utc)
        row = self._make_row("instagram", "entity_a", "entity_b", dt)
        key = _thread_key(row)
        assert key.startswith("instagram:")
        assert "entity_a" in key
        assert "2026-03-10" in key

    def test_none_target_becomes_unknown(self):
        from src.pipeline.conversation_analytics import _thread_key
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        row = self._make_row("instagram", "entity_a", None, dt)
        key = _thread_key(row)
        assert "unknown" in key
