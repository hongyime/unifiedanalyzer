"""Unit tests for the Telegram merge-review bot (src/notifications/merge_bot.py).

Covers:
  - parse_callback_data: valid/invalid formats
  - callback_data size budget (≤ 64 bytes)
  - _handle_callback "y" → _apply_merge, marks resolved, edits message
  - _handle_callback "n" → _apply_dismiss, marks resolved, edits message
  - Double-tap idempotency (_resolved guard)
  - Stale-token no-op (scheduler restart scenario)
  - Offset advancement in the polling loop
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _fresh():
    """Reload merge_bot to get a clean module with reset globals."""
    import src.notifications.merge_bot as mb
    importlib.reload(mb)
    return mb


def _cq(callback_data: str, cq_id: str = "cqid123") -> dict:
    return {
        "id": cq_id,
        "data": callback_data,
        "from": {"id": 12345},  # authorized test user (see setenv in each test)
        "message": {"message_id": 999, "chat": {"id": -100123}},
    }


# ---------------------------------------------------------------------------
# parse_callback_data — pure logic
# ---------------------------------------------------------------------------


def test_parse_yes_returns_decision_and_token():
    mb = _fresh()
    result = mb.parse_callback_data("mrg:y:abcd1234")
    assert result == ("y", "abcd1234")


def test_parse_no_returns_decision_and_token():
    mb = _fresh()
    result = mb.parse_callback_data("mrg:n:deadbeef")
    assert result == ("n", "deadbeef")


def test_parse_wrong_prefix_returns_none():
    mb = _fresh()
    assert mb.parse_callback_data("foo:y:abcd1234") is None


def test_parse_unknown_decision_char_returns_none():
    mb = _fresh()
    assert mb.parse_callback_data("mrg:x:abcd1234") is None


def test_parse_too_few_parts_returns_none():
    mb = _fresh()
    assert mb.parse_callback_data("mrg:y") is None


def test_parse_too_many_parts_returns_none():
    mb = _fresh()
    # Extra colon → 4 parts → None
    assert mb.parse_callback_data("mrg:y:tok:extra") is None


# ---------------------------------------------------------------------------
# callback_data size budget (Telegram hard limit: 64 bytes)
# ---------------------------------------------------------------------------


def test_callback_data_fits_within_64_bytes():
    mb = _fresh()
    yes = mb.callback_data_yes(ID_A, ID_B)
    no = mb.callback_data_no(ID_A, ID_B)
    assert len(yes.encode()) <= 64, f"callback_data_yes = {len(yes.encode())} bytes"
    assert len(no.encode()) <= 64, f"callback_data_no = {len(no.encode())} bytes"


def test_callback_data_yes_prefix():
    mb = _fresh()
    assert mb.callback_data_yes(ID_A, ID_B).startswith("mrg:y:")


def test_callback_data_no_prefix():
    mb = _fresh()
    assert mb.callback_data_no(ID_A, ID_B).startswith("mrg:n:")


def test_callback_data_token_is_deterministic():
    """Same pair always produces the same token regardless of order."""
    mb = _fresh()
    t1 = mb.callback_data_yes(ID_A, ID_B)
    t2 = mb.callback_data_yes(ID_B, ID_A)
    assert t1 == t2


# ---------------------------------------------------------------------------
# _handle_callback "y" → merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_callback_yes_dispatches_merge(monkeypatch):
    mb = _fresh()

    token = mb._make_token(ID_A, ID_B)  # register pair in store
    cb_data = f"mrg:y:{token}"

    merge_calls: list[tuple] = []
    dismiss_calls: list[tuple] = []
    answer_calls: list[str] = []
    edit_texts: list[str] = []

    async def fake_merge(id_a, id_b):
        merge_calls.append((id_a, id_b))
        return {"ok": True}

    async def fake_dismiss(id_a, id_b):
        dismiss_calls.append((id_a, id_b))
        return {"ok": True}

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
    monkeypatch.setattr(mb, "_apply_merge", fake_merge)
    monkeypatch.setattr(mb, "_apply_dismiss", fake_dismiss)
    monkeypatch.setattr(mb.telegram, "answer_callback_query",
                        lambda cq_id, text="": answer_calls.append(text) or {"ok": True})
    monkeypatch.setattr(mb.telegram, "edit_message_text",
                        lambda chat, msg, text: edit_texts.append(text) or {"ok": True})

    await mb._handle_callback(_cq(cb_data))

    assert merge_calls == [(ID_A, ID_B)], "merge must be called once with correct IDs"
    assert dismiss_calls == [], "dismiss must NOT be called for 'y'"
    assert token in mb._resolved, "token must be marked resolved on success"
    assert any("erg" in t for t in answer_calls), "spinner ack must mention merging"
    assert edit_texts and "Merged" in edit_texts[0]


# ---------------------------------------------------------------------------
# _handle_callback "n" → dismiss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_callback_no_dispatches_dismiss(monkeypatch):
    mb = _fresh()

    token = mb._make_token(ID_A, ID_B)
    cb_data = f"mrg:n:{token}"

    merge_calls: list = []
    dismiss_calls: list = []
    edit_texts: list[str] = []

    async def fake_merge(id_a, id_b):
        merge_calls.append((id_a, id_b))
        return {"ok": True}

    async def fake_dismiss(id_a, id_b):
        dismiss_calls.append((id_a, id_b))
        return {"ok": True}

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
    monkeypatch.setattr(mb, "_apply_merge", fake_merge)
    monkeypatch.setattr(mb, "_apply_dismiss", fake_dismiss)
    monkeypatch.setattr(mb.telegram, "answer_callback_query", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(mb.telegram, "edit_message_text",
                        lambda chat, msg, text: edit_texts.append(text) or {"ok": True})

    await mb._handle_callback(_cq(cb_data))

    assert dismiss_calls == [(ID_A, ID_B)], "dismiss must be called once with correct IDs"
    assert merge_calls == [], "merge must NOT be called for 'n'"
    assert token in mb._resolved
    assert edit_texts and "not same" in edit_texts[0].lower()


# ---------------------------------------------------------------------------
# Double-tap idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_callback_double_tap_is_noop(monkeypatch):
    mb = _fresh()

    token = mb._make_token(ID_A, ID_B)
    mb._resolved.add(token)   # pre-mark as already resolved

    merge_calls: list = []
    answer_calls: list[str] = []

    async def fake_merge(id_a, id_b):
        merge_calls.append((id_a, id_b))
        return {"ok": True}

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
    monkeypatch.setattr(mb, "_apply_merge", fake_merge)
    monkeypatch.setattr(mb, "_apply_dismiss", fake_merge)  # same sentinel
    monkeypatch.setattr(mb.telegram, "answer_callback_query",
                        lambda cq_id, text="": answer_calls.append(text) or {"ok": True})
    monkeypatch.setattr(mb.telegram, "edit_message_text", lambda *a: {"ok": True})

    await mb._handle_callback(_cq(f"mrg:y:{token}"))

    assert merge_calls == [], "merge must NOT fire for already-resolved token"
    assert any("already" in t.lower() or "resolved" in t.lower() for t in answer_calls), (
        f"answer must mention 'already resolved'; got {answer_calls!r}"
    )


# ---------------------------------------------------------------------------
# Stale token (scheduler restart wipes _pair_store)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_callback_stale_token_is_noop(monkeypatch):
    mb = _fresh()
    # NOTE: we do NOT call _make_token, so the token is NOT in _pair_store

    unknown_token = "deadbeef"
    answer_calls: list[str] = []

    async def should_not_call(*args):
        pytest.fail("merge/dismiss must not be called for a stale token")

    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "12345")
    monkeypatch.setattr(mb, "_apply_merge", should_not_call)
    monkeypatch.setattr(mb, "_apply_dismiss", should_not_call)
    monkeypatch.setattr(mb.telegram, "answer_callback_query",
                        lambda cq_id, text="": answer_calls.append(text) or {"ok": True})
    monkeypatch.setattr(mb.telegram, "edit_message_text", lambda *a: {"ok": True})

    await mb._handle_callback(_cq(f"mrg:y:{unknown_token}"))

    assert any("stale" in t.lower() or "restart" in t.lower() for t in answer_calls), (
        f"answer must mention stale/restart; got {answer_calls!r}"
    )


# ---------------------------------------------------------------------------
# Offset advances in the polling loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poller_advances_offset_for_each_update(monkeypatch):
    """_offset must be incremented to update_id + 1 after each processed update."""
    mb = _fresh()
    mb._offset = 0

    # Pre-register the pair so the handler runs successfully
    token = mb._make_token(ID_A, ID_B)

    call_count = [0]

    def fake_get_updates(offset, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "ok": True,
                "result": [
                    {
                        "update_id": 500,
                        "callback_query": {
                            "id": "cq1",
                            "data": f"mrg:y:{token}",
                            "message": {"message_id": 1, "chat": {"id": -100}},
                        },
                    }
                ],
            }
        # Second call: raise CancelledError to stop the loop cleanly
        raise asyncio.CancelledError("test_done")

    monkeypatch.setattr(mb.telegram, "get_webhook_info",
                        lambda: {"ok": True, "result": {"url": ""}})
    monkeypatch.setattr(mb.telegram, "get_updates", fake_get_updates)
    monkeypatch.setattr(mb.telegram, "answer_callback_query", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(mb.telegram, "edit_message_text", lambda *a: {"ok": True})

    async def fake_merge(id_a, id_b):
        return {"ok": True}

    monkeypatch.setattr(mb, "_apply_merge", fake_merge)

    # run_callback_poller returns when it catches CancelledError
    await mb.run_callback_poller()

    assert mb._offset == 501, f"Expected _offset=501 after update_id=500; got {mb._offset}"
    assert call_count[0] == 2, "Second get_updates call should have raised CancelledError"
