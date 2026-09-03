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

The poller runs as a single asyncio.Task inside the existing scheduler loop
(NOT a new process, NOT a new container).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request

from src.notifications import telegram

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token store — in-memory (does not survive scheduler restart; noted in docs)
# ---------------------------------------------------------------------------

# token (8 hex chars) → (entity_a_id, entity_b_id)
_pair_store: dict[str, tuple[str, str]] = {}

# Tokens whose pair has already been acted on (double-tap guard)
_resolved: set[str] = set()

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


def _http_post_sync(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
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
    url = f"http://127.0.0.1:{_api_port()}/api/entities/merge"
    payload = {"source_entity_ids": [id_a, id_b], "reason": "telegram_merge_bot"}
    return await asyncio.to_thread(_http_post_sync, url, payload)


async def _apply_dismiss(id_a: str, id_b: str) -> dict:
    """Call POST /api/entities/dismiss-match (entity_actions.dismiss_match).

    The dashboard uses this endpoint for the ❌ 'Not same person' action; it
    records a negative (0) calibration label and removes the
    same_person_probability relationship so the pair stops surfacing.
    """
    url = f"http://127.0.0.1:{_api_port()}/api/entities/dismiss-match"
    payload = {"entity_a": id_a, "entity_b": id_b}
    return await asyncio.to_thread(_http_post_sync, url, payload)


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
        await asyncio.to_thread(telegram.answer_callback_query, cq_id, reject)
        return

    parsed = parse_callback_data(data)
    if parsed is None:
        # Not a merge-bot callback — acknowledge and ignore
        await asyncio.to_thread(telegram.answer_callback_query, cq_id, "")
        return

    decision, token = parsed

    # Double-tap guard
    if token in _resolved:
        await asyncio.to_thread(telegram.answer_callback_query, cq_id, "Already resolved ✓")
        return

    pair = _lookup_pair(token)
    if pair is None:
        # Token not in store: scheduler was restarted; card is stale
        await asyncio.to_thread(
            telegram.answer_callback_query, cq_id, "Stale card — restart cleared the queue"
        )
        return

    id_a, id_b = pair

    # Acknowledge immediately to clear the Telegram spinner before the HTTP call
    spinner_text = "Merging…" if decision == "y" else "Marking not same…"
    await asyncio.to_thread(telegram.answer_callback_query, cq_id, spinner_text)

    if decision == "y":
        result = await _apply_merge(id_a, id_b)
        if result.get("ok"):
            _resolved.add(token)
            new_text = "✅ <b>Merged</b> — decision recorded."
            logger.info("merge-bot: merged %s + %s", id_a, id_b)
        else:
            new_text = f"❌ Merge failed: {str(result.get('error', 'unknown'))[:200]}"
            logger.warning("merge-bot: merge failed for %s/%s: %s", id_a, id_b, result)
    else:
        result = await _apply_dismiss(id_a, id_b)
        if result.get("ok"):
            _resolved.add(token)
            new_text = "🚫 <b>Marked not same person</b> — decision recorded."
            logger.info("merge-bot: dismissed %s / %s", id_a, id_b)
        else:
            new_text = f"❌ Dismiss failed: {str(result.get('error', 'unknown'))[:200]}"
            logger.warning("merge-bot: dismiss failed for %s/%s: %s", id_a, id_b, result)

    if chat_id and message_id:
        await asyncio.to_thread(telegram.edit_message_text, chat_id, message_id, new_text)
        # Decision made — unpin so the pinned queue only holds OPEN candidates.
        try:
            await asyncio.to_thread(telegram.unpin_chat_message, chat_id, message_id)
        except Exception:
            logger.debug("merge-bot: unpin failed (non-fatal)", exc_info=True)


# ---------------------------------------------------------------------------
# Long-poll loop (asyncio Task)
# ---------------------------------------------------------------------------

async def run_callback_poller() -> None:
    """Long-poll Telegram getUpdates and dispatch merge-review callbacks.

    Designed to run as a single asyncio.Task inside start_scheduler().
    On startup it checks/removes any existing webhook (getUpdates fails if a
    webhook is set), then polls in a tight loop with 25-second long-poll
    timeouts.  On network / API errors it backs off 30 s.

    Only ONE instance of this coroutine should ever be running per bot token;
    start_scheduler() creates it exactly once.
    """
    global _offset

    logger.info("merge-bot: callback poller starting (long-poll mode)")

    # Webhook check — getUpdates and webhooks are mutually exclusive
    try:
        info = await asyncio.to_thread(telegram.get_webhook_info)
        webhook_url = (info.get("result") or {}).get("url", "")
        if webhook_url:
            logger.warning(
                "merge-bot: webhook is set to %r — deleting it to enable long-polling",
                webhook_url,
            )
            await asyncio.to_thread(telegram.delete_webhook)
            logger.info("merge-bot: webhook deleted")
        else:
            logger.info("merge-bot: no webhook set, long-polling ready")
    except Exception:
        logger.exception("merge-bot: webhook check failed (non-fatal, continuing)")

    while True:
        try:
            updates = await asyncio.to_thread(telegram.get_updates, _offset, 25)

            if not updates.get("ok"):
                logger.debug("merge-bot: getUpdates not ok: %s", updates)
                await asyncio.sleep(5)
                continue

            for upd in updates.get("result") or []:
                # Advance offset FIRST so a crash during handling doesn't re-deliver
                _offset = upd["update_id"] + 1
                cq = upd.get("callback_query")
                if cq:
                    try:
                        await _handle_callback(cq)
                    except Exception:
                        logger.exception(
                            "merge-bot: error handling callback update_id=%s",
                            upd.get("update_id"),
                        )

        except asyncio.CancelledError:
            logger.info("merge-bot: poller cancelled")
            return
        except Exception:
            logger.exception("merge-bot: getUpdates loop error; backing off 30s")
            await asyncio.sleep(30)
