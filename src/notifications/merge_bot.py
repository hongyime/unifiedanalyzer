"""Telegram 2-button merge-review bot.

Reused endpoints:
  ✅ MERGE   — POST /api/entities/merge          (entity_actions.merge_entities)
  ❌ REJECT  — POST /api/entities/dismiss-match  (entity_actions.dismiss_match)

callback_data budget:
  Two full UUIDs = 79 bytes → exceeds Telegram's 64-byte limit.
  Fix: deterministic 8-hex-char token keyed by sorted(idA,idB) SHA-256.
  "mrg:y:TOKEN" / "mrg:n:TOKEN" = 14 bytes ✓.
  _pair_store is in-memory (noted); survives while the scheduler runs.
  Stale buttons after a restart show "Stale card" and are no-ops.

The scheduler owns a dedicated callback thread so synchronous analysis cannot
block button acknowledgements. Database-backed commands stay on its original loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import urllib.error
import urllib.request

import anyio

from src.notifications import telegram
from src.notifications.merge_bot_lookup import whois_lookup as _whois_lookup
from src.notifications.merge_bot_polling import run_callback_poller

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token store — in-memory (does not survive scheduler restart; noted in docs)
# ---------------------------------------------------------------------------

# token (8 hex chars) → (entity_a_id, entity_b_id)
_pair_store: dict[str, tuple[str, str]] = {}

# Tokens whose pair has already been acted on (double-tap guard)
_resolved: set[str] = set()
_in_flight: set[str] = set()

# Persistent offset for getUpdates (in-memory; resets to 0 on restart which is safe)
_offset: int = 0


def _make_token(id_a: str, id_b: str) -> str:
    """Return an 8-char deterministic hex token for a sorted entity pair.

    Deterministic so the same pair always gets the same key; in-memory store
    maps the token back to the actual UUIDs.
    """
    key = ":".join(sorted([id_a, id_b]))
    token = hashlib.sha256(key.encode()).hexdigest()[:8]
    _pair_store[token] = (id_a, id_b)
    return token


def _lookup_pair(token: str) -> tuple[str, str] | None:
    """Retrieve (entity_a_id, entity_b_id) for a token, or None if stale."""
    return _pair_store.get(token)


def callback_data_yes(id_a: str, id_b: str) -> str:
    """Build ✅ callback_data string (≤ 64 bytes)."""
    return f"mrg:y:{_make_token(id_a, id_b)}"


def callback_data_no(id_a: str, id_b: str) -> str:
    """Build ❌ callback_data string (≤ 64 bytes)."""
    return f"mrg:n:{_make_token(id_a, id_b)}"


def parse_callback_data(data: str) -> tuple[str, str] | None:
    """Parse 'mrg:{y|n}:{token}' → ('y'|'n', token) or None for non-merge payloads."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "mrg" or parts[1] not in ("y", "n"):
        return None
    return parts[1], parts[2]


# ---------------------------------------------------------------------------
# HTTP helpers (sync, called via asyncio.to_thread)
# ---------------------------------------------------------------------------

def _api_port() -> str:
    return os.getenv("API_PORT", "8002")


def _api_base_url() -> str:
    fallback = f"http://127.0.0.1:{_api_port()}"
    return (os.getenv("ANALYZER_INTERNAL_API_URL") or fallback).rstrip("/")


