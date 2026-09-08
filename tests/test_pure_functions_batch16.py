"""
Pure-function tests — batch 16.

Covers previously untested modules with no DB or I/O:
- pipeline.alert_engine: _decode_meta, _env_bool, _env_float, _env_int
- notifications.alerts: _num, _esc
- notifications.intelligence: _num, _pct, _age, _safe_int,
  INTELLIGENCE_ALERT_TYPES / INTELLIGENCE_PHASES constants
- api.routes.collector_health: _collector_dashboard_url, _collector_cookie_vault_url
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# alert_engine: _decode_meta, _env_bool, _env_float, _env_int
# ---------------------------------------------------------------------------

class TestAlertEngineDecodeMeta:
    def _d(self, raw):
        from src.pipeline.alert_engine import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_json_bytes(self):
        assert self._d(b'{"b": 2}') == {"b": 2}

    def test_invalid_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


class TestAlertEngineEnvBool:
    def _b(self, key, default=True):
        from src.pipeline.alert_engine import _env_bool
        return _env_bool(key, default)

    def test_true_values(self, monkeypatch):
        from src.pipeline.alert_engine import _env_bool
        for val in ("true", "1", "yes", "True", "YES"):
            monkeypatch.setenv("_TEST_ALERT_BOOL", val)
            assert _env_bool("_TEST_ALERT_BOOL") is True, f"Failed for {val!r}"

    def test_false_values(self, monkeypatch):
        from src.pipeline.alert_engine import _env_bool
        for val in ("false", "0", "no", "False", "NO"):
            monkeypatch.setenv("_TEST_ALERT_BOOL", val)
            assert _env_bool("_TEST_ALERT_BOOL") is False, f"Failed for {val!r}"

    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv("_TEST_ALERT_BOOL", raising=False)
        assert self._b("_TEST_ALERT_BOOL", True) is True
        assert self._b("_TEST_ALERT_BOOL", False) is False


class TestAlertEngineEnvFloat:
    def test_reads_float(self, monkeypatch):
        monkeypatch.setenv("_TEST_ALERT_FLOAT", "2.5")
        from src.pipeline.alert_engine import _env_float
        assert abs(_env_float("_TEST_ALERT_FLOAT", 1.0) - 2.5) < 1e-9

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_ALERT_FLOAT", raising=False)
        from src.pipeline.alert_engine import _env_float
        assert abs(_env_float("_TEST_ALERT_FLOAT", 3.14) - 3.14) < 1e-9


class TestAlertEngineEnvInt:
    def test_reads_int(self, monkeypatch):
        monkeypatch.setenv("_TEST_ALERT_INT", "42")
        from src.pipeline.alert_engine import _env_int
        assert _env_int("_TEST_ALERT_INT", 10) == 42

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_ALERT_INT", raising=False)
        from src.pipeline.alert_engine import _env_int
        assert _env_int("_TEST_ALERT_INT", 7) == 7


# ---------------------------------------------------------------------------
# notifications.alerts: _num, _esc
# ---------------------------------------------------------------------------

class TestAlertsNum:
    def _n(self, value):
        from src.notifications.alerts import _num
        return _num(value)

    def test_integer(self):
        assert self._n(1000) == "1,000"

    def test_zero(self):
        assert self._n(0) == "0"

    def test_none(self):
        assert self._n(None) == "0"

    def test_string_int(self):
        assert self._n("500") == "500"

    def test_exception_returns_zero(self):
        assert self._n("bad") == "0"


class TestAlertsEsc:
    def _e(self, value):
        from src.notifications.alerts import _esc
        return _esc(value)

    def test_escapes_html(self):
        assert "&amp;" in self._e("a & b")
        assert "&lt;" in self._e("<script>")
        assert "&gt;" in self._e(">end")

    def test_plain_string_passthrough(self):
        assert self._e("hello world") == "hello world"

    def test_none_converts_to_string(self):
        result = self._e(None)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# notifications.intelligence: _num, _pct, _age, _safe_int, constants
# ---------------------------------------------------------------------------

class TestIntelligenceNum:
    def _n(self, value):
        from src.notifications.intelligence import _num
        return _num(value)

    def test_integer(self):
        assert self._n(1500) == "1,500"

    def test_none(self):
        assert self._n(None) == "0"

    def test_zero(self):
        assert self._n(0) == "0"

    def test_exception_returns_zero(self):
        assert self._n("bad") == "0"


class TestIntelligencePct:
    def _p(self, part, total):
        from src.notifications.intelligence import _pct
        return _pct(part, total)

    def test_half(self):
        assert self._p(50, 100) == "50%"

    def test_zero_total(self):
        assert self._p(10, 0) == "0%"

    def test_none_total(self):
        assert self._p(10, None) == "0%"

    def test_full(self):
        assert self._p(100, 100) == "100%"

    def test_none_part(self):
        assert self._p(None, 100) == "0%"


class TestIntelligenceAge:
    def _a(self, value):
        from src.notifications.intelligence import _age
        return _age(value)

    def test_none_returns_never(self):
        assert self._a(None) == "never"

    def test_seconds_ago(self):
        recent = datetime.now(timezone.utc) - timedelta(seconds=30)
        result = self._a(recent)
        assert result.endswith("s ago") or result.endswith("m ago")

    def test_minutes_ago(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=30)
        result = self._a(old)
        assert "m ago" in result

    def test_hours_ago(self):
        old = datetime.now(timezone.utc) - timedelta(hours=5)
        result = self._a(old)
        assert "h ago" in result

    def test_days_ago(self):
        old = datetime.now(timezone.utc) - timedelta(days=3)
        result = self._a(old)
        assert "d ago" in result

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime.utcnow() - timedelta(minutes=5)
        result = self._a(naive)
        assert result != "never"


class TestIntelligenceSafeInt:
    def _s(self, stats, key):
        from src.notifications.intelligence import _safe_int
        return _safe_int(stats, key)

    def test_normal_int(self):
        assert self._s({"k": 42}, "k") == 42

    def test_missing_key_returns_zero(self):
        assert self._s({}, "k") == 0

    def test_none_value_returns_zero(self):
        assert self._s({"k": None}, "k") == 0

    def test_bool_returns_zero(self):
        # booleans are ints in Python but should be 0 per the guard
        assert self._s({"k": True}, "k") == 0

    def test_negative_returns_zero(self):
        assert self._s({"k": -5}, "k") == 0

    def test_string_int(self):
        assert self._s({"k": "10"}, "k") == 10

    def test_exception_returns_zero(self):
        assert self._s({"k": "bad"}, "k") == 0


class TestIntelligenceConstants:
    def test_alert_types_non_empty(self):
        from src.notifications.intelligence import INTELLIGENCE_ALERT_TYPES
        assert len(INTELLIGENCE_ALERT_TYPES) > 0

    def test_phases_non_empty(self):
        from src.notifications.intelligence import INTELLIGENCE_PHASES
        assert len(INTELLIGENCE_PHASES) > 0

    def test_emotional_spike_in_alert_types(self):
        from src.notifications.intelligence import INTELLIGENCE_ALERT_TYPES
        assert "EMOTIONAL_SPIKE" in INTELLIGENCE_ALERT_TYPES

    def test_face_clustering_in_phases(self):
        from src.notifications.intelligence import INTELLIGENCE_PHASES
        assert "face_clustering" in INTELLIGENCE_PHASES


# ---------------------------------------------------------------------------
# api.routes.collector_health: _collector_dashboard_url, _collector_cookie_vault_url
# ---------------------------------------------------------------------------

class TestCollectorHealthUrls:
    def test_dashboard_url_default(self, monkeypatch):
        monkeypatch.delenv("COLLECTOR_DASHBOARD_URL", raising=False)
        from src.api.routes.collector_health import _collector_dashboard_url
        url = _collector_dashboard_url()
        assert url.startswith("http")
        assert not url.endswith("/")

    def test_dashboard_url_custom(self, monkeypatch):
        monkeypatch.setenv("COLLECTOR_DASHBOARD_URL", "http://myhost:9000/")
        from src.api.routes.collector_health import _collector_dashboard_url
        url = _collector_dashboard_url()
        assert url == "http://myhost:9000"

    def test_cookie_vault_url_default(self, monkeypatch):
        monkeypatch.delenv("COLLECTOR_COOKIE_VAULT_URL", raising=False)
        from src.api.routes.collector_health import _collector_cookie_vault_url
        url = _collector_cookie_vault_url()
        assert url.startswith("http")
        assert not url.endswith("/")

    def test_cookie_vault_url_custom(self, monkeypatch):
        monkeypatch.setenv("COLLECTOR_COOKIE_VAULT_URL", "http://vault:9999/")
        from src.api.routes.collector_health import _collector_cookie_vault_url
        url = _collector_cookie_vault_url()
        assert url == "http://vault:9999"
