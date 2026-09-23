"""Regression contracts for responsive, retryable Telegram decisions."""
from __future__ import annotations

import importlib
import threading
from typing import TypedDict

import anyio
import pytest

from src.notifications import merge_bot as bot

ID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
ID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


class User(TypedDict):
    id: int


class Message(TypedDict):
    message_id: int
    chat: User


Callback = TypedDict("Callback", {"id": str, "data": str, "from": User, "message": Message})


def callback(data: str, query_id: str = "first") -> Callback:
    return {"id": query_id, "data": data, "from": {"id": 12345},
            "message": {"message_id": 999, "chat": {"id": -100123}}}


@pytest.fixture(autouse=True)
def isolated_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.reload(bot)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
    monkeypatch.setattr(bot.telegram, "_bot_post", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(bot.telegram, "_get_config", lambda: True)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,route", [("_apply_merge", "merge"), ("_apply_dismiss", "dismiss-match")])
async def test_decision_uses_configured_api_service(monkeypatch: pytest.MonkeyPatch, action: str, route: str) -> None:
    # Given a scheduler whose API lives in another container.
    monkeypatch.setenv("ANALYZER_INTERNAL_API_URL", "http://api-service:8123/")
    urls: list[str] = []

    def post(url: str, payload: dict[str, str | list[str]]) -> dict[str, bool]:
        urls.append(url)
        return {"ok": True}

    monkeypatch.setattr(bot, "_http_post_sync", post)
    # When either decision is applied, then the configured service receives it.
    await getattr(bot, action)(ID_A, ID_B)
    assert urls == [f"http://api-service:8123/api/entities/{route}"], "Decision was sent to scheduler localhost instead of the API service"


@pytest.mark.asyncio
async def test_concurrent_repeat_press_applies_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a first decision still waiting for its backend.
    data = bot.callback_data_yes(ID_A, ID_B)
    started, release = anyio.Event(), anyio.Event()
    calls: list[tuple[str, str]] = []

    async def merge(a: str, b: str) -> dict[str, bool]:
        calls.append((a, b))
        started.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(bot, "_apply_merge", merge)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bot._handle_callback, callback(data))
        await started.wait()
        try:
            # When the same button is pressed again before completion.
            with anyio.move_on_after(0.5) as second:
                await bot._handle_callback(callback(data, "repeat"))
        finally:
            release.set()
    # Then the repeat receives feedback without a second write or backend wait.
    assert not second.cancel_called, "Repeat press waited for an in-flight decision"
    assert calls == [(ID_A, ID_B)], "Concurrent presses applied the same decision twice"


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,action", [("y", "_apply_merge"), ("n", "_apply_dismiss")])
async def test_failed_decision_keeps_retry_card(monkeypatch: pytest.MonkeyPatch, decision: str, action: str) -> None:
    # Given a backend failure and an existing pinned review card.
    token = bot._make_token(ID_A, ID_B)
    edited: list[str] = []
    unpinned: list[int] = []
    replies: list[str] = []

    async def fail(a: str, b: str) -> dict[str, bool | str]:
        return {"ok": False, "error": "backend unavailable"}

    monkeypatch.setattr(bot, action, fail)
    monkeypatch.setattr(bot.telegram, "edit_message_text", lambda chat, msg, text: edited.append(text))
    monkeypatch.setattr(bot.telegram, "unpin_chat_message", lambda chat, msg: unpinned.append(msg))
    monkeypatch.setattr(bot.telegram, "reply_message_sync", lambda chat, text, msg: replies.append(text))
    # When the operator presses a decision button.
    await bot._handle_callback(callback(f"mrg:{decision}:{token}"))
    # Then the card remains actionable and a separate failure response is sent.
    assert not edited and not unpinned, "A failed decision removed the retry buttons or pinned card"
    assert len(replies) == 1
    assert token not in bot._resolved


@pytest.mark.asyncio
async def test_next_callback_is_acknowledged_while_first_backend_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a slow decision followed by a different button in the next update batch.
    first = bot.callback_data_yes(ID_A, ID_B)
    next_data = bot.callback_data_yes(ID_A, ID_C)
    first_started, release, next_ack = threading.Event(), threading.Event(), threading.Event()
    stop_poll = threading.Event()
    cancelled = anyio.get_cancelled_exc_class()

    def get_updates(offset: int, timeout: int) -> dict:
        if offset == 0:
            return {"ok": True, "result": [{"update_id": 100, "callback_query": callback(first)}]}
        if offset == 101:
            return {"ok": True, "result": [{"update_id": 101, "callback_query": callback(next_data, "next")}]}
        stop_poll.wait(10)
        raise cancelled()

    def answer(query_id: str, text: str = "") -> dict[str, bool]:
        if query_id == "next":
            next_ack.set()
        return {"ok": True}

    async def merge(a: str, b: str) -> dict[str, bool]:
        if b == ID_B:
            first_started.set()
            await anyio.to_thread.run_sync(release.wait)
        return {"ok": True}

    monkeypatch.setattr(bot.telegram, "get_webhook_info", lambda: {"ok": True, "result": {"url": ""}})
    monkeypatch.setattr(bot.telegram, "get_updates", get_updates)
    monkeypatch.setattr(bot.telegram, "answer_callback_query", answer)
    monkeypatch.setattr(bot, "_apply_merge", merge)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bot.run_callback_poller)
        try:
            assert await anyio.to_thread.run_sync(first_started.wait, 5)
            # When the next callback arrives, then its acknowledgement is independent.
            with anyio.move_on_after(3) as acknowledgement:
                acknowledged = await anyio.to_thread.run_sync(next_ack.wait, 2)
        finally:
            release.set()
            stop_poll.set()
            tasks.cancel_scope.cancel()
    assert acknowledged and not acknowledgement.cancel_called, "The next button acknowledgement was blocked behind the first backend request"


@pytest.mark.asyncio
async def test_callback_acknowledgement_survives_blocked_scheduler_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a running poller and a scheduler about to enter a synchronous phase.
    polling, pressed, acknowledged, stop = (threading.Event() for _ in range(4))
    cancelled = anyio.get_cancelled_exc_class()

    def updates(offset: int, timeout: int) -> dict:
        if offset == 0:
            polling.set()
            pressed.wait(5)
            return {"ok": True, "result": [{"update_id": 1, "callback_query": callback("unknown:button")}]}
        stop.wait(5)
        raise cancelled()

    def answer(query_id: str, text: str = "") -> dict[str, bool]:
        acknowledged.set()
        return {"ok": True}

    monkeypatch.setattr(bot.telegram, "get_webhook_info", lambda: {"ok": True, "result": {"url": ""}})
    monkeypatch.setattr(bot.telegram, "get_updates", updates)
    monkeypatch.setattr(bot.telegram, "answer_callback_query", answer)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bot.run_callback_poller)
        try:
            assert await anyio.to_thread.run_sync(polling.wait, 5)
            # When the operator presses while synchronous analysis blocks this loop.
            pressed.set()
            responded_while_blocked = acknowledged.wait(3)
        finally:
            stop.set()
            tasks.cancel_scope.cancel()
    # Then acknowledgement runs independently of the blocked scheduler loop.
    assert responded_while_blocked, "Synchronous analysis blocked Telegram button acknowledgement"
