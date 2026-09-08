"""
Pure-function tests — batch 75.

Covers:
- notifications.merge_bot: _make_token, _lookup_pair, callback_data_yes/no,
  parse_callback_data, _api_port, _is_authorized
- notifications.intelligence: _num, _pct, _age, _safe_int
- api.routes.readiness: _collector_summary_ok, _scheduler_progress_ok
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# notifications.merge_bot: token helpers
# ---------------------------------------------------------------------------

class TestMakeToken:
    def _t(self, a, b):
        from src.notifications.merge_bot import _make_token
        return _make_token(a, b)

    def test_returns_8_chars(self):
        t = self._t("aaa", "bbb")
        assert len(t) == 8

    def test_deterministic(self):
        assert self._t("x", "y") == self._t("x", "y")

    def test_order_independent(self):
        assert self._t("a", "b") == self._t("b", "a")

    def test_hex_chars_only(self):
        import re
        assert re.fullmatch(r"[0-9a-f]{8}", self._t("p", "q"))


class TestLookupPair:
    def test_returns_none_for_unknown_token(self):
        from src.notifications.merge_bot import _lookup_pair
        assert _lookup_pair("00000000") is None

    def test_round_trip(self):
        from src.notifications.merge_bot import _make_token, _lookup_pair
        token = _make_token("id-aaa", "id-bbb")
        result = _lookup_pair(token)
        assert result is not None
        assert set(result) == {"id-aaa", "id-bbb"}


class TestCallbackData:
    def test_yes_prefix(self):
        from src.notifications.merge_bot import callback_data_yes
        assert callback_data_yes("a", "b").startswith("mrg:y:")

    def test_no_prefix(self):
        from src.notifications.merge_bot import callback_data_no
        assert callback_data_no("a", "b").startswith("mrg:n:")

    def test_yes_no_different(self):
        from src.notifications.merge_bot import callback_data_yes, callback_data_no
        assert callback_data_yes("a", "b") != callback_data_no("a", "b")

    def test_within_64_bytes(self):
        from src.notifications.merge_bot import callback_data_yes
        data = callback_data_yes("a" * 10, "b" * 10)
        assert len(data.encode()) <= 64


class TestParseCallbackData:
    def _p(self, data):
        from src.notifications.merge_bot import parse_callback_data
        return parse_callback_data(data)

    def test_yes_parsed(self):
        action, token = self._p("mrg:y:abc12345")
        assert action == "y"
        assert token == "abc12345"

    def test_no_parsed(self):
        action, token = self._p("mrg:n:deadbeef")
        assert action == "n"

    def test_wrong_prefix_returns_none(self):
        assert self._p("btn:y:abc12345") is None

    def test_invalid_action_returns_none(self):
        assert self._p("mrg:x:abc12345") is None

    def test_too_few_parts_returns_none(self):
        assert self._p("mrg:y") is None

    def test_empty_string_returns_none(self):
        assert self._p("") is None


class TestApiPort:
    def test_default_8002(self):
        import os
        from src.notifications.merge_bot import _api_port
        os.environ.pop("API_PORT", None)
        assert _api_port() == "8002"

    def test_env_override(self):
        import os
        from src.notifications.merge_bot import _api_port
        os.environ["API_PORT"] = "9999"
        try:
            assert _api_port() == "9999"
        finally:
            os.environ.pop("API_PORT", None)


class TestIsAuthorized:
    def _a(self, user_id, allowed=""):
        import os
        from src.notifications.merge_bot import _is_authorized
        os.environ["TELEGRAM_ALLOWED_USER_IDS"] = allowed
        try:
            return _is_authorized(user_id)
        finally:
            os.environ.pop("TELEGRAM_ALLOWED_USER_IDS", None)

    def test_empty_allowlist_rejects_all(self):
        assert self._a("12345", "") is False

    def test_user_in_allowlist_allowed(self):
        assert self._a("12345", "12345,67890") is True

    def test_user_not_in_allowlist_rejected(self):
        assert self._a("99999", "12345,67890") is False

    def test_whitespace_stripped(self):
        assert self._a("42", " 42 , 99 ") is True


# ---------------------------------------------------------------------------
# notifications.intelligence: _num, _pct, _age, _safe_int
# ---------------------------------------------------------------------------

class TestNum:
    def _n(self, v):
        from src.notifications.intelligence import _num
        return _num(v)

    def test_zero(self):
        assert self._n(0) == "0"

    def test_large_number_comma_formatted(self):
        assert self._n(1_000_000) == "1,000,000"

    def test_none_treated_as_zero(self):
        assert self._n(None) == "0"

    def test_float_truncated(self):
        assert self._n(3.9) == "3"

    def test_invalid_returns_zero(self):
        assert self._n("notanumber") == "0"


class TestPct:
    def _p(self, part, total):
        from src.notifications.intelligence import _pct
        return _pct(part, total)

    def test_zero_total_returns_zero_pct(self):
        assert self._p(5, 0) == "0%"

    def test_none_total_returns_zero_pct(self):
        assert self._p(5, None) == "0%"

    def test_half(self):
        assert self._p(50, 100) == "50%"

    def test_full(self):
        assert self._p(100, 100) == "100%"

    def test_none_part_treated_as_zero(self):
        assert self._p(None, 100) == "0%"


class TestAge:
    def _a(self, value):
        from src.notifications.intelligence import _age
        return _age(value)

    def test_none_returns_never(self):
        assert self._a(None) == "never"

    def test_recent_seconds(self):
        dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        assert self._a(dt).endswith("s ago")

    def test_minutes(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=10)
        result = self._a(dt)
        assert "m ago" in result

    def test_hours(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=5)
        result = self._a(dt)
        assert "h ago" in result

    def test_days(self):
        dt = datetime.now(timezone.utc) - timedelta(days=3)
        result = self._a(dt)
        assert "d ago" in result

    def test_naive_datetime_handled(self):
        dt = datetime.utcnow() - timedelta(hours=2)
        result = self._a(dt)
        assert "ago" in result


class TestSafeInt:
    def _s(self, stats, key):
        from src.notifications.intelligence import _safe_int
        return _safe_int(stats, key)

    def test_normal_int(self):
        assert self._s({"k": 5}, "k") == 5

    def test_missing_key_returns_zero(self):
        assert self._s({}, "k") == 0

    def test_bool_returns_zero(self):
        assert self._s({"k": True}, "k") == 0

    def test_negative_clamped_to_zero(self):
        assert self._s({"k": -3}, "k") == 0

    def test_string_number(self):
        assert self._s({"k": "7"}, "k") == 7


# ---------------------------------------------------------------------------
# api.routes.readiness: _scheduler_progress_ok
# ---------------------------------------------------------------------------

class TestSchedulerProgressOk:
    def _s(self, incremental, full):
        from src.api.routes.readiness import _scheduler_progress_ok
        return _scheduler_progress_ok(incremental, full)

    def test_both_ok(self):
        assert self._s({"ok": True}, {"ok": True}) is True

    def test_both_not_ok(self):
        assert self._s({"ok": False}, {"ok": False}) is False

    def test_incremental_stale_full_fresh(self):
        inc = {"ok": False, "state": "stale", "running_error": None}
        full = {"ok": True, "state": "fresh"}
        assert self._s(inc, full) is True

    def test_incremental_stale_with_error_not_ok(self):
        inc = {"ok": False, "state": "stale", "running_error": "timeout"}
        full = {"ok": True, "state": "fresh"}
        assert self._s(inc, full) is False
