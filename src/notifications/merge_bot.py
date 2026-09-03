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
import html
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
# Command-center: /whois lookup
# ---------------------------------------------------------------------------


async def _whois_lookup(query: str) -> str:
    """Resolve *query* to an entity (ILIKE match on name or platform handle).

    Returns a compact HTML summary card, or a 'no entity' notice.
    Enrichment from the collector DB is best-effort (never raises).
    """
    from src.db.connection import get_analyzer_pool, get_collector_pool

    like = f"%{query.strip()}%"
    pool = get_analyzer_pool()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT e.id::text, e.canonical_name, e.tier, e.confidence_score,
                   e.signal_count, e.last_seen_at
            FROM entities e
            WHERE e.canonical_name ILIKE $1
               OR EXISTS (
                   SELECT 1 FROM entity_platform_links epl
                   WHERE epl.entity_id = e.id
                     AND epl.platform_username ILIKE $1
               )
            ORDER BY e.confidence_score DESC NULLS LAST
            LIMIT 1
            """,
            like,
        )

        if not row:
            return f"\u2753 No entity found for <code>{html.escape(query[:80])}</code>"

        eid = row["id"]

        links = await conn.fetch(
            """
            SELECT source, platform_id, platform_username,
                   COALESCE(NULLIF(platform_username,''),
                            NULLIF(platform_name,''),
                            platform_id) AS handle
            FROM entity_platform_links
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC
            """,
            eid,
        )

        sigs = await conn.fetch(
            """
            SELECT signal_type, confidence
            FROM identity_signals
            WHERE entity_id = $1::uuid
            ORDER BY confidence DESC NULLS LAST
            LIMIT 5
            """,
            eid,
        )

    # Enrichment: collector DB (all best-effort, never fatal)
    wa_jids = [
        lnk["platform_id"] for lnk in links
        if lnk["source"] == "whatsapp" and lnk["platform_id"]
    ]
    usernames = list({
        lnk["platform_username"] for lnk in links
        if lnk["platform_username"]
    })
    emails = [u for u in usernames if "@" in u]

    gaia_line = ""
    disc_count = 0
    phone_info = ""
    wa_device_count = 0

    try:
        cpool = get_collector_pool()
        async with cpool.acquire() as cconn:
            if emails:
                grow = await cconn.fetchrow(
                    "SELECT rt.target_value AS email, ro.value "
                    "FROM recon_observations ro "
                    "JOIN recon_targets rt ON rt.id = ro.target_id "
                    "WHERE ro.module = 'ghunt' AND ro.observation_type = 'GAIA_ID' "
                    "  AND rt.target_value = ANY($1::text[]) LIMIT 1",
                    emails,
                )
                if grow:
                    gaia_line = (
                        f"{html.escape(grow['email'])} "
                        f"GAIA:{html.escape(str(grow['value'] or ''))[:20]}"
                    )
            if usernames:
                disc_count = int(await cconn.fetchval(
                    "SELECT COUNT(DISTINCT ro.value) FROM recon_observations ro "
                    "JOIN recon_targets rt ON rt.id = ro.target_id "
                    "WHERE ro.module = 'maigret' "
                    "  AND ro.observation_type = 'ACCOUNT_EXTERNAL_OWNED' "
                    "  AND rt.target_value = ANY($1::text[])",
                    usernames,
                ) or 0)
            if wa_jids:
                prow = await cconn.fetchrow(
                    "SELECT carrier, region_name FROM wa_phone_intel "
                    "WHERE phone_jid = ANY($1::text[]) LIMIT 1",
                    wa_jids,
                )
                if prow:
                    phone_info = ", ".join(
                        v for v in [prow["region_name"], prow["carrier"]] if v
                    )
                drow = await cconn.fetchrow(
                    "SELECT SUM(COALESCE(device_count, 0)) AS total "
                    "FROM wa_device_observations "
                    "WHERE phone_jid = ANY($1::text[])",
                    wa_jids,
                )
                if drow and drow["total"]:
                    wa_device_count = int(drow["total"])
    except Exception:
        pass  # enrichment is always best-effort

    # Format output
    base_url = telegram.get_dashboard_url()
    name = html.escape(row["canonical_name"] or "Unknown")
    tier = row["tier"] or "?"
    conf = (
        f"{float(row['confidence_score']):.0%}"
        if row["confidence_score"] is not None else "?"
    )
    handle_parts = [
        f"{html.escape(lnk['source'])}:{html.escape(lnk['handle'])}"
        for lnk in links[:8]
        if lnk["handle"]
    ]
    sig_parts = [
        f"{s['signal_type'].replace('_', ' ')} ({float(s['confidence']):.0%})"
        if s["confidence"] is not None
        else s["signal_type"].replace("_", " ")
        for s in sigs
    ]

    lines = [f"\U0001f464 <b>{name}</b> [{tier}] conf {conf}"]
    if handle_parts:
        lines.append(f"Handles: {' \u00b7 '.join(handle_parts)}")
    if sig_parts:
        lines.append(f"Signals: {'; '.join(sig_parts)}")
    if gaia_line:
        lines.append(f"Google: {gaia_line}")
    if disc_count:
        lines.append(f"Discovered accounts: {disc_count}")
    if phone_info:
        lines.append(f"Phone: {phone_info}")
    if wa_device_count:
        lines.append(f"WA devices: {wa_device_count}")
    lines.append(f"{base_url}/entities/{eid}")
    return "\n".join(lines)


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
                msg_upd = upd.get("message")
                if cq:
                    try:
                        await _handle_callback(cq)
                    except Exception:
                        logger.exception(
                            "merge-bot: error handling callback update_id=%s",
                            upd.get("update_id"),
                        )
                elif msg_upd:
                    try:
                        await _handle_message(msg_upd)
                    except Exception:
                        logger.exception(
                            "merge-bot: error handling message update_id=%s",
                            upd.get("update_id"),
                        )

        except asyncio.CancelledError:
            logger.info("merge-bot: poller cancelled")
            return
        except Exception:
            logger.exception("merge-bot: getUpdates loop error; backing off 30s")
            await asyncio.sleep(30)
