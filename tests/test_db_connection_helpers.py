"""
QA-lane tests for src.db.connection.is_db_transient_error.

Covers:
- True cases: Postgres recovery/startup messages, connection refused, OSError,
  asyncio.TimeoutError, asyncpg.InterfaceError
- False cases: UndefinedTableError, ValueError, generic RuntimeError
"""
from __future__ import annotations

import asyncio

import asyncpg
import pytest

from src.db.connection import is_db_transient_error


# ---------------------------------------------------------------------------
# True cases — transient, should NOT trigger Telegram alert
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", [
    "the database system is in recovery mode",
    "The database system is in recovery mode",           # case-insensitive
    "the database system is starting up",
    "the database system is recovering",
    "database system is starting",
    "database is in recovery",
    "cannot connect to server",
    "connection refused",
    "server closed the connection unexpectedly",
    "terminating connection due to administrator command",
    "too many connections",
])
def test_is_db_transient_error_true_for_postgres_messages(msg):
    exc = asyncpg.PostgresError(msg)
    assert is_db_transient_error(exc) is True, (
        f"Expected True for message {msg!r}"
    )


def test_is_db_transient_error_true_for_oserror():
    assert is_db_transient_error(OSError("connection refused")) is True


def test_is_db_transient_error_true_for_asyncio_timeout():
    assert is_db_transient_error(asyncio.TimeoutError()) is True


def test_is_db_transient_error_true_for_asyncpg_interface_error():
    assert is_db_transient_error(asyncpg.InterfaceError("connection does not exist")) is True


def test_is_db_transient_error_true_for_connection_error():
    assert is_db_transient_error(ConnectionError("reset by peer")) is True


# ---------------------------------------------------------------------------
# False cases — real logic bugs, should fire Telegram alert
# ---------------------------------------------------------------------------

def test_is_db_transient_error_false_for_undefined_table():
    # asyncpg.UndefinedTableError is a logic/schema bug — must NOT be suppressed
    exc = asyncpg.exceptions.UndefinedTableError("relation \"foo\" does not exist")
    assert is_db_transient_error(exc) is False


def test_is_db_transient_error_false_for_value_error():
    assert is_db_transient_error(ValueError("bad input")) is False


def test_is_db_transient_error_false_for_generic_runtime_error():
    assert is_db_transient_error(RuntimeError("logic bug")) is False


def test_is_db_transient_error_false_for_key_error():
    assert is_db_transient_error(KeyError("missing key")) is False
