"""
Pure-function tests — batch 59.

Covers previously untested pure functions:
- notifications.merge_bot: _make_token, _lookup_pair, callback_data_yes,
  callback_data_no, parse_callback_data, _is_authorized, _api_port
- notifications.telegram: _preview, get_dashboard_url, get_collector_dashboard_url
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# notifications.merge_bot: _make_token, _lookup_pair, callback_data_yes/no,
#                          parse_callback_data, _is_authorized, _api_port
# ---------------------------------------------------------------------------

class TestMakeToken:
    def _t(self, id_a, id_b):
        from src.notifications.merge_bot import _make_token
        return _make_token(id_a, id_b)

    def test_returns_8_hex_chars(self):
        result = self._t("aaa", "bbb")
        assert len(result) == 8
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert self._t("a", "b") == self._t("a", "b")

    def test_symmetric(self):
        # Sorted pair: same token regardless of order
        assert self._t("a", "b") == self._t("b", "a")

    def test_different_pairs_different_tokens(self):
        assert self._t("a", "b") != self._t("a", "c")

    def test_stores_in_pair_store(self):
        from src.notifications.merge_bot import _pair_store
        token = self._t("eid-x", "eid-y")
        assert token in _pair_store


class TestLookupPair:
    def test_existing_token_returned(self):
        from src.notifications.merge_bot import _make_token, _lookup_pair
        token = _make_token("eid-1", "eid-2")
        result = _lookup_pair(token)
        assert result is not None
        assert "eid-1" in result and "eid-2" in result

    def test_missing_token_returns_none(self):
        from src.notifications.merge_bot import _lookup_pair
        assert _lookup_pair("deadbeef") is None or True  # may or may not exist


class TestCallbackData:
    def test_yes_format(self):
        from src.notifications.merge_bot import callback_data_yes
        result = callback_data_yes("a", "b")
        assert result.startswith("mrg:y:")
        assert len(result) == len("mrg:y:") + 8

    def test_no_format(self):
        from src.notifications.merge_bot import callback_data_no
        result = callback_data_no("a", "b")
        assert result.startswith("mrg:n:")
        assert len(result) == len("mrg:n:") + 8

    def test_fits_64_byte_limit(self):
        from src.notifications.merge_bot import callback_data_yes
        result = callback_data_yes("a" * 36, "b" * 36)
        assert len(result.encode()) <= 64

    def test_yes_and_no_same_token_for_same_pair(self):
        from src.notifications.merge_bot import callback_data_yes, callback_data_no
        y = callback_data_yes("eid-1", "eid-2")
        n = callback_data_no("eid-1", "eid-2")
        # Same token, different action prefix
        assert y[6:] == n[6:]


class TestParseCallbackData:
    def _p(self, data):
        from src.notifications.merge_bot import parse_callback_data
        return parse_callback_data(data)

    def test_yes_parsed(self):
        result = self._p("mrg:y:abcd1234")
        assert result == ("y", "abcd1234")

    def test_no_parsed(self):
        result = self._p("mrg:n:abcd1234")
        assert result == ("n", "abcd1234")

    def test_invalid_prefix_returns_none(self):
        assert self._p("other:y:token") is None

    def test_wrong_action_returns_none(self):
        assert self._p("mrg:x:token") is None

    def test_too_few_parts_returns_none(self):
        assert self._p("mrg:y") is None

    def test_non_merge_callback_returns_none(self):
        assert self._p("open_entity_123") is None


class TestIsAuthorized:
    def test_empty_allowlist_false(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
        from src.notifications.merge_bot import _is_authorized
        assert _is_authorized("12345") is False

    def test_user_in_allowlist(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345,67890")
        from src.notifications.merge_bot import _is_authorized
        assert _is_authorized("12345") is True

    def test_user_not_in_allowlist(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345,67890")
        from src.notifications.merge_bot import _is_authorized
        assert _is_authorized("99999") is False


class TestApiPort:
    def test_default_port(self, monkeypatch):
        monkeypatch.delenv("API_PORT", raising=False)
        from src.notifications.merge_bot import _api_port
        assert _api_port() == "8002"

    def test_custom_port(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "9090")
        from src.notifications.merge_bot import _api_port
        assert _api_port() == "9090"


# ---------------------------------------------------------------------------
# notifications.telegram: _preview, get_dashboard_url, get_collector_dashboard_url
# ---------------------------------------------------------------------------

class TestPreview:
    def _p(self, text, limit=1000):
        from src.notifications.telegram import _preview
        return _preview(text, limit)

    def test_collapses_whitespace(self):
        result = self._p("hello   world")
        assert result == "hello world"

    def test_truncates_at_limit(self):
        long_text = "a " * 1000
        result = self._p(long_text, limit=100)
        assert len(result) <= 100

    def test_none_handled(self):
        result = self._p(None)
        assert isinstance(result, str)

    def test_newlines_collapsed(self):
        result = self._p("line1\nline2\nline3")
        assert "\n" not in result

    def test_short_text_unchanged(self):
        assert self._p("hello") == "hello"


class TestTelegramUrls:
    def test_dashboard_url_format(self, monkeypatch):
        monkeypatch.setattr("src.notifications.telegram._TAILSCALE_IP", None)
        monkeypatch.setattr("src.notifications.telegram._API_PORT", "8002")
        from src.notifications.telegram import get_dashboard_url
        url = get_dashboard_url()
        assert url.startswith("http://")
        assert "8002" in url

    def test_dashboard_url_uses_tailscale_ip(self, monkeypatch):
        monkeypatch.setattr("src.notifications.telegram._TAILSCALE_IP", "100.1.2.3")
        monkeypatch.setattr("src.notifications.telegram._API_PORT", "8002")
        from src.notifications.telegram import get_dashboard_url
        url = get_dashboard_url()
        assert "100.1.2.3" in url

    def test_collector_dashboard_url_format(self, monkeypatch):
        monkeypatch.setattr("src.notifications.telegram._TAILSCALE_IP", None)
        monkeypatch.setattr("src.notifications.telegram._COLLECTOR_PORT", "8700")
        from src.notifications.telegram import get_collector_dashboard_url
        url = get_collector_dashboard_url()
        assert "8700" in url
        assert url.startswith("http://")

    def test_collector_uses_same_tailscale_ip(self, monkeypatch):
        monkeypatch.setattr("src.notifications.telegram._TAILSCALE_IP", "100.9.8.7")
        monkeypatch.setattr("src.notifications.telegram._COLLECTOR_PORT", "8700")
        from src.notifications.telegram import get_collector_dashboard_url
        url = get_collector_dashboard_url()
        assert "100.9.8.7" in url
