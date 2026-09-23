from __future__ import annotations

import importlib
import threading

import anyio
import pytest

from src.notifications import merge_bot as bot


@pytest.fixture(autouse=True)
def isolated_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.reload(bot)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
    monkeypatch.setattr(bot.telegram, "get_webhook_info", lambda: {"ok": True, "result": {"url": ""}})
    monkeypatch.setattr(bot.telegram, "_get_config", lambda: True)
    monkeypatch.setattr(bot.telegram, "_bot_post", lambda *args, **kwargs: {"ok": True})


@pytest.mark.asyncio
@pytest.mark.parametrize("preceding_text", ["ordinary message", "/whois slow"])
async def test_saturation_never_silently_discards_command(monkeypatch: pytest.MonkeyPatch, preceding_text: str) -> None:
    # Given a batch that previously filled every slot before its command ran.
    release, stop, feedback = threading.Event(), threading.Event(), threading.Event()
    cancelled = anyio.get_cancelled_exc_class()
    batch = [{"update_id": i, "message": {"message_id": i, "chat": {"id": -100},
             "from": {"id": 12345}, "text": preceding_text}} for i in range(8)]
    batch.append({"update_id": 8, "message": {"message_id": 99, "chat": {"id": -100},
                  "from": {"id": 12345}, "text": "/digest"}})

    def updates(offset: int, timeout: int) -> dict:
        if offset == 0:
            return {"ok": True, "result": batch}
        stop.wait(5)
        raise cancelled()

    async def handle(message: dict) -> None:
        if message["text"] == "/digest":
            feedback.set()
        elif message["text"].startswith("/"):
            await anyio.to_thread.run_sync(release.wait)

    def reply(chat: int, text: str, message_id: int) -> dict[str, bool]:
        if message_id == 99:
            feedback.set()
        return {"ok": True}

    monkeypatch.setattr(bot.telegram, "get_updates", updates)
    monkeypatch.setattr(bot.telegram, "reply_message_sync", reply)
    monkeypatch.setattr(bot, "_handle_message", handle)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bot.run_callback_poller)
        try:
            # When the command arrives, then execute it or explicitly report busy.
            responded = await anyio.to_thread.run_sync(feedback.wait, 3)
        finally:
            release.set()
            stop.set()
            tasks.cancel_scope.cancel()
    assert responded, "A saturated callback poller silently discarded the operator command"


@pytest.mark.asyncio
async def test_slow_overflow_ack_does_not_block_next_overflow_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given eight active handlers and a stalled busy response for callback nine.
    release, stop, slow_started, next_ack = (threading.Event() for _ in range(4))
    cancelled = anyio.get_cancelled_exc_class()

    def updates(offset: int, timeout: int) -> dict:
        if offset == 0:
            return {"ok": True, "result": [{"update_id": i, "callback_query": {"id": str(i)}} for i in range(8)]}
        if offset == 8:
            return {"ok": True, "result": [
                {"update_id": 8, "callback_query": {"id": "slow"}},
                {"update_id": 9, "callback_query": {"id": "next"}},
            ]}
        stop.wait(5)
        raise cancelled()

    async def handle(query: dict) -> None:
        await anyio.to_thread.run_sync(release.wait)

    def answer(query_id: str, text: str = "") -> dict[str, bool]:
        if query_id == "slow":
            slow_started.set()
            release.wait(5)
        if query_id == "next":
            next_ack.set()
        return {"ok": True}

    monkeypatch.setattr(bot.telegram, "get_updates", updates)
    monkeypatch.setattr(bot.telegram, "answer_callback_query", answer)
    monkeypatch.setattr(bot, "_handle_callback", handle)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(bot.run_callback_poller)
        try:
            assert await anyio.to_thread.run_sync(slow_started.wait, 5)
            # When callback ten arrives, then its busy response starts independently.
            responded = await anyio.to_thread.run_sync(next_ack.wait, 3)
        finally:
            release.set()
            stop.set()
            tasks.cancel_scope.cancel()
    assert responded, "One slow overflow acknowledgement blocked the following callback"


@pytest.mark.asyncio
async def test_shutdown_joins_inflight_decision_write(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given an acknowledged decision whose blocking HTTP write has not finished.
    started, release, write_finished, stop, poll_finished = (threading.Event() for _ in range(5))
    data = bot.callback_data_yes("entity-a", "entity-b")

    def updates(offset: int, timeout: int) -> dict:
        if offset == 0:
            return {"ok": True, "result": [{"update_id": 1, "callback_query": {
                "id": "decision", "data": data, "from": {"id": 12345},
            }}]}
        stop.wait(5)
        return {"ok": True, "result": []}

    def post(url: str, payload: dict) -> dict[str, bool]:
        started.set()
        release.wait(10)
        write_finished.set()
        return {"ok": True}

    scope = anyio.CancelScope()

    async def poll() -> None:
        with scope:
            await bot.run_callback_poller()
        poll_finished.set()

    monkeypatch.setattr(bot.telegram, "get_updates", updates)
    monkeypatch.setattr(bot, "_http_post_sync", post)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(poll)
        try:
            assert await anyio.to_thread.run_sync(started.wait, 5)
            # When shutdown begins, then it cannot abandon an active decision write.
            scope.cancel()
            stop.set()
            ended_before_write = await anyio.to_thread.run_sync(poll_finished.wait, 0.2)
        finally:
            release.set()
            stop.set()
    assert not ended_before_write, "Shutdown abandoned an in-flight decision write"
    assert write_finished.is_set() and poll_finished.is_set()
    assert not any(t.name == "telegram-callbacks" for t in threading.enumerate())
