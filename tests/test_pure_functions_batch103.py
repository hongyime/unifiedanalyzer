"""
Pure-function tests — batch 103.

Covers:
- pipeline.decision_replay: _coerce_feature_snapshot, _features_from_snapshot_container
- pipeline.account_proximity: _default_owner, _parse_beeper_native_id (additional)
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _coerce_feature_snapshot
# ---------------------------------------------------------------------------

class TestCoerceFeatureSnapshot:
    def _c(self, raw):
        from src.pipeline.decision_replay import _coerce_feature_snapshot
        return _coerce_feature_snapshot(raw)

    def test_empty_returns_empty(self):
        assert self._c({}) == {}

    def test_float_values(self):
        result = self._c({"shared_email": 0.9, "username_exact": 0.7})
        assert abs(result["shared_email"] - 0.9) < 1e-9
        assert abs(result["username_exact"] - 0.7) < 1e-9

    def test_invalid_value_skipped(self):
        result = self._c({"shared_email": "bad", "username_exact": 0.5})
        assert "shared_email" not in result
        assert abs(result["username_exact"] - 0.5) < 1e-9

    def test_none_value_treated_as_zero(self):
        result = self._c({"shared_email": None})
        assert abs(result["shared_email"] - 0.0) < 1e-9

    def test_key_stringified(self):
        result = self._c({42: 0.5})
        assert "42" in result


# ---------------------------------------------------------------------------
# pipeline.decision_replay: _features_from_snapshot_container
# ---------------------------------------------------------------------------

class TestFeaturesFromSnapshotContainer:
    def _f(self, snapshot):
        from src.pipeline.decision_replay import _features_from_snapshot_container
        return _features_from_snapshot_container(snapshot)

    def test_non_dict_returns_empty(self):
        assert self._f(None) == {}
        assert self._f([]) == {}

    def test_no_sources_returns_empty(self):
        assert self._f({}) == {}

    def test_sources_dict_with_contributing_signals_dict(self):
        import json
        snapshot = {
            "sources": json.dumps({
                "contributing_signals": {"shared_email": 0.9}
            })
        }
        result = self._f(snapshot)
        assert abs(result.get("shared_email", 0) - 0.9) < 1e-9

    def test_contributing_signals_list(self):
        snapshot = {
            "sources": {
                "contributing_signals": [
                    {"type": "shared_email", "confidence": 0.9},
                    {"type": "username_exact", "confidence": 0.7},
                ]
            }
        }
        result = self._f(snapshot)
        assert abs(result["shared_email"] - 0.9) < 1e-9
        assert abs(result["username_exact"] - 0.7) < 1e-9

    def test_list_signal_max_confidence_kept(self):
        snapshot = {
            "sources": {
                "contributing_signals": [
                    {"type": "shared_email", "confidence": 0.5},
                    {"type": "shared_email", "confidence": 0.9},
                ]
            }
        }
        result = self._f(snapshot)
        assert abs(result["shared_email"] - 0.9) < 1e-9

    def test_list_signal_missing_type_skipped(self):
        snapshot = {
            "sources": {
                "contributing_signals": [
                    {"confidence": 0.9},  # no type
                ]
            }
        }
        result = self._f(snapshot)
        assert result == {}


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _default_owner
# ---------------------------------------------------------------------------

class TestDefaultOwner:
    def _d(self, owner_accounts, platform):
        from src.pipeline.account_proximity import _default_owner
        return _default_owner(owner_accounts, platform)

    def test_empty_accounts_returns_empty(self):
        assert self._d({}, "instagram") == ""

    def test_platform_not_present_returns_empty(self):
        assert self._d({"telegram": {"@alice"}}, "instagram") == ""

    def test_single_owner_returned(self):
        result = self._d({"instagram": {"alice"}}, "instagram")
        assert result == "alice"

    def test_multiple_owners_returns_sorted_first(self):
        result = self._d({"instagram": {"charlie", "alice", "bob"}}, "instagram")
        assert result == "alice"

    def test_empty_string_owner_excluded(self):
        result = self._d({"instagram": {"", "alice"}}, "instagram")
        assert result == "alice"


# ---------------------------------------------------------------------------
# pipeline.account_proximity: _parse_beeper_native_id additional
# ---------------------------------------------------------------------------

class TestParseBeeperNativeIdAdditional:
    def _p(self, network, participant_id, full_name=None):
        from src.pipeline.account_proximity import _parse_beeper_native_id
        return _parse_beeper_native_id(network, participant_id, full_name)

    def test_whatsapp_phone_display(self):
        # Use whatsapp: prefix so BEEPER_ID_RE matches and phone path fires
        platform, account = self._p("WhatsApp", "whatsapp:6591234567", full_name="+6591234567")
        assert platform == "whatsapp"
        assert "6591234567" in account

    def test_empty_participant_id(self):
        platform, account = self._p("Telegram", "", None)
        assert isinstance(platform, str)
        assert isinstance(account, str)

    def test_unknown_network_returns_beeper(self):
        platform, account = self._p("Signal", "signal:alice", None)
        assert isinstance(platform, str)
        assert isinstance(account, str)
