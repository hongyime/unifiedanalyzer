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
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

DECISION_LOG_DIR = Path(os.getenv("ANALYZER_DECISION_LOG_DIR", "Z:/unifiedanalyzer/decisions"))
DECISION_EVENT_SCHEMA_VERSION = 1
_DECISION_EVENT_REQUIRED_FIELDS = {
    "schema_version",
    "audit_id",
    "event_type",
    "actor",
    "entity_ids",
    "payload",
    "created_at",
    "prev_sha256",
    "sha256",
    "idempotency_key",
}
_DECISION_EVENT_OPTIONAL_FIELDS = {
    "stable_refs",
    "evidence_snapshot",
}
_DECISION_EVENT_FIELDS = _DECISION_EVENT_REQUIRED_FIELDS | _DECISION_EVENT_OPTIONAL_FIELDS


def _canonical_json(obj) -> str:
    """Deterministic JSON encoding so identical payloads hash identically
    across runs (dict key order, whitespace stable)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _append_jsonl(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _canonical_json(event) + "\n"
    # Write-through append. A temp+replace pattern is wrong for JSONL appenders;
    # keep each line single-write and flush it so a crash leaves either a full
    # event line or nothing.
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(c in "0123456789abcdef" for c in value)
    )


def _validate_decision_event(event: dict) -> None:
    missing = _DECISION_EVENT_REQUIRED_FIELDS - event.keys()
    if missing:
        raise ValueError(f"decision event missing fields: {sorted(missing)}")

    unexpected = event.keys() - _DECISION_EVENT_FIELDS
    if unexpected:
        raise ValueError(f"decision event has unexpected fields: {sorted(unexpected)}")

    if event["schema_version"] != DECISION_EVENT_SCHEMA_VERSION:
        raise ValueError("decision event schema_version must be 1")

    audit_id = event["audit_id"]
    if isinstance(audit_id, bool) or not isinstance(audit_id, int) or audit_id <= 0:
        raise ValueError("decision event audit_id must be a positive integer")

    event_type = event["event_type"]
    if not isinstance(event_type, str) or not event_type:
        raise ValueError("decision event event_type must be a non-empty string")

    actor = event["actor"]
    if actor is not None and not isinstance(actor, str):
        raise ValueError("decision event actor must be a string or null")

    entity_ids = event["entity_ids"]
    if not isinstance(entity_ids, list) or not all(isinstance(x, str) and x for x in entity_ids):
        raise ValueError("decision event entity_ids must be a list of non-empty strings")

    if not isinstance(event["payload"], dict):
        raise ValueError("decision event payload must be an object")

    created_at = event["created_at"]
    if not isinstance(created_at, str):
        raise ValueError("decision event created_at must be an ISO timestamp string")
    try:
        datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError("decision event created_at must be an ISO timestamp string") from exc

    prev_sha256 = event["prev_sha256"]
    if prev_sha256 is not None and not _is_sha256_hex(prev_sha256):
        raise ValueError("decision event prev_sha256 must be null or a sha256 hex string")

    if not _is_sha256_hex(event["sha256"]):
        raise ValueError("decision event sha256 must be a sha256 hex string")

    if not _is_sha256_hex(event["idempotency_key"]):
        raise ValueError("decision event idempotency_key must be a sha256 hex string")

    if "stable_refs" in event:
        stable_refs = event["stable_refs"]
        if not isinstance(stable_refs, list):
            raise ValueError("decision event stable_refs must be a list")
        for ref in stable_refs:
            if not isinstance(ref, dict):
                raise ValueError("decision event stable_refs entries must be objects")
            if not isinstance(ref.get("source"), str) or not ref.get("source"):
                raise ValueError("decision event stable_refs entries require source")
            if ref.get("platform_id") is not None and not isinstance(ref.get("platform_id"), str):
                raise ValueError("decision event stable_refs platform_id must be string or null")
            if ref.get("platform_username") is not None and not isinstance(ref.get("platform_username"), str):
                raise ValueError("decision event stable_refs platform_username must be string or null")
            if ref.get("media_sha256") is not None and not isinstance(ref.get("media_sha256"), str):
                raise ValueError("decision event stable_refs media_sha256 must be string or null")
            if ref.get("sidecar_path") is not None and not isinstance(ref.get("sidecar_path"), str):
                raise ValueError("decision event stable_refs sidecar_path must be string or null")

    if "evidence_snapshot" in event and not isinstance(event["evidence_snapshot"], dict):
        raise ValueError("decision event evidence_snapshot must be an object")


def _decision_log_path(created_at) -> Path:
    return DECISION_LOG_DIR / f"{created_at:%Y-%m}.jsonl"


def _decision_event_idempotency_key(
    *,
    action: str,
    actor: str | None,
    entity_ids: list[str] | None,
    payload: dict,
    created_at,
) -> str:
    return hashlib.sha256(
        _canonical_json({
            "action": action,
            "actor": actor,
            "entity_ids": sorted(entity_ids or []),
            "payload": payload or {},
            "created_at": created_at.isoformat(timespec="microseconds"),
        }).encode("utf-8")
    ).hexdigest()


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip().lower()
    if _is_sha256_hex(stripped):
        return stripped
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()


def _decision_event(*, audit_id: int, prev_sha256: str | None,
                    sha256: str, action: str, actor: str | None,
                    entity_ids: list[str] | None, payload: dict,
                    created_at, idempotency_key: str | None = None) -> dict:
    event_key = idempotency_key or _decision_event_idempotency_key(
        action=action,
        actor=actor,
        entity_ids=entity_ids,
        payload=payload,
        created_at=created_at,
    )
    event = {
        "schema_version": DECISION_EVENT_SCHEMA_VERSION,
        "audit_id": audit_id,
        "event_type": action,
        "actor": actor,
        "entity_ids": entity_ids or [],
        "payload": payload or {},
        "created_at": created_at.isoformat(timespec="microseconds"),
        "prev_sha256": prev_sha256,
        "sha256": sha256,
        "idempotency_key": event_key,
        "stable_refs": _stable_refs_from_payload(payload or {}),
        "evidence_snapshot": _evidence_snapshot_from_payload(payload or {}),
    }
    _validate_decision_event(event)
    return event


def _stable_refs_from_payload(payload: dict) -> list[dict[str, str | None]]:
    refs: list[dict[str, str | None]] = []
    for snapshot in _walk_entity_snapshots(payload):
        for link in snapshot.get("platform_links") or []:
            source = _clean_str(link.get("source"))
            platform_id = _clean_str(link.get("platform_id"))
            username = _clean_str(link.get("platform_username"))
            if not source or not (platform_id or username):
                continue
            ref = {
                "source": source,
                "platform_id": platform_id,
                "platform_username": username,
                "media_sha256": None,
                "sidecar_path": None,
            }
            if ref not in refs:
                refs.append(ref)

    media_ref = payload.get("media_ref") if isinstance(payload, dict) else None
    if isinstance(media_ref, dict):
        source = _clean_str(media_ref.get("source"))
        content_id = _clean_str(media_ref.get("content_id") or media_ref.get("platform_media_id"))
        media_sha = _clean_str(media_ref.get("sha256") or media_ref.get("media_sha256"))
        sidecar_path = _clean_str(media_ref.get("sidecar_path") or media_ref.get("vault_sidecar"))
        if source and (content_id or media_sha or sidecar_path):
            ref = {
                "source": source,
                "platform_id": content_id,
                "platform_username": None,
                "media_sha256": media_sha,
                "sidecar_path": sidecar_path,
            }
            if ref not in refs:
                refs.append(ref)
    return refs


def _evidence_snapshot_from_payload(payload: dict) -> dict:
    snapshot: dict = {}
    for key in (
        "confidence",
        "evidence_refs",
        "candidate_evidence",
        "relationship_snapshot",
        "location_ref",
        "media_ref",
        "platform_link_snapshot",
        "watch_status",
        "previous_watch_status",
        "reason",
        "notes",
    ):
        if key in payload:
            snapshot[key] = payload[key]
    return snapshot


def _walk_entity_snapshots(value):
    if isinstance(value, dict):
        if isinstance(value.get("platform_links"), list):
            yield value
        for child in value.values():
            yield from _walk_entity_snapshots(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_entity_snapshots(child)


def _clean_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _write_decision_event(*, audit_id: int, prev_sha256: str | None,
                          sha256: str, action: str, actor: str | None,
                          entity_ids: list[str] | None, payload: dict,
                          created_at, idempotency_key: str | None = None) -> Path:
    event = _decision_event(
        audit_id=audit_id,
        prev_sha256=prev_sha256,
        sha256=sha256,
        action=action,
        actor=actor,
        entity_ids=entity_ids,
        payload=payload,
        created_at=created_at,
        idempotency_key=idempotency_key,
    )
    path = _decision_log_path(created_at)
    _append_jsonl(path, event)
    return path


def _jsonl_contains_event(path: Path, *, audit_id: int, idempotency_key: str | None) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("audit_id") == audit_id:
                    return True
                if idempotency_key and event.get("idempotency_key") == idempotency_key:
                    return True
    except Exception:
        return False
    return False


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


def _row_value(row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key, default)
        return default


async def _mark_decision_jsonl_written(conn, audit_id: int, path: Path) -> None:
    try:
        await conn.execute(
            """
            UPDATE audit_log
            SET decision_jsonl_path = $2,
                decision_jsonl_written_at = NOW(),
                decision_jsonl_error = NULL
            WHERE id = $1
            """,
            audit_id,
            str(path),
        )
    except Exception:
        logger.exception("decision JSONL status update failed: audit_id=%s", audit_id)


async def _mark_decision_jsonl_error(conn, audit_id: int, error: str) -> None:
    try:
        await conn.execute(
            """
            UPDATE audit_log
            SET decision_jsonl_error = $2
            WHERE id = $1
            """,
            audit_id,
            error[:1000],
        )
    except Exception:
        logger.exception("decision JSONL error status update failed: audit_id=%s", audit_id)


async def append_audit(conn, *, action: str, actor: str | None = None,
                       entity_ids: Iterable[str] | None = None,
                       payload: dict | None = None,
                       idempotency_key: str | None = None) -> int:
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
        event_key = _normalize_idempotency_key(idempotency_key) or _decision_event_idempotency_key(
            action=action,
            actor=actor,
            entity_ids=entity_id_list,
            payload=payload,
            created_at=created_at,
        )

        row = await conn.fetchrow("""
            INSERT INTO audit_log (prev_sha256, sha256, action, actor,
                                    entity_ids, payload, idempotency_key, created_at)
            VALUES ($1, $2, $3, $4, $5::uuid[], $6::jsonb, $7, $8)
            ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL
            DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
            RETURNING id, created_at, prev_sha256, sha256, decision_jsonl_written_at
        """, prev_sha, sha, action, actor, entity_id_list,
             json.dumps(payload, default=str), event_key, created_at)
        audit_id = int(row["id"])
        if _row_value(row, "decision_jsonl_written_at") is not None:
            return audit_id
        try:
            path = _write_decision_event(
                audit_id=audit_id,
                prev_sha256=_row_value(row, "prev_sha256", prev_sha),
                sha256=_row_value(row, "sha256", sha),
                action=action,
                actor=actor,
                entity_ids=entity_id_list,
                payload=payload,
                created_at=row["created_at"],
                idempotency_key=event_key,
            )
            await _mark_decision_jsonl_written(conn, audit_id, path)
        except Exception as exc:
            logger.exception("decision JSONL append failed (non-fatal): action=%s", action)
            await _mark_decision_jsonl_error(conn, audit_id, str(exc) or "decision JSONL append failed")
        return audit_id
    except Exception:
        # Non-fatal: never let audit logging break the actual action.
        logger.exception("audit_log append failed (non-fatal): action=%s", action)
        return -1


async def retry_pending_decision_jsonl(conn, *, limit: int = 100) -> dict[str, int]:
    """Retry audit rows whose DB insert succeeded before JSONL append did."""
    rows = await conn.fetch(
        """
        SELECT id, prev_sha256, sha256, action, actor, entity_ids, payload,
               created_at, idempotency_key
        FROM audit_log
        WHERE decision_jsonl_written_at IS NULL
        ORDER BY id ASC
        LIMIT $1
        """,
        limit,
    )
    stats = {"pending": len(rows), "already_present": 0, "written": 0, "failed": 0}
    for row in rows:
        audit_id = int(row["id"])
        entity_id_list = [str(x) for x in (_row_value(row, "entity_ids") or [])] or None
        payload = _row_value(row, "payload") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        try:
            event = _decision_event(
                audit_id=audit_id,
                prev_sha256=_row_value(row, "prev_sha256"),
                sha256=_row_value(row, "sha256"),
                action=_row_value(row, "action"),
                actor=_row_value(row, "actor"),
                entity_ids=entity_id_list,
                payload=payload,
                created_at=_row_value(row, "created_at"),
                idempotency_key=_row_value(row, "idempotency_key"),
            )
            path = _decision_log_path(_row_value(row, "created_at"))
            if _jsonl_contains_event(
                path,
                audit_id=audit_id,
                idempotency_key=event["idempotency_key"],
            ):
                await _mark_decision_jsonl_written(conn, audit_id, path)
                stats["already_present"] += 1
                continue
            _append_jsonl(path, event)
            await _mark_decision_jsonl_written(conn, audit_id, path)
            stats["written"] += 1
        except Exception as exc:
            stats["failed"] += 1
            logger.exception("decision JSONL retry failed: audit_id=%s", audit_id)
            await _mark_decision_jsonl_error(conn, audit_id, str(exc))
    return stats


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
