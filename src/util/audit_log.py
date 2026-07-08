"""Hash-chained audit log for analyst decisions.

Every merge / dismiss / split / watch-status-change writes one row whose
sha256 covers (prev_sha256, action, actor, entity_ids, payload, timestamp).
A gap or hash mismatch on read is detectable evidence of tampering.

Modeled after the FORGE OSINT audit pattern, scoped to entity-graph decisions
so we retain provenance across the UUID churn a full-resolution can cause.

Usage:
    from src.util.audit_log import append_audit
    await append_audit(
        conn, action="merge_entities", actor="dashboard",
        entity_ids=[eid_a, eid_b], payload={"target": target_id, "reason": ""},
    )

Reading the chain:
    from src.util.audit_log import verify_audit_chain
    ok, break_at = await verify_audit_chain(conn)
    # ok=False means a tamper detected; break_at is the first bad row id.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Iterable

logger = logging.getLogger(__name__)


def _canonical_json(obj) -> str:
    """Deterministic JSON encoding so identical payloads hash identically
    across runs (dict key order, whitespace stable)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_sha256: str | None, action: str, actor: str | None,
          entity_ids: list[str] | None, payload: dict, created_at_iso: str) -> str:
    """SHA-256 over the canonical serialization of the row plus the previous
    row's hash. `created_at_iso` MUST match the row's created_at column value
    to the microsecond, else verification fails."""
    h = hashlib.sha256()
    h.update((prev_sha256 or "").encode("utf-8"))
    h.update(b"|")
    h.update(action.encode("utf-8"))
    h.update(b"|")
    h.update((actor or "").encode("utf-8"))
    h.update(b"|")
    # Sort entity_ids for stable order.
    ids = sorted(str(x) for x in (entity_ids or []))
    h.update(_canonical_json(ids).encode("utf-8"))
    h.update(b"|")
    h.update(_canonical_json(payload or {}).encode("utf-8"))
    h.update(b"|")
    h.update(created_at_iso.encode("utf-8"))
    return h.hexdigest()


async def append_audit(conn, *, action: str, actor: str | None = None,
                       entity_ids: Iterable[str] | None = None,
                       payload: dict | None = None) -> int:
    """Append one row. Returns the new row id. Non-fatal on failure - we log
    a warning and swallow, because losing an audit line should never break
    the operator's action itself.

    This performs the (prev_sha256, sha256, ...) computation atomically inside
    a serializable read-uncommitted view of the last row; two concurrent
    appenders could in principle race and both pick the same prev_sha256.
    Idempotent guard: caller should use conn.transaction() when concerns about
    ordering matter (merge/dismiss inherently already do). For our workload
    (dashboard-driven, one operator at a time), the race window is negligible.
    """
    try:
        entity_id_list = [str(x) for x in (entity_ids or [])] or None
        payload = payload or {}

        # Grab the previous row's hash + timestamp.
        prev = await conn.fetchrow(
            "SELECT sha256 FROM audit_log ORDER BY id DESC LIMIT 1"
        )
        prev_sha = prev["sha256"] if prev else None

        # Compute created_at HERE so it's identical to what we hash + insert.
        now_row = await conn.fetchrow("SELECT NOW() AS ts")
        created_at = now_row["ts"]
        # ISO with microseconds - stable format for hashing.
        created_iso = created_at.isoformat(timespec="microseconds")

        sha = _hash(prev_sha, action, actor, entity_id_list, payload, created_iso)

        row = await conn.fetchrow("""
            INSERT INTO audit_log (prev_sha256, sha256, action, actor,
                                    entity_ids, payload, created_at)
            VALUES ($1, $2, $3, $4, $5::uuid[], $6::jsonb, $7)
            RETURNING id
        """, prev_sha, sha, action, actor, entity_id_list,
             json.dumps(payload, default=str), created_at)
        return int(row["id"])
    except Exception:
        # Non-fatal: never let audit logging break the actual action.
        logger.exception("audit_log append failed (non-fatal): action=%s", action)
        return -1


async def verify_audit_chain(conn) -> tuple[bool, int | None]:
    """Verify the entire audit_log chain integrity. Returns (True, None) if
    intact, else (False, first_bad_id). O(rows) - re-hashes each row and
    compares. Use for periodic integrity checks or on-demand.
    """
    prev_sha = None
    async for row in conn.cursor("""
        SELECT id, prev_sha256, sha256, action, actor, entity_ids,
               payload, created_at
        FROM audit_log ORDER BY id ASC
    """):
        expected_prev = prev_sha
        actual_prev = row["prev_sha256"]
        if expected_prev != actual_prev:
            return False, int(row["id"])

        entity_id_list = [str(x) for x in (row["entity_ids"] or [])] or None
        payload = row["payload"] or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        created_iso = row["created_at"].isoformat(timespec="microseconds")

        expected_sha = _hash(expected_prev, row["action"], row["actor"],
                              entity_id_list, payload, created_iso)
        if expected_sha != row["sha256"]:
            return False, int(row["id"])
        prev_sha = row["sha256"]
    return True, None