def _http_post_sync(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning("merge-bot HTTP POST %s → %s %s", url, e.code, body)
        return {"ok": False, "error": f"{e.code} {body}"}
    except Exception as exc:
        logger.warning("merge-bot HTTP POST %s failed: %s", url, exc)
        return {"ok": False, "error": str(exc)}


async def _apply_merge(id_a: str, id_b: str) -> dict:
    """Call POST /api/entities/merge (entity_actions.merge_entities)."""
    url = f"{_api_base_url()}/api/entities/merge"
    payload = {"source_entity_ids": [id_a, id_b], "reason": "telegram_merge_bot"}
    return await anyio.to_thread.run_sync(_http_post_sync, url, payload)


async def _apply_dismiss(id_a: str, id_b: str) -> dict:
    """Call POST /api/entities/dismiss-match (entity_actions.dismiss_match).

    The dashboard uses this endpoint for the ❌ 'Not same person' action; it
    records a negative (0) calibration label and removes the
    same_person_probability relationship so the pair stops surfacing.
    """
    url = f"{_api_base_url()}/api/entities/dismiss-match"
    payload = {"entity_a": id_a, "entity_b": id_b}
    return await anyio.to_thread.run_sync(_http_post_sync, url, payload)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

def _is_authorized(user_id: str) -> bool:
    """Authorized-user gate for the one command center. Empty allowlist locks
    everything down (reject + self-discovery), matching the collector bot."""
    raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
    if not raw:
        return False
    allowed = {x.strip() for x in raw.split(",") if x.strip()}
    return str(user_id) in allowed


async def _handle_callback(cq: dict) -> None:
    """Dispatch a single Telegram callback_query."""
    global _resolved

    cq_id = cq.get("id", "")
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")

    # Authorized-user gating: only allow-listed Telegram ids may act on cards.
    from_id = str((cq.get("from") or {}).get("id", ""))
    if not _is_authorized(from_id):
        allow = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        reject = (
            f"Not authorized. Your Telegram id is {from_id} \u2014 ask admin to add it "
            "to TELEGRAM_ALLOWED_USER_IDS." if not allow else f"Not authorized (id {from_id})."
        )
        await anyio.to_thread.run_sync(telegram.answer_callback_query, cq_id, reject)
        return

    parsed = parse_callback_data(data)
    if parsed is None:
        # Not a merge-bot callback — acknowledge and ignore
        await anyio.to_thread.run_sync(telegram.answer_callback_query, cq_id, "")
        return

    decision, token = parsed

    # Double-tap guard
    if token in _resolved:
        await anyio.to_thread.run_sync(telegram.answer_callback_query, cq_id, "Already resolved ✓")
        return

    if token in _in_flight:
        await anyio.to_thread.run_sync(telegram.answer_callback_query, cq_id, "Already processing — please wait")
        return

    pair = _lookup_pair(token)
    if pair is None:
        # Token not in store: scheduler was restarted; card is stale
        await anyio.to_thread.run_sync(
            telegram.answer_callback_query, cq_id, "Stale card — restart cleared the queue"
        )
        return

    id_a, id_b = pair

    # Reserve before the first await so concurrent presses cannot apply twice.
    _in_flight.add(token)
    try:
        spinner_text = "Merging…" if decision == "y" else "Marking not same…"
        await anyio.to_thread.run_sync(telegram.answer_callback_query, cq_id, spinner_text)
        result = await (_apply_merge(id_a, id_b) if decision == "y" else _apply_dismiss(id_a, id_b))
        if not result.get("ok"):
            logger.warning("merge-bot: decision failed for token=%s", token)
            if chat_id and message_id:
                error = html.escape(str(result.get("error", "unknown"))[:200])
                await anyio.to_thread.run_sync(
                    telegram.reply_message_sync, chat_id,
                    f"❌ Decision could not be confirmed: {error}. Please retry or check the dashboard.",
                    message_id,
                )
            return

        _resolved.add(token)
        new_text = ("✅ <b>Merged</b> — decision recorded." if decision == "y"
                    else "🚫 <b>Marked not same person</b> — decision recorded.")
        if chat_id and message_id:
            await anyio.to_thread.run_sync(telegram.edit_message_text, chat_id, message_id, new_text)
            try:
                await anyio.to_thread.run_sync(telegram.unpin_chat_message, chat_id, message_id)
            except Exception:
                logger.debug("merge-bot: unpin failed (non-fatal)", exc_info=True)
    finally:
        _in_flight.discard(token)


# ---------------------------------------------------------------------------
# Command-center: message handler (/whois, /digest)
# ---------------------------------------------------------------------------


async def _handle_message(msg: dict) -> None:
    """Dispatch a Telegram text message arriving from an authorized operator.

    Supported commands:
      /whois <handle-or-name>  — entity lookup card
      /digest                  — on-demand identity digest

    All sends use telegram.reply_message_sync (→ asyncio.to_thread).
    Un-authorized senders get a brief rejection; all other text is ignored.
    """
    from_id = str((msg.get("from") or {}).get("id", ""))
    chat_id = (msg.get("chat") or {}).get("id")
    message_id = msg.get("message_id")
    text = (msg.get("text") or "").strip()

    if not text.startswith("/"):
        return  # not a command — ignore silently

    # Gate: only allow-listed Telegram ids may use command center
    if not _is_authorized(from_id):
        raw = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "").strip()
        reject = (
            f"\u26d4 Not authorized. Your Telegram id is <code>{html.escape(from_id)}</code>"
            " — ask admin to add it to TELEGRAM_ALLOWED_USER_IDS."
            if not raw
            else f"\u26d4 Not authorized (id {html.escape(from_id)})."
        )
        if chat_id and message_id:
            await asyncio.to_thread(
                telegram.reply_message_sync, chat_id, reject, message_id
            )
        return

    # Parse command (strip @botname suffix Telegram appends in group chats)
    cmd_parts = text.split(None, 1)
    cmd = cmd_parts[0].split("@")[0].lower()
    arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ""

    if cmd == "/whois":
        if not arg:
            reply = "Usage: <code>/whois &lt;handle or name&gt;</code>"
        else:
            logger.info("merge-bot: /whois %r from %s", arg, from_id)
            try:
                reply = await _whois_lookup(arg)
            except Exception as exc:
                logger.exception("merge-bot: /whois lookup failed for %r", arg)
                reply = f"\u274c Lookup error: <code>{html.escape(str(exc)[:200])}</code>"
        if chat_id and message_id:
            await asyncio.to_thread(
                telegram.reply_message_sync, chat_id, reply, message_id
            )

    elif cmd == "/digest":
        logger.info("merge-bot: /digest requested by %s", from_id)
        try:
            from src.notifications.alerts import build_identity_digest, notify_identity_digest
            d = await build_identity_digest()
            ok = await notify_identity_digest(d)
            if not ok and chat_id and message_id:
                await asyncio.to_thread(
                    telegram.reply_message_sync,
                    chat_id,
                    "\u274c Digest send failed (check scheduler logs).",
                    message_id,
                )
        except Exception as exc:
            logger.exception("merge-bot: /digest failed")
            if chat_id and message_id:
                await asyncio.to_thread(
                    telegram.reply_message_sync,
                    chat_id,
                    f"\u274c Digest error: <code>{html.escape(str(exc)[:200])}</code>",
                    message_id,
                )
    # Any other /command is silently ignored
