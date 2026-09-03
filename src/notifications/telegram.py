import os
import json
import logging
import asyncio
import subprocess
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

# Windows: don't pop a visible console window when shelling out to `tailscale`.
# 0 on POSIX (flag absent there).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_BOT_TOKEN: str | None = None
_CHAT_ID: str | None = None
_THREAD_ID: int | None = None
_TAILSCALE_IP: str | None = None
_API_PORT: str = "8002"
# The collector-health alert links to the UnifiedCollector dashboard (a separate
# service on :8700, which has the rich /collectors view), NOT the analyzer's own
# :8002 dashboard. Configurable via COLLECTOR_DASHBOARD_PORT.
_COLLECTOR_PORT: str = "8700"


def _get_config():
    global _BOT_TOKEN, _CHAT_ID, _THREAD_ID, _TAILSCALE_IP, _API_PORT, _COLLECTOR_PORT
    _BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    _CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    _API_PORT = os.getenv("API_PORT", "8002")
    _COLLECTOR_PORT = os.getenv("COLLECTOR_DASHBOARD_PORT", "8700")
    thread = os.getenv("TELEGRAM_THREAD_ID")
    _THREAD_ID = int(thread) if thread else None
    if not _BOT_TOKEN or not _CHAT_ID:
        return False
    # Prefer an explicit TAILSCALE_IP env over CLI detection: the `tailscale`
    # binary isn't present inside the container, so _detect_tailscale_ip()
    # returns None there and alert URLs would fall back to 127.0.0.1 (useless
    # when the alert is opened from a phone / another machine).
    _TAILSCALE_IP = os.getenv("TAILSCALE_IP") or _detect_tailscale_ip()
    return True


def _detect_tailscale_ip() -> str | None:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5,
            creationflags=_NO_WINDOW,
        )
        if result.returncode == 0:
            ip = result.stdout.strip().split("\n")[0].strip()
            if ip:
                return ip
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_dashboard_url() -> str:
    host = _TAILSCALE_IP or "127.0.0.1"
    return f"http://{host}:{_API_PORT}"


def get_collector_dashboard_url() -> str:
    """UnifiedCollector dashboard (:8700 by default) — used for collector-health
    alerts so the link opens the live collector /collectors view, not the
    analyzer's own :8002 dashboard."""
    host = _TAILSCALE_IP or "127.0.0.1"
    return f"http://{host}:{_COLLECTOR_PORT}"


def _preview(text: str, limit: int = 1000) -> str:
    single_line = " ".join(str(text).split())
    return single_line[:limit]


