"""
Pure-function tests — batch 5.

Covers previously untested modules with no DB or I/O:
- pipeline.email_breach: _is_enabled, _api_url, _query_xposedornot (breach normalisation)
- pipeline.email_recognition: _is_enabled, _holehe_available, _run_holehe output parsing
- pipeline.phone_enrichment: _is_enabled, _parse_phone
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# email_breach
# ---------------------------------------------------------------------------

class TestEmailBreachIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("EMAIL_BREACH_CHECK_ENABLED", raising=False)
        from src.pipeline.email_breach import _is_enabled
        assert _is_enabled() is True

    def test_disabled_when_set_to_0(self, monkeypatch):
        monkeypatch.setenv("EMAIL_BREACH_CHECK_ENABLED", "0")
        from src.pipeline.email_breach import _is_enabled
        assert _is_enabled() is False

    def test_enabled_when_set_to_1(self, monkeypatch):
        monkeypatch.setenv("EMAIL_BREACH_CHECK_ENABLED", "1")
        from src.pipeline.email_breach import _is_enabled
        assert _is_enabled() is True


class TestEmailBreachApiUrl:
    def test_url_contains_email(self):
        from src.pipeline.email_breach import _api_url
        url = _api_url("test@example.com")
        assert "xposedornot.com" in url
        assert "test%40example.com" in url or "test@example.com" in url

    def test_url_special_chars_encoded(self):
        from src.pipeline.email_breach import _api_url
        url = _api_url("user+tag@example.com")
        assert "xposedornot.com" in url
        # + must be encoded
        assert "user+tag@example.com" not in url or "%2B" in url or "%40" in url

    def test_url_starts_with_https(self):
        from src.pipeline.email_breach import _api_url
        assert _api_url("a@b.com").startswith("https://")


class TestQueryXposedornot:
    """Test the breach-normalisation logic inside _query_xposedornot by patching
    urllib.request.urlopen so no real HTTP call is made."""

    def _call(self, response_body: str, status: int = 200):
        import io
        import json
        from unittest.mock import MagicMock, patch
        from src.pipeline.email_breach import _query_xposedornot

        mock_resp = MagicMock()
        mock_resp.read.return_value = response_body.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            return _query_xposedornot("test@example.com")

    def test_string_breach_list(self):
        import json
        body = json.dumps({"breaches": [["Adobe", "LinkedIn"]]})
        result = self._call(body)
        assert len(result) == 2
        names = {r["breach_name"] for r in result}
        assert names == {"Adobe", "LinkedIn"}

    def test_dict_breach_list(self):
        import json
        body = json.dumps({"breaches": [{"breach": "Adobe", "date": "2013"}]})
        result = self._call(body)
        assert result[0]["breach_name"] == "Adobe"
        assert result[0]["breach_date"] == "2013"

    def test_empty_breaches(self):
        import json
        result = self._call(json.dumps({"breaches": []}))
        assert result == []

    def test_no_breaches_key(self):
        import json
        result = self._call(json.dumps({}))
        assert result == []

    def test_404_returns_empty(self):
        import urllib.error
        from unittest.mock import patch
        from src.pipeline.email_breach import _query_xposedornot
        err = urllib.error.HTTPError(url="", code=404, msg="Not Found", hdrs=None, fp=None)
        with patch("urllib.request.urlopen", side_effect=err):
            assert _query_xposedornot("clean@example.com") == []

    def test_500_returns_empty(self):
        import urllib.error
        from unittest.mock import patch
        from src.pipeline.email_breach import _query_xposedornot
        err = urllib.error.HTTPError(url="", code=500, msg="Error", hdrs=None, fp=None)
        with patch("urllib.request.urlopen", side_effect=err):
            assert _query_xposedornot("x@x.com") == []

    def test_network_error_returns_empty(self):
        from unittest.mock import patch
        from src.pipeline.email_breach import _query_xposedornot
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            assert _query_xposedornot("x@x.com") == []

    def test_breach_without_name_excluded(self):
        import json
        # dict breach with no recognized name key → filtered out
        body = json.dumps({"breaches": [{"unknown_field": "value"}]})
        result = self._call(body)
        assert result == []


# ---------------------------------------------------------------------------
# email_recognition
# ---------------------------------------------------------------------------

class TestEmailRecognitionIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HOLEHE_ENABLED", raising=False)
        from src.pipeline.email_recognition import _is_enabled
        assert _is_enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("HOLEHE_ENABLED", "0")
        from src.pipeline.email_recognition import _is_enabled
        assert _is_enabled() is False


class TestHoleheAvailable:
    def test_returns_bool(self):
        from src.pipeline.email_recognition import _holehe_available
        result = _holehe_available()
        assert isinstance(result, bool)

    def test_false_when_not_in_path(self, monkeypatch):
        from unittest.mock import patch
        from src.pipeline.email_recognition import _holehe_available
        with patch("shutil.which", return_value=None):
            assert _holehe_available() is False

    def test_true_when_in_path(self, monkeypatch):
        from unittest.mock import patch
        from src.pipeline.email_recognition import _holehe_available
        with patch("shutil.which", return_value="/usr/bin/holehe"):
            assert _holehe_available() is True


class TestRunHoleheOutputParsing:
    """Test _run_holehe stdout parsing by patching subprocess.run."""

    def _run(self, stdout: str):
        from unittest.mock import MagicMock, patch
        from src.pipeline.email_recognition import _run_holehe
        mock_result = MagicMock()
        mock_result.stdout = stdout
        with patch("subprocess.run", return_value=mock_result):
            return _run_holehe("test@example.com")

    def test_used_line_parsed(self):
        result = self._run("[+] adobe.com\n[-] amazon.com\n")
        assert len(result) == 1
        assert result[0]["service"] == "adobe.com"
        assert result[0]["url"] == "https://adobe.com"

    def test_multiple_used_lines(self):
        result = self._run("[+] adobe.com\n[+] linkedin.com\n[-] google.com\n")
        assert len(result) == 2
        services = {r["service"] for r in result}
        assert services == {"adobe.com", "linkedin.com"}

    def test_no_used_lines(self):
        result = self._run("[-] amazon.com\n[-] google.com\n")
        assert result == []

    def test_empty_stdout(self):
        assert self._run("") == []

    def test_timeout_returns_empty(self):
        import subprocess
        from unittest.mock import patch
        from src.pipeline.email_recognition import _run_holehe
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1)):
            assert _run_holehe("x@x.com") == []

    def test_file_not_found_returns_empty(self):
        from unittest.mock import patch
        from src.pipeline.email_recognition import _run_holehe
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert _run_holehe("x@x.com") == []


# ---------------------------------------------------------------------------
# phone_enrichment
# ---------------------------------------------------------------------------

class TestPhoneEnrichmentIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("PHONE_ENRICHMENT_ENABLED", raising=False)
        from src.pipeline.phone_enrichment import _is_enabled
        assert _is_enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("PHONE_ENRICHMENT_ENABLED", "0")
        from src.pipeline.phone_enrichment import _is_enabled
        assert _is_enabled() is False

    def test_enabled_when_1(self, monkeypatch):
        monkeypatch.setenv("PHONE_ENRICHMENT_ENABLED", "1")
        from src.pipeline.phone_enrichment import _is_enabled
        assert _is_enabled() is True


class TestParsePhone:
    def _p(self, v):
        from src.pipeline.phone_enrichment import _parse_phone
        return _parse_phone(v)

    def test_phone_prefix_stripped(self):
        assert self._p("phone:+6591234567") == "+6591234567"

    def test_phone_prefix_bare_number(self):
        assert self._p("phone:6591234567") == "6591234567"

    def test_e164_passthrough(self):
        assert self._p("+6591234567") == "+6591234567"

    def test_bare_number_without_plus_returns_none(self):
        # no "phone:" prefix and no leading "+" → not parseable
        assert self._p("6591234567") is None

    def test_empty_string_returns_none(self):
        assert self._p("") is None

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_strips_whitespace_after_phone_prefix(self):
        assert self._p("phone:  +6591234567  ") == "+6591234567"

    def test_leading_space_before_plus_returns_none(self):
        # _parse_phone checks startswith('+') — leading space means no match → None
        assert self._p("  +6591234567  ") is None
