import os
import logging
import subprocess
import httpx

logger = logging.getLogger(__name__)

_BOT_TOKEN: str | None = None
_CHAT_ID: str | None = None
_THREAD_ID: int | None = None
_TAILSCALE_IP: str | None = None
_API_PORT: str = "8002"


def _get_config():
    global _BOT_TOKEN, _CHAT_ID, _THREAD_ID, _TAILSCALE_IP, _API_PORT
    _BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    _CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    _API_PORT = os.getenv("API_PORT", "8002")
    thread = os.getenv("TELEGRAM_THREAD_ID")
    _THREAD_ID = int(thread) if thread else None
    if not _BOT_TOKEN or not _CHAT_ID:
        return False
    _TAILSCALE_IP = _detect_tailscale_ip()
    return True


def _detect_tailscale_ip() -> str | None:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True, text=True, timeout=5,
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


async def send(text: str, parse_mode: str = "HTML") -> bool:
    if not _BOT_TOKEN:
        if not _get_config():
            return False

    url = f"https://api.telegram.org/bot{_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": _CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if _THREAD_ID:
        payload["message_thread_id"] = _THREAD_ID

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:200])
                return False
            return True
    except Exception:
        logger.debug("Telegram send failed (network)", exc_info=True)
        return False
