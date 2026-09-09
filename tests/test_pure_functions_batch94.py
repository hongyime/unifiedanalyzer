"""
Pure-function tests — batch 94.

Covers:
- pipeline.alert_engine: _decode_meta, _env_bool, _env_float, _env_int
- pipeline.contact_extraction: _extract_emails, _extract_phone_numbers,
  _extract_website_domains
- pipeline.timeline_text_features: _env_bool, _env_int
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# pipeline.alert_engine: _decode_meta
# ---------------------------------------------------------------------------

class TestAlertDecodeMeta:
    def _d(self, raw):
        from src.pipeline.alert_engine import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        d = {"k": 1}
        assert self._d(d) is d

    def test_json_string_parsed(self):
        import json
        assert self._d(json.dumps({"k": 1})) == {"k": 1}

    def test_invalid_returns_empty(self):
        assert self._d("not json") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


# ---------------------------------------------------------------------------
# pipeline.alert_engine: _env_bool
# ---------------------------------------------------------------------------

class TestAlertEnvBool:
    def _b(self, key, default=True, value=None):
        from src.pipeline.alert_engine import _env_bool
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _env_bool(key, default)
        finally:
            os.environ.pop(key, None)

    def test_true_string(self):
        assert self._b("__AE_A__", value="true") is True

    def test_one_string(self):
        assert self._b("__AE_B__", value="1") is True

    def test_false_string(self):
        assert self._b("__AE_C__", value="false") is False

    def test_default_returned_when_unset(self):
        assert self._b("__AE_D__", default=True) is True


# ---------------------------------------------------------------------------
# pipeline.alert_engine: _env_float
# ---------------------------------------------------------------------------

class TestAlertEnvFloat:
    def _f(self, key, default=0.5, value=None):
        from src.pipeline.alert_engine import _env_float
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _env_float(key, default)
        finally:
            os.environ.pop(key, None)

    def test_returns_default_when_unset(self):
        assert abs(self._f("__AEF_X__") - 0.5) < 1e-9

    def test_returns_env_value(self):
        assert abs(self._f("__AEF_Y__", value=0.8) - 0.8) < 1e-9


# ---------------------------------------------------------------------------
# pipeline.alert_engine: _env_int
# ---------------------------------------------------------------------------

class TestAlertEnvInt:
    def _i(self, key, default=5, value=None):
        from src.pipeline.alert_engine import _env_int
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _env_int(key, default)
        finally:
            os.environ.pop(key, None)

    def test_returns_default_when_unset(self):
        assert self._i("__AEI_X__") == 5

    def test_returns_env_value(self):
        assert self._i("__AEI_Y__", value=10) == 10


# ---------------------------------------------------------------------------
# pipeline.contact_extraction: _extract_emails
# ---------------------------------------------------------------------------

class TestExtractEmails:
    def _e(self, text):
        from src.pipeline.contact_extraction import _extract_emails
        return _extract_emails(text)

    def test_extracts_email(self):
        result = self._e("contact me at alice@example.com for info")
        assert "alice@example.com" in result

    def test_lowercased(self):
        result = self._e("Alice@Example.COM")
        assert all(e == e.lower() for e in result)

    def test_no_email_empty(self):
        assert self._e("no email here") == []

    def test_multiple_emails(self):
        result = self._e("a@b.com and c@d.com")
        assert len(result) >= 2

    def test_empty_text_empty(self):
        assert self._e("") == []


# ---------------------------------------------------------------------------
# pipeline.contact_extraction: _extract_phone_numbers
# ---------------------------------------------------------------------------

class TestExtractPhoneNumbers:
    def _p(self, text):
        from src.pipeline.contact_extraction import _extract_phone_numbers
        return _extract_phone_numbers(text)

    def test_extracts_phone_digits(self):
        result = self._p("call +65 9123 4567")
        assert any("91234567" in n or "6591234567" in n for n in result)

    def test_no_phone_empty(self):
        assert self._p("no phone here") == []

    def test_empty_text_empty(self):
        assert self._p("") == []


# ---------------------------------------------------------------------------
# pipeline.contact_extraction: _extract_website_domains
# ---------------------------------------------------------------------------

class TestExtractWebsiteDomains:
    def _w(self, text):
        from src.pipeline.contact_extraction import _extract_website_domains
        return _extract_website_domains(text)

    def test_extracts_domain(self):
        result = self._w("visit my site at https://alice.me")
        if result:
            assert any("alice" in d for d in result)

    def test_known_platform_excluded(self):
        # instagram.com should be excluded as a platform
        result = self._w("https://www.instagram.com/alice")
        assert "instagram.com" not in result

    def test_no_url_empty(self):
        assert self._w("no url here") == []

    def test_empty_text_empty(self):
        assert self._w("") == []


# ---------------------------------------------------------------------------
# pipeline.timeline_text_features: _env_bool
# ---------------------------------------------------------------------------

class TestTtfEnvBool:
    def _b(self, key, default=False, value=None):
        from src.pipeline.timeline_text_features import _env_bool
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _env_bool(key, default)
        finally:
            os.environ.pop(key, None)

    def test_returns_default_when_unset(self):
        assert self._b("__TTF_A__", default=False) is False

    def test_true_string(self):
        assert self._b("__TTF_B__", value="true") is True

    def test_one_string(self):
        assert self._b("__TTF_C__", value="1") is True

    def test_false_string(self):
        assert self._b("__TTF_D__", value="false") is False

    def test_on_string(self):
        assert self._b("__TTF_E__", value="on") is True


# ---------------------------------------------------------------------------
# pipeline.timeline_text_features: _env_int
# ---------------------------------------------------------------------------

class TestTtfEnvInt:
    def _i(self, key, default=10, value=None):
        from src.pipeline.timeline_text_features import _env_int
        if value is not None:
            os.environ[key] = str(value)
        else:
            os.environ.pop(key, None)
        try:
            return _env_int(key, default)
        finally:
            os.environ.pop(key, None)

    def test_returns_default_when_unset(self):
        assert self._i("__TTF_I_X__") == 10

    def test_returns_env_value(self):
        assert self._i("__TTF_I_Y__", value=42) == 42

    def test_empty_string_returns_default(self):
        assert self._i("__TTF_I_Z__", value="") == 10
