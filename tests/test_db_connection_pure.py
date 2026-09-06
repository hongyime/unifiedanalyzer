"""
QA-lane tests for pure helper functions in src/db/connection.py:
- _parse_dsn: DSN URL parsing to connection params dict
- _get_env: required env var retrieval
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# connection._parse_dsn
# ---------------------------------------------------------------------------

from src.db.connection import _parse_dsn


class TestParseDsn:
    def test_full_dsn_parsed(self):
        result = _parse_dsn("postgres://collector:secret@db-host:5433/mydb")
        assert result["host"] == "db-host"
        assert result["port"] == 5433
        assert result["user"] == "collector"
        assert result["password"] == "secret"
        assert result["database"] == "mydb"

    def test_localhost_normalized_to_127(self):
        result = _parse_dsn("postgres://u:p@localhost:5432/db")
        assert result["host"] == "127.0.0.1"

    def test_default_port_5432(self):
        result = _parse_dsn("postgres://u:p@host/db")
        assert result["port"] == 5432

    def test_default_user_collector(self):
        result = _parse_dsn("postgres://:p@host/db")
        assert result["user"] == "collector"

    def test_empty_password_allowed(self):
        result = _parse_dsn("postgres://u@host/db")
        assert result["password"] == "" or result["password"] is None

    def test_database_leading_slash_stripped(self):
        result = _parse_dsn("postgres://u:p@host:5432/mydb")
        assert result["database"] == "mydb"

    def test_real_ci_url(self):
        result = _parse_dsn("postgres://collector:collector@localhost:5432/unifiedanalyzer")
        assert result["host"] == "127.0.0.1"
        assert result["database"] == "unifiedanalyzer"
        assert result["user"] == "collector"


# ---------------------------------------------------------------------------
# connection._get_env
# ---------------------------------------------------------------------------

from src.db.connection import _get_env


class TestGetEnv:
    def test_returns_set_value(self, monkeypatch):
        monkeypatch.setenv("TEST_GET_ENV_KEY", "my_value")
        assert _get_env("TEST_GET_ENV_KEY") == "my_value"

    def test_raises_when_unset(self, monkeypatch):
        monkeypatch.delenv("TEST_GET_ENV_MISSING", raising=False)
        with pytest.raises(RuntimeError, match="Missing required"):
            _get_env("TEST_GET_ENV_MISSING")

    def test_raises_when_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_GET_ENV_EMPTY", "")
        with pytest.raises(RuntimeError):
            _get_env("TEST_GET_ENV_EMPTY")
