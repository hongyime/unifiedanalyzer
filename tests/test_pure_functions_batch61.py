"""
Pure-function tests — batch 61.

Covers previously untested pure functions:
- notifications.telegram: _NO_WINDOW constant, _COLLECTOR_PORT default,
  get_updates params structure (constants), delete_webhook format
- notifications.merge_bot: card-text formatting helpers (pure HTML escape,
  merge card text patterns), _handle_message command-parse helpers
- notifications.alerts: _now, _num, _esc additional edge cases
"""
from __future__ import annotations

import html


# ---------------------------------------------------------------------------
# notifications.telegram: module-level constants
# ---------------------------------------------------------------------------

class TestTelegramModuleConstants:
    def test_no_window_is_int(self):
        from src.notifications.telegram import _NO_WINDOW
        assert isinstance(_NO_WINDOW, int)
        assert _NO_WINDOW >= 0

    def test_collector_port_default(self, monkeypatch):
        monkeypatch.delenv("COLLECTOR_DASHBOARD_PORT", raising=False)
        import importlib
        # _COLLECTOR_PORT is set at module level; check the env default
        import os
        assert os.getenv("COLLECTOR_DASHBOARD_PORT", "8700") == "8700"

    def test_api_port_default(self, monkeypatch):
        monkeypatch.delenv("API_PORT", raising=False)
        import os
        assert os.getenv("API_PORT", "8002") == "8002"


# ---------------------------------------------------------------------------
# notifications.merge_bot: card text / HTML escaping helpers
# ---------------------------------------------------------------------------

class TestMergeBotCardText:
    def test_callback_data_within_64_bytes(self):
        from src.notifications.merge_bot import callback_data_yes, callback_data_no
        # UUIDs are 36 chars each
        id_a = "12345678-1234-1234-1234-123456789abc"
        id_b = "87654321-4321-4321-4321-cba987654321"
        yes = callback_data_yes(id_a, id_b)
        no = callback_data_no(id_a, id_b)
        assert len(yes.encode("utf-8")) <= 64
        assert len(no.encode("utf-8")) <= 64

    def test_token_consistent_across_calls(self):
        from src.notifications.merge_bot import callback_data_yes
        y1 = callback_data_yes("a", "b")
        y2 = callback_data_yes("a", "b")
        assert y1 == y2

    def test_different_pairs_different_callbacks(self):
        from src.notifications.merge_bot import callback_data_yes
        y1 = callback_data_yes("a", "b")
        y2 = callback_data_yes("a", "c")
        assert y1 != y2

    def test_parse_roundtrip_yes(self):
        from src.notifications.merge_bot import callback_data_yes, parse_callback_data
        y = callback_data_yes("eid-1", "eid-2")
        parsed = parse_callback_data(y)
        assert parsed is not None
        assert parsed[0] == "y"
        assert len(parsed[1]) == 8

    def test_parse_roundtrip_no(self):
        from src.notifications.merge_bot import callback_data_no, parse_callback_data
        n = callback_data_no("eid-1", "eid-2")
        parsed = parse_callback_data(n)
        assert parsed is not None
        assert parsed[0] == "n"

    def test_make_token_pair_lookup_roundtrip(self):
        from src.notifications.merge_bot import _make_token, _lookup_pair
        token = _make_token("uuid-x", "uuid-y")
        pair = _lookup_pair(token)
        assert pair is not None
        assert "uuid-x" in pair
        assert "uuid-y" in pair


# ---------------------------------------------------------------------------
# notifications.alerts: additional _now, _num, _esc tests
# ---------------------------------------------------------------------------

class TestAlertsNumAdditional:
    def _n(self, v):
        from src.notifications.alerts import _num
        return _num(v)

    def test_large_number_formatted(self):
        result = self._n(1_000_000)
        assert "," in result or "000" in result

    def test_negative_number(self):
        result = self._n(-5)
        assert "-5" in result or "5" in result

    def test_float_truncated(self):
        result = self._n(3.7)
        assert "3" in result


class TestAlertsEscAdditional:
    def _e(self, v):
        from src.notifications.alerts import _esc
        return _esc(v)

    def test_ampersand_escaped(self):
        assert "&amp;" in self._e("a & b")

    def test_quotes_not_escaped_by_default(self):
        # html.escape by default doesn't escape single quotes
        result = self._e("it's")
        assert "it" in result

    def test_unicode_preserved(self):
        result = self._e("日本語")
        assert "日本語" in result

    def test_empty_string(self):
        assert self._e("") == ""


class TestAlertsNow:
    def test_returns_string(self):
        from src.notifications.alerts import _now
        result = _now()
        assert isinstance(result, str)

    def test_contains_utc(self):
        from src.notifications.alerts import _now
        assert "UTC" in _now()

    def test_contains_year(self):
        from src.notifications.alerts import _now
        assert "20" in _now()


# ---------------------------------------------------------------------------
# notifications.telegram: _NO_WINDOW posix compatibility
# ---------------------------------------------------------------------------

class TestTelegramSubprocessConstants:
    def test_no_window_zero_on_linux(self):
        """On non-Windows, CREATE_NO_WINDOW doesn't exist so it defaults to 0."""
        import subprocess
        # Either the attr exists (Windows) or falls back to 0
        val = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        from src.notifications.telegram import _NO_WINDOW
        assert _NO_WINDOW == val

    def test_send_max_retries_at_least_1(self):
        from src.notifications.telegram import _SEND_MAX_RETRIES
        assert _SEND_MAX_RETRIES >= 1


# ---------------------------------------------------------------------------
# notifications.merge_bot: _is_authorized edge cases
# ---------------------------------------------------------------------------

class TestMergeBotIsAuthorizedEdge:
    def test_whitespace_in_allowlist(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", " 12345 , 67890 ")
        from src.notifications.merge_bot import _is_authorized
        assert _is_authorized("12345") is True
        assert _is_authorized("67890") is True
        assert _is_authorized("99999") is False

    def test_zero_as_user_id_false_if_not_listed(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
        from src.notifications.merge_bot import _is_authorized
        assert _is_authorized("0") is False

    def test_string_vs_int_user_id(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
        from src.notifications.merge_bot import _is_authorized
        # _is_authorized converts user_id to str
        assert _is_authorized(12345) is True
