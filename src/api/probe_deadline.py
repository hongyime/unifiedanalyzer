from collections.abc import Awaitable, Callable
from typing import TypeVar

import anyio

Result = TypeVar("Result")


async def with_deadline(run: Callable[[], Awaitable[Result]], timeout: float) -> Result:
    if timeout <= 0:
        raise TimeoutError
    with anyio.fail_after(timeout):
        return await run()