async def _audit_send(
    *,
    text: str,
    message_type: str,
    status: str,
    telegram_message_id: int | None = None,
    related_run_id: str | None = None,
    related_alert_id: str | None = None,
    error: str | None = None,
) -> None:
    try:
        from src.db.connection import get_analyzer_pool

        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notification_audit (
                    channel, chat_id, message_type, text_preview, status,
                    telegram_message_id, related_run_id, related_alert_id, error
                )
                VALUES (
                    'telegram', $1, $2, $3, $4,
                    $5, $6::uuid, $7::uuid, $8
                )
                """,
                _CHAT_ID,
                message_type[:80],
                _preview(text),
                status,
                telegram_message_id,
                related_run_id,
                related_alert_id,
                (error[:1000] if error else None),
            )
    except Exception:
        logger.debug("Telegram notification audit write failed (non-fatal)", exc_info=True)


def _send_sync(
    text: str,
    parse_mode: str = "HTML",
    reply_markup: dict | None = None,
) -> tuple[bool, int | None, str | None]:
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    payload: dict = {
        "chat_id": _CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if _THREAD_ID:
        payload["message_thread_id"] = _THREAD_ID

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        body = resp.read().decode("utf-8", errors="replace")
        message_id = None
        try:
            parsed = json.loads(body)
            message_id = parsed.get("result", {}).get("message_id")
        except (TypeError, json.JSONDecodeError):
            pass
        return resp.status == 200, message_id, None if resp.status == 200 else body[:200]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        logger.warning("Telegram send failed: %s %s", e.code, body)
        return False, None, f"{e.code} {body}"
    except Exception:
        logger.debug("Telegram send failed (network)", exc_info=True)
        return False, None, "network_error"


_SEND_MAX_RETRIES = int(os.getenv("TELEGRAM_SEND_MAX_RETRIES", "4"))


def _parse_retry_after(error: str | None) -> int | None:
    """Extract Telegram 429 retry_after seconds from a failed-send error, else None.

    Convention: every outbound call throttles + backs off honoring the server's
    retry_after, and never silently drops the message.
    """
    if not error or "429" not in error:
        return None
    try:
        body = error[error.index("{"):]
        secs = (json.loads(body).get("parameters") or {}).get("retry_after")
        if secs is not None:
            return max(1, int(secs))
    except Exception:
        pass
    marker = "retry after "
    if marker in error:
        digits = ""
        for ch in error.split(marker, 1)[1]:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            return int(digits)
    return 5


async def send(
    text: str,
    parse_mode: str = "HTML",
    *,
    reply_markup: dict | None = None,
    message_type: str = "general",
    related_run_id: str | None = None,
    related_alert_id: str | None = None,
    pin: bool = False,
) -> bool:
    if not _BOT_TOKEN:
        if not _get_config():
            await _audit_send(
                text=text,
                message_type=message_type,
                status="skipped",
                related_run_id=related_run_id,
                related_alert_id=related_alert_id,
                error="telegram_not_configured",
            )
            return False
    # Dynamic backoff: honor Telegram's 429 retry_after and retry the SAME send
    # (never drop the message) up to a bounded count. Prevents the burst-429
    # storm that silently lost merge-review cards.
    ok = False
    message_id = None
    error = None
    for _attempt in range(_SEND_MAX_RETRIES + 1):
        ok, message_id, error = await asyncio.to_thread(_send_sync, text, parse_mode, reply_markup)
        if ok:
            break
        retry_after = _parse_retry_after(error)
        if retry_after is None or _attempt >= _SEND_MAX_RETRIES:
            break
        logger.info(
            "Telegram 429 rate-limit; backing off %ss then retrying (attempt %d/%d)",
            retry_after, _attempt + 1, _SEND_MAX_RETRIES,
        )
        await asyncio.sleep(retry_after + 1)
    await _audit_send(
        text=text,
        message_type=message_type,
        status="sent" if ok else "failed",
        telegram_message_id=message_id,
        related_run_id=related_run_id,
        related_alert_id=related_alert_id,
        error=error,
    )
    # Merge-review cards get pinned so the collector-update flood doesn't bury
    # them; the callback poller unpins on decision. Best-effort, non-fatal.
    if ok and pin and message_id:
        try:
            res = await asyncio.to_thread(pin_chat_message, _CHAT_ID, message_id)
            if not res.get("ok"):
                logger.warning(
                    "pin_chat_message failed (bot may lack can_pin_messages right): %s",
                    res.get("error"),
                )
        except Exception:
            logger.debug("pin_chat_message error (non-fatal)", exc_info=True)
    return ok


# ──────────────────────────────────────────────────────
# Bot-API helpers for the merge-review callback poller
# ──────────────────────────────────────────────────────

def _bot_post(method: str, payload: dict) -> dict:
    """POST to a Telegram Bot API method; return parsed JSON."""
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        logger.warning("Telegram %s failed: %s %s", method, e.code, body)
        return {"ok": False, "error": f"{e.code} {body}"}
    except Exception as exc:
        logger.debug("Telegram %s error", method, exc_info=True)
        return {"ok": False, "error": str(exc)}


def _bot_get(method: str, params: dict | None = None) -> dict:
    """GET a Telegram Bot API method (e.g. getUpdates, getWebhookInfo)."""
    import urllib.parse
    base = f"https://api.telegram.org/bot{_BOT_TOKEN}/{method}"
    if params:
        base = f"{base}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(base, headers={"Accept": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=35)  # ≥ long-poll timeout
        return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        logger.warning("Telegram %s GET failed: %s %s", method, e.code, body)
        return {"ok": False, "error": f"{e.code} {body}"}
    except Exception as exc:
        logger.debug("Telegram %s GET error", method, exc_info=True)
        return {"ok": False, "error": str(exc)}


def answer_callback_query(callback_query_id: str, text: str = "") -> dict:
    """Acknowledge a callback query (clears the spinner on the tapped button)."""
    _get_config()
    return _bot_post("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text[:200] if text else "",
    })


def edit_message_text(chat_id: int | str, message_id: int, text: str) -> dict:
    """Replace the text of a sent message (also removes any inline keyboard)."""
    _get_config()
    return _bot_post("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text[:4096],
        "parse_mode": "HTML",
    })


def get_updates(
    offset: int = 0,
    timeout: int = 25,
    allowed_updates: list[str] | None = None,
) -> dict:
    """Long-poll getUpdates.  Default requests callback_query + message updates."""
    _get_config()
    params: dict = {
        "allowed_updates": json.dumps(
            allowed_updates if allowed_updates is not None else ["callback_query", "message"]
        ),
        "timeout": timeout,
    }
    if offset:
        params["offset"] = offset
    return _bot_get("getUpdates", params)


def get_webhook_info() -> dict:
    """Return current webhook info (result.url is empty string when none set)."""
    _get_config()
    return _bot_get("getWebhookInfo")


def delete_webhook() -> dict:
    """Remove any set webhook so getUpdates long-polling works."""
    _get_config()
    return _bot_post("deleteWebhook", {"drop_pending_updates": False})


def pin_chat_message(chat_id: int | str, message_id: int, *, disable_notification: bool = True) -> dict:
    """Pin a message so it stays visible above the collector-update flood.

    disable_notification suppresses the pin alert; the tiny 'pinned a message'
    service line may still appear on some clients. Requires the bot to have the
    can_pin_messages right in a group/supergroup (fails gracefully otherwise).
    """
    _get_config()
    return _bot_post("pinChatMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
        "disable_notification": disable_notification,
    })


def unpin_chat_message(chat_id: int | str, message_id: int) -> dict:
    """Unpin a specific message (called when a merge decision is made)."""
    _get_config()
    return _bot_post("unpinChatMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
    })


def reply_message_sync(
    chat_id: int | str,
    text: str,
    reply_to_message_id: int | None = None,
) -> dict:
    """Send an HTML reply to *chat_id*, optionally quoting *reply_to_message_id*.

    Called via asyncio.to_thread from the command-center message handler.
    """
    _get_config()
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if _THREAD_ID:
        payload["message_thread_id"] = _THREAD_ID
    return _bot_post("sendMessage", payload)
