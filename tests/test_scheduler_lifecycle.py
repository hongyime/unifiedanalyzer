import signal
from builtins import ExceptionGroup
from contextlib import contextmanager

import anyio
import pytest

from src.scheduler.lifecycle import run_until_terminated


@pytest.mark.asyncio
async def test_normal_scheduler_exit_restores_signal_handlers() -> None:
    # Given the process's existing handlers.
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    ran = anyio.Event()

    async def run() -> None:
        ran.set()

    # When the scheduler finishes normally, then its handlers are restored.
    await run_until_terminated(run)
    assert ran.is_set()
    assert {sig: signal.getsignal(sig) for sig in previous} == previous


@pytest.mark.asyncio
async def test_termination_waits_for_scheduler_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a running scheduler with asynchronous cleanup.
    cleaned = anyio.Event()
    sender, receiver = anyio.create_memory_object_stream[int](1)

    @contextmanager
    def receive_signals(*signals: signal.Signals):
        yield receiver

    monkeypatch.setattr(anyio, "open_signal_receiver", receive_signals)

    async def run() -> None:
        try:
            await sender.send(signal.SIGTERM)
            await anyio.sleep_forever()
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.lowlevel.checkpoint()
                cleaned.set()

    # When termination is requested, then cleanup finishes before return.
    await run_until_terminated(run)
    assert cleaned.is_set()
    await sender.aclose()
    await receiver.aclose()


@pytest.mark.asyncio
async def test_scheduler_failure_restores_signal_handlers() -> None:
    # Given an unexpected scheduler failure.
    previous = signal.getsignal(signal.SIGTERM)

    async def run() -> None:
        raise RuntimeError("scheduler failed")

    # When the failure propagates, then the temporary signal handler is removed.
    with pytest.raises((RuntimeError, ExceptionGroup)):
        await run_until_terminated(run)
    assert signal.getsignal(signal.SIGTERM) == previous
