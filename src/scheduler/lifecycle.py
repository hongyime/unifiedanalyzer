import logging
import signal
from collections.abc import Awaitable, Callable
from contextlib import ExitStack

import anyio

logger = logging.getLogger(__name__)


async def run_until_terminated(run: Callable[[], Awaitable[None]]) -> None:
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    with ExitStack() as resources:
        def restore_handlers() -> None:
            for sig, handler in previous.items():
                if handler is not None and signal.getsignal(sig) != handler:
                    signal.signal(sig, handler)

        # Run after the receiver closes: asyncio removes its handlers on exit,
        # which otherwise loses the enclosing runner's SIGINT callback.
        resources.callback(restore_handlers)
        try:
            signals = resources.enter_context(anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT))
        except NotImplementedError:
            await run()
            return

        async with anyio.create_task_group() as tasks:
            async def terminate() -> None:
                async for signum in signals:
                    logger.info("Scheduler termination requested: signal=%s", signum)
                    tasks.cancel_scope.cancel()
                    return

            tasks.start_soon(terminate)
            try:
                await run()
            finally:
                tasks.cancel_scope.cancel()
