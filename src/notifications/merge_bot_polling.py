import logging
import threading

import anyio
from anyio.from_thread import BlockingPortal

from src.notifications import telegram

logger = logging.getLogger(__name__)


async def run_callback_poller() -> None:
    stop = threading.Event()
    errors: list[Exception] = []
    async with BlockingPortal() as commands:
        def serve() -> None:
            try:
                anyio.run(_poll_updates, stop, commands)
            except Exception as exc:
                logger.exception("merge-bot: callback thread failed")
                errors.append(exc)

        worker = threading.Thread(target=serve, name="telegram-callbacks", daemon=True)
        worker.start()
        try:
            await anyio.to_thread.run_sync(worker.join, abandon_on_cancel=True)
        except anyio.get_cancelled_exc_class():
            logger.info("merge-bot: stopping callback thread")
        finally:
            # Join the owned thread while keeping the DB-command loop responsive for cleanup.
            stop.set()
            with anyio.CancelScope(shield=True):
                if worker.is_alive():
                    await commands.stop(cancel_remaining=True)
                await anyio.to_thread.run_sync(worker.join)
        if errors:
            raise errors[0]


async def _poll_updates(stop: threading.Event, commands: BlockingPortal) -> None:
    from src.notifications import merge_bot

    logger.info("merge-bot: callback poller starting (long-poll mode)")
    try:
        info = await anyio.to_thread.run_sync(telegram.get_webhook_info)
        webhook_url = (info.get("result") or {}).get("url", "")
        if webhook_url:
            logger.warning("merge-bot: deleting existing webhook to enable long-polling")
            await anyio.to_thread.run_sync(telegram.delete_webhook)
        else:
            logger.info("merge-bot: no webhook set, long-polling ready")
    except Exception:
        logger.exception("merge-bot: webhook check failed (non-fatal, continuing)")

    slots = anyio.CapacityLimiter(8)
    feedback_send, feedback_receive = anyio.create_memory_object_stream[dict](32)

    async def send_feedback() -> None:
        async with feedback_receive.clone() as receiver:
            async for upd in receiver:
                try:
                    cq = upd.get("callback_query") or {}
                    message = upd.get("message") or {}
                    if cq.get("id"):
                        await anyio.to_thread.run_sync(
                            telegram.answer_callback_query, cq["id"], "Busy — please retry this button shortly",
                        )
                    elif message.get("chat") and message.get("message_id") is not None:
                        await anyio.to_thread.run_sync(
                            telegram.reply_message_sync, message["chat"]["id"],
                            "Busy — please retry this command shortly", message["message_id"],
                        )
                except Exception:
                    logger.exception("merge-bot: busy feedback failed")

    async def dispatch(upd: dict) -> None:
        try:
            if upd.get("callback_query"):
                await merge_bot._handle_callback(upd["callback_query"])
            elif upd.get("message"):
                await anyio.to_thread.run_sync(commands.call, merge_bot._handle_message, upd["message"])
        except Exception:
            logger.exception("merge-bot: error handling update_id=%s", upd.get("update_id"))
        finally:
            slots.release_on_behalf_of(upd["update_id"])

    try:
        async with feedback_send, feedback_receive:
            async with anyio.create_task_group() as tasks:
                for _ in range(8):
                    tasks.start_soon(send_feedback)
                try:
                    while not stop.is_set():
                        try:
                            updates = await anyio.to_thread.run_sync(telegram.get_updates, merge_bot._offset, 25)
                            if not updates.get("ok"):
                                logger.debug("merge-bot: getUpdates not ok: %s", updates)
                                await anyio.sleep(5)
                                continue
                            for upd in updates.get("result") or []:
                                message = upd.get("message") or {}
                                if not upd.get("callback_query") and not (message.get("text") or "").startswith("/"):
                                    merge_bot._offset = upd["update_id"] + 1
                                    continue
                                try:
                                    slots.acquire_on_behalf_of_nowait(upd["update_id"])
                                except anyio.WouldBlock:
                                    await feedback_send.send(upd)
                                else:
                                    tasks.start_soon(dispatch, upd)
                                merge_bot._offset = upd["update_id"] + 1
                        except Exception:
                            logger.exception("merge-bot: getUpdates loop error; backing off 30s")
                            await anyio.sleep(30)
                finally:
                    await feedback_send.aclose()
    except anyio.get_cancelled_exc_class():
        logger.info("merge-bot: poller cancelled")
