"""
Pure-function tests — batch 8.

Covers previously untested modules with no DB or I/O:
- face.utils.atomic: atomic_operation (success path, rollback on failure,
  rollback failure doesn't mask original error)
- pipeline.handle_fanout: _is_enabled, _sherlock_available, _run_sherlock output parsing
- pipeline.indicator_export: _valid_ipv4, _normalize_domain, _normalize_email,
  normalize_phone_e164, normalize_indicator, extract_indicators_from_text
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# face.utils.atomic: atomic_operation
# ---------------------------------------------------------------------------

class TestAtomicOperation:
    def _run(self, op, rollback):
        from src.face.utils.atomic import atomic_operation
        return atomic_operation(op, rollback)

    def test_success_returns_result(self):
        result = self._run(lambda: 42, lambda v: None)
        assert result == 42

    def test_failure_calls_rollback(self):
        rolled_back = []
        def op():
            raise ValueError("boom")
        def rollback(v):
            rolled_back.append(v)

        import pytest
        with pytest.raises(ValueError, match="boom"):
            self._run(op, rollback)
        assert rolled_back == [None]

    def test_original_error_reraised_even_if_rollback_fails(self):
        import pytest
        def op():
            raise RuntimeError("original")
        def rollback(v):
            raise OSError("rollback also failed")

        with pytest.raises(RuntimeError, match="original"):
            self._run(op, rollback)

    def test_rollback_receives_none(self):
        received = []
        def op():
            raise Exception("x")
        def rollback(v):
            received.append(v)

        import pytest
        with pytest.raises(Exception):
            self._run(op, rollback)
        assert received == [None]

    def test_no_exception_rollback_not_called(self):
        called = []
        self._run(lambda: "ok", lambda v: called.append(v))
        assert called == []


# ---------------------------------------------------------------------------
# pipeline.handle_fanout: _is_enabled, _sherlock_available, _run_sherlock
# ---------------------------------------------------------------------------

class TestHandleFanoutIsEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("HANDLE_FANOUT_ENABLED", raising=False)
        from src.pipeline.handle_fanout import _is_enabled
        assert _is_enabled() is True

    def test_disabled_when_0(self, monkeypatch):
        monkeypatch.setenv("HANDLE_FANOUT_ENABLED", "0")
        from src.pipeline.handle_fanout import _is_enabled
        assert _is_enabled() is False


class TestSherlockAvailable:
    def test_false_when_not_on_path(self):
        from unittest.mock import patch
        from src.pipeline.handle_fanout import _sherlock_available
        with patch("shutil.which", return_value=None):
            assert _sherlock_available() is False

    def test_true_when_on_path(self):
        from unittest.mock import patch
        from src.pipeline.handle_fanout import _sherlock_available
        with patch("shutil.which", return_value="/usr/bin/sherlock"):
            assert _sherlock_available() is True


class TestRunSherlock:
    def _run(self, stdout):
        from unittest.mock import MagicMock, patch
        from src.pipeline.handle_fanout import _run_sherlock
        mock_result = MagicMock()
        mock_result.stdout = stdout
        with patch("subprocess.run", return_value=mock_result):
            return _run_sherlock("testuser")

    def test_found_line_parsed(self):
        result = self._run("[+] GitHub: https://github.com/testuser\n")
        assert len(result) == 1
        assert result[0]["site"] == "GitHub"
        assert result[0]["url"] == "https://github.com/testuser"

    def test_not_found_line_skipped(self):
        result = self._run("[-] Amazon: not found\n[+] LinkedIn: https://linkedin.com/in/x\n")
        assert len(result) == 1
        assert result[0]["site"] == "LinkedIn"

    def test_line_without_http_url_skipped(self):
        result = self._run("[+] SomeService: service://not-http\n")
        assert result == []

    def test_multiple_hits(self):
        stdout = "[+] GitHub: https://github.com/u\n[+] Twitter: https://twitter.com/u\n"
        result = self._run(stdout)
        assert len(result) == 2

    def test_empty_stdout(self):
        assert self._run("") == []

    def test_timeout_returns_empty(self):
        import subprocess
        from unittest.mock import patch
        from src.pipeline.handle_fanout import _run_sherlock
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=1)):
            assert _run_sherlock("x") == []

    def test_file_not_found_returns_empty(self):
        from unittest.mock import patch
        from src.pipeline.handle_fanout import _run_sherlock
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert _run_sherlock("x") == []


# ---------------------------------------------------------------------------
# pipeline.indicator_export pure helpers
# ---------------------------------------------------------------------------

class TestValidIpv4:
    def _v(self, v):
        from src.pipeline.indicator_export import _valid_ipv4
        return _valid_ipv4(v)

    def test_valid_ip(self):
        assert self._v("192.168.1.1") == "192.168.1.1"

    def test_invalid_ip(self):
        assert self._v("999.999.999.999") is None

    def test_non_ip_string(self):
        assert self._v("not-an-ip") is None

    def test_ipv6_returns_none(self):
        assert self._v("::1") is None


class TestNormalizeDomain:
    def _n(self, v):
        from src.pipeline.indicator_export import _normalize_domain
        return _normalize_domain(v)

    def test_valid_domain(self):
        assert self._n("example.com") == "example.com"

    def test_uppercase_lowercased(self):
        assert self._n("EXAMPLE.COM") == "example.com"

    def test_strips_trailing_dot(self):
        assert self._n("example.com.") == "example.com"

    def test_no_dot_returns_none(self):
        assert self._n("localhost") is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_all_numeric_labels_returns_none(self):
        # looks like IP address — not a domain
        assert self._n("192.168.1.1") is None

    def test_label_too_long_returns_none(self):
        long_label = "a" * 64
        assert self._n(f"{long_label}.com") is None


class TestNormalizeEmail:
    def _n(self, v):
        from src.pipeline.indicator_export import _normalize_email
        return _normalize_email(v)

    def test_valid_email(self):
        assert self._n("user@example.com") == "user@example.com"

    def test_uppercase_lowercased(self):
        assert self._n("User@Example.COM") == "user@example.com"

    def test_invalid_email_returns_none(self):
        assert self._n("not-an-email") is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_missing_domain_returns_none(self):
        assert self._n("user@") is None


class TestNormalizePhoneE164:
    def _n(self, v, region=None):
        from src.pipeline.indicator_export import normalize_phone_e164
        return normalize_phone_e164(v, default_region=region)

    def test_e164_passthrough(self):
        result = self._n("+6591234567")
        assert result == "+6591234567"

    def test_sg_8digit_number(self):
        result = self._n("91234567", region="SG")
        assert result == "+6591234567"

    def test_us_10digit_number(self):
        result = self._n("6505551234", region="US")
        assert result == "+16505551234"

    def test_too_short_returns_none(self):
        assert self._n("123") is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_none_returns_none(self):
        assert self._n(None) is None


class TestNormalizeIndicator:
    def _n(self, itype, value):
        from src.pipeline.indicator_export import normalize_indicator
        return normalize_indicator(itype, value)

    def test_domain(self):
        ind = self._n("domain", "example.com")
        assert ind is not None
        assert ind.indicator_type == "domain"
        assert ind.normalized_value == "example.com"

    def test_email(self):
        ind = self._n("email", "user@example.com")
        assert ind is not None
        assert ind.indicator_type == "email"
        assert ind.confidence == 0.8

    def test_ipv4(self):
        ind = self._n("ip", "1.2.3.4")
        assert ind is not None
        assert ind.indicator_type == "ipv4"

    def test_username(self):
        ind = self._n("username", "@octocat")
        assert ind is not None
        assert ind.normalized_value == "octocat"

    def test_full_name_with_space(self):
        ind = self._n("full_name", '"John Smith"')
        assert ind is not None
        assert ind.indicator_type == "full_name"

    def test_full_name_without_space_returns_none(self):
        assert self._n("full_name", "SingleName") is None

    def test_unknown_type_returns_none(self):
        assert self._n("unknown_type", "value") is None

    def test_empty_value_returns_none(self):
        assert self._n("email", "") is None

    def test_invalid_email_returns_none(self):
        assert self._n("email", "not-an-email") is None


class TestExtractIndicatorsFromText:
    def _e(self, text):
        from src.pipeline.indicator_export import extract_indicators_from_text
        return extract_indicators_from_text(text)

    def test_extracts_email(self):
        results = self._e("contact me at user@example.com for details")
        types = {r.indicator_type for r in results}
        assert "email" in types

    def test_extracts_ip(self):
        results = self._e("server at 192.168.1.100 is down")
        types = {r.indicator_type for r in results}
        assert "ipv4" in types

    def test_empty_text_returns_empty(self):
        assert self._e("") == []

    def test_none_returns_empty(self):
        assert self._e(None) == []

    def test_no_indicators_returns_empty(self):
        assert self._e("hello world no indicators here") == []

    def test_deduplicates_same_email(self):
        results = self._e("send to user@x.com and user@x.com again")
        emails = [r for r in results if r.indicator_type == "email"]
        assert len(emails) == 1
