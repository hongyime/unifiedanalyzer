"""Unit tests for the Telegram command-center: /whois gating and /digest.

Covers:
  - _is_authorized: empty allowlist, listed id, unlisted id, whitespace in env
  - _handle_message: unauthorized /whois → ⛔ rejection, _whois_lookup NOT called
  - _handle_message: authorized /whois → _whois_lookup called, reply sent
  - _handle_message: non-command text → silently ignored
  - _handle_message: /whois with no arg → usage hint, no lookup
"""
from __future__ import annotations

import asyncio
import importlib
import os

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh():
    """Reload merge_bot to get a clean module with reset globals."""
    import src.notifications.merge_bot as mb
    importlib.reload(mb)
    return mb


def _msg(text: str, from_id: int = 9998, chat_id: int = -100123, message_id: int = 1) -> dict:
    return {
        "from": {"id": from_id},
        "chat": {"id": chat_id},
        "message_id": message_id,
        "text": text,
    }


# ---------------------------------------------------------------------------
# _is_authorized — pure function, no side effects
# ---------------------------------------------------------------------------

def test_is_authorized_empty_env_rejects_all():
    mb = _fresh()
    with _env(TELEGRAM_ALLOWED_USER_IDS=""):
        assert mb._is_authorized("123456") is False
        assert mb._is_authorized("") is False


def test_is_authorized_listed_id_passes():
    mb = _fresh()
    with _env(TELEGRAM_ALLOWED_USER_IDS="111222,333444"):
        assert mb._is_authorized("111222") is True
        assert mb._is_authorized("333444") is True


def test_is_authorized_unlisted_id_fails():
    mb = _fresh()
    with _env(TELEGRAM_ALLOWED_USER_IDS="111222,333444"):
        assert mb._is_authorized("999999") is False


def test_is_authorized_strips_spaces_in_env():
    mb = _fresh()
    with _env(TELEGRAM_ALLOWED_USER_IDS=" 111222 , 333444 "):
        assert mb._is_authorized("111222") is True


# ---------------------------------------------------------------------------
# _handle_message gating
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unauthorized_whois_sends_rejection(monkeypatch):
    """Unauthorized from-id issuing /whois → ⛔ reply; _whois_lookup never called."""
    mb = _fresh()

    reply_calls: list[tuple] = []
    lookup_called = []

    async def fake_whois(query):
        lookup_called.append(query)
        return "should not be called"

    async def fake_to_thread(fn, *args, **kwargs):
        reply_calls.append(args)
        return {"ok": True}

    monkeypatch.setattr(mb, "_whois_lookup", fake_whois)
    monkeypatch.setattr(mb.asyncio, "to_thread", fake_to_thread)

    with _env(TELEGRAM_ALLOWED_USER_IDS="111222333"):
        await mb._handle_message(_msg("/whois thejianhaotan", from_id=9998))

    assert not lookup_called, "_whois_lookup must NOT be called for unauthorized user"
    assert reply_calls, "Should have called asyncio.to_thread for rejection reply"
    texts = [arg for call_args in reply_calls for arg in call_args if isinstance(arg, str)]
    assert any("⛔" in t or "not authorized" in t.lower() for t in texts), (
        f"Expected rejection text in {texts}"
    )


@pytest.mark.asyncio
async def test_authorized_whois_calls_lookup(monkeypatch):
    """Authorized from-id issuing /whois → _whois_lookup is called."""
    mb = _fresh()

    lookup_called = []
    reply_texts: list[str] = []

    async def fake_whois(query):
        lookup_called.append(query)
        return f"👤 <b>Result for {query}</b>"

    async def fake_to_thread(fn, *args, **kwargs):
        for a in args:
            if isinstance(a, str):
                reply_texts.append(a)
        return {"ok": True}

    monkeypatch.setattr(mb, "_whois_lookup", fake_whois)
    monkeypatch.setattr(mb.asyncio, "to_thread", fake_to_thread)

    with _env(TELEGRAM_ALLOWED_USER_IDS="111222333"):
        await mb._handle_message(_msg("/whois thejianhaotan", from_id=111222333))

    assert lookup_called == ["thejianhaotan"], f"Expected lookup for 'thejianhaotan'; got {lookup_called}"
    assert any("Result for thejianhaotan" in t for t in reply_texts), (
        f"Expected lookup result in reply; got {reply_texts}"
    )


@pytest.mark.asyncio
async def test_whois_no_arg_sends_usage(monkeypatch):
    """/whois with no argument → usage hint, no lookup."""
    mb = _fresh()

    lookup_called = []
    reply_texts: list[str] = []

    async def fake_whois(query):
        lookup_called.append(query)
        return "should not happen"

    async def fake_to_thread(fn, *args, **kwargs):
        for a in args:
            if isinstance(a, str):
                reply_texts.append(a)
        return {"ok": True}

    monkeypatch.setattr(mb, "_whois_lookup", fake_whois)
    monkeypatch.setattr(mb.asyncio, "to_thread", fake_to_thread)

    with _env(TELEGRAM_ALLOWED_USER_IDS="111222333"):
        await mb._handle_message(_msg("/whois", from_id=111222333))

    assert not lookup_called, "No lookup for empty /whois"
    assert any("usage" in t.lower() or "whois" in t.lower() for t in reply_texts), (
        f"Expected usage hint; got {reply_texts}"
    )


@pytest.mark.asyncio
async def test_non_command_text_ignored(monkeypatch):
    """Plain text (no leading /) is silently ignored."""
    mb = _fresh()

    to_thread_called = []

    async def fake_to_thread(fn, *args, **kwargs):
        to_thread_called.append(True)
        return {"ok": True}

    monkeypatch.setattr(mb.asyncio, "to_thread", fake_to_thread)

    with _env(TELEGRAM_ALLOWED_USER_IDS="111222333"):
        await mb._handle_message(_msg("hello there", from_id=111222333))

    assert not to_thread_called, "Non-command text must be silently ignored"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _env:
    """Context manager: temporarily set env vars."""

    def __init__(self, **kwargs: str):
        self._new = kwargs
        self._old: dict[str, str | None] = {}

    def __enter__(self):
        for k, v in self._new.items():
            self._old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *_):
        for k, old in self._old.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
