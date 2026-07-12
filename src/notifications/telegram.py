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


def _send_sync(text: str, parse_mode: str = "HTML") -> bool:
    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": _CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if _THREAD_ID:
        payload["message_thread_id"] = _THREAD_ID

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status == 200
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        logger.warning("Telegram send failed: %s %s", e.code, body)
        return False
    except Exception:
        logger.debug("Telegram send failed (network)", exc_info=True)
        return False


async def send(text: str, parse_mode: str = "HTML") -> bool:
    if not _BOT_TOKEN:
        if not _get_config():
            return False
    return await asyncio.to_thread(_send_sync, text, parse_mode)
