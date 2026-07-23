from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from src.util.audit_log import DECISION_LOG_DIR, _validate_decision_event


LEGACY_EVENT_TYPE_ALIASES = {
    "dismiss_match": "dismiss_identity_candidate",
    "merge_entities": "merge_confirmed",
}

DERIVED_REBUILD_BY_EVENT = {
    "merge_confirmed": ("identity_scores", "entity_graph", "timeline_events"),
    "split_person": ("identity_scores", "entity_graph", "timeline_events"),
    "dismiss_identity_candidate": ("identity_scores", "review_candidates"),
    "confirm_relationship": ("entity_graph", "relationship_views"),
    "reject_relationship": ("entity_graph", "relationship_views"),
    "confirm_location": ("location_claims", "map_layers"),
    "reject_location": ("location_claims", "map_layers"),
    "assign_media_owner": ("media_attribution", "timeline_events"),
    "reject_media_owner": ("media_attribution", "timeline_events"),
    "assign_person_in_photo": ("face_links", "timeline_events"),
    "reject_person_in_photo": ("face_links", "timeline_events"),
    "assign_target_tier": ("priority_hints", "watch_views"),
    "add_note": ("person_views",),
    "adjust_source_confidence": ("identity_scores", "relationship_views", "location_claims"),
}

DEFAULT_BACKUP_MAX_AGE_HOURS = 24


class BackupRequiredError(RuntimeError):
    """Raised when replay apply is blocked because no fresh backup is available."""


@dataclass
class DecisionReplayEvent:
    path: str
    line: int
    event_type: str | None
    audit_id: int | None
    status: str
    reason: str | None = None
    resolved_entity_ids: list[str] = field(default_factory=list)
    stable_refs: list[dict[str, str | None]] = field(default_factory=list)
    derived_rebuild_required: list[str] = field(default_factory=list)
    payload_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "event_type": self.event_type,
            "audit_id": self.audit_id,
            "status": self.status,
            "reason": self.reason,
            "resolved_entity_ids": self.resolved_entity_ids,
            "stable_refs": self.stable_refs,
            "derived_rebuild_required": self.derived_rebuild_required,
            "payload_summary": self.payload_summary,
        }


@dataclass
class DecisionReplayReport:
    log_dir: Path
    scanned: int = 0
    restorable: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    invalid: int = 0
    by_event_type: Counter[str] = field(default_factory=Counter)
    derived_rebuild_required: Counter[str] = field(default_factory=Counter)
    events: list[DecisionReplayEvent] = field(default_factory=list)

    def add(self, event: DecisionReplayEvent) -> None:
        self.scanned += 1
        if event.event_type:
            self.by_event_type[event.event_type] += 1
        if event.status == "restorable":
            self.restorable += 1
        elif event.status == "ambiguous":
            self.ambiguous += 1
        elif event.status == "invalid":
            self.invalid += 1
        else:
            self.unresolved += 1
        for item in event.derived_rebuild_required:
            self.derived_rebuild_required[item] += 1
        self.events.append(event)

    def to_dict(self, *, unresolved_only: bool = False) -> dict[str, Any]:
        events = self.events
        if unresolved_only:
            events = [
                event for event in events
                if event.status in {"unresolved", "ambiguous", "invalid"}
            ]
        return {
            "log_dir": str(self.log_dir),
            "scanned": self.scanned,
            "restorable": self.restorable,
            "unresolved": self.unresolved,
            "ambiguous": self.ambiguous,
            "invalid": self.invalid,
            "by_event_type": dict(sorted(self.by_event_type.items())),
            "derived_rebuild_required": dict(sorted(self.derived_rebuild_required.items())),
            "events": [event.to_dict() for event in events],
        }

    def to_text(self, *, unresolved_only: bool = False) -> str:
        lines = [
            f"Decision log: {self.log_dir}",
            f"Events scanned: {self.scanned}",
            f"Restorable: {self.restorable}",
            f"Unresolved: {self.unresolved}",
            f"Ambiguous: {self.ambiguous}",
            f"Invalid: {self.invalid}",
        ]
        if self.derived_rebuild_required:
            lines.append("")
            lines.append("Derived rebuilds required:")
            lines.extend(f"  {name}: {count}" for name, count in sorted(self.derived_rebuild_required.items()))
        events = self.events
        if unresolved_only:
            events = [
                event for event in events
                if event.status in {"unresolved", "ambiguous", "invalid"}
            ]
        if events:
            lines.append("")
            lines.append("Unresolved events:" if unresolved_only else "Events:")
            for event in events[:50]:
                suffix = f" ({event.reason})" if event.reason else ""
                lines.append(f"  {event.status}: {event.event_type or 'unknown'} audit={event.audit_id}{suffix}")
        return "\n".join(lines)


@dataclass
class DecisionReplayApplyReport:
    replay: DecisionReplayReport
    audit_applied: int = 0
    audit_skipped_existing: int = 0
    skipped_invalid: int = 0
    effect_applied: int = 0
    effect_skipped_unresolved: int = 0
    effect_unsupported: int = 0
    backup_guard: str | None = None

    @property
    def applied(self) -> int:
        return self.audit_applied

    @property
    def skipped_existing(self) -> int:
        return self.audit_skipped_existing

    def to_dict(self, *, unresolved_only: bool = False) -> dict[str, Any]:
        return {
            "audit_applied": self.audit_applied,
            "audit_skipped_existing": self.audit_skipped_existing,
            "skipped_invalid": self.skipped_invalid,
            "effect_applied": self.effect_applied,
            "effect_skipped_unresolved": self.effect_skipped_unresolved,
            "effect_unsupported": self.effect_unsupported,
            "backup_guard": self.backup_guard,
            "replay": self.replay.to_dict(unresolved_only=unresolved_only),
        }

    def to_text(self, *, unresolved_only: bool = False) -> str:
        lines = [
            "Decision replay apply",
            f"Applied audit rows: {self.audit_applied}",
            f"Skipped existing audit rows: {self.audit_skipped_existing}",
            f"Skipped invalid events: {self.skipped_invalid}",
            f"Applied safe effects: {self.effect_applied}",
            f"Skipped effects due unresolved refs: {self.effect_skipped_unresolved}",
            f"Unsupported effects needing rebuild/manual replay: {self.effect_unsupported}",
        ]
        if self.backup_guard:
            lines.append(f"Backup guard: {self.backup_guard}")
        lines.append("")
        lines.append(self.replay.to_text(unresolved_only=unresolved_only))
        return "\n".join(lines)


def iter_decision_events(log_dir: str | Path | None = None):
    root = Path(log_dir) if log_dir else DECISION_LOG_DIR
    if not root.exists():
        return
    for path in sorted(root.glob("*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                yield path, line_no, line


async def dry_run_decision_replay(conn, *, log_dir: str | Path | None = None, limit: int | None = None) -> DecisionReplayReport:
    root = Path(log_dir) if log_dir else DECISION_LOG_DIR
    report = DecisionReplayReport(log_dir=root)
    for path, line_no, line in iter_decision_events(root) or ():
        if limit is not None and report.scanned >= limit:
            break
        report.add(await inspect_decision_line(conn, root, path, line_no, line))
    return report


async def apply_decision_replay(
    conn,
    *,
    log_dir: str | Path | None = None,
    limit: int | None = None,
    require_backup: bool = True,
    backup_dir: str | Path | None = None,
    backup_max_age_hours: int = DEFAULT_BACKUP_MAX_AGE_HOURS,
) -> DecisionReplayApplyReport:
    """Restore durable audit rows from decision JSONL.

    This intentionally does not mutate derived entity/link/location tables yet.
    It restores the tamper-evident decision ledger, reports unresolved stable
    references, and tells the operator which derived tables need rebuilds.
    """
    backup_guard = None
    if require_backup:
        backup_guard = await assert_backup_guard(
            conn,
            backup_dir=backup_dir,
            max_age_hours=backup_max_age_hours,
        )

    root = Path(log_dir) if log_dir else DECISION_LOG_DIR
    apply_report = DecisionReplayApplyReport(
        replay=DecisionReplayReport(log_dir=root),
        backup_guard=backup_guard,
    )
    for path, line_no, line in iter_decision_events(root) or ():
        if limit is not None and apply_report.replay.scanned >= limit:
            break
        replay_event = await inspect_decision_line(conn, root, path, line_no, line)
        apply_report.replay.add(replay_event)
        if replay_event.status == "invalid":
            apply_report.skipped_invalid += 1
            continue

        payload = json.loads(line)
        inserted = await restore_audit_event(conn, payload)
        if inserted:
            apply_report.audit_applied += 1
        else:
            apply_report.audit_skipped_existing += 1

        effect = await apply_decision_effect(conn, payload, replay_event)
        if effect == "applied":
            apply_report.effect_applied += 1
        elif effect == "skipped_unresolved":
            apply_report.effect_skipped_unresolved += 1
        else:
            apply_report.effect_unsupported += 1

    await _reset_audit_log_sequence(conn)
    return apply_report


async def assert_backup_guard(
    conn,
    *,
    backup_dir: str | Path | None = None,
    max_age_hours: int = DEFAULT_BACKUP_MAX_AGE_HOURS,
) -> str:
    """Require a successful analyzer DB backup before replay apply."""
    max_age = timedelta(hours=max(1, int(max_age_hours)))
    db_status = await _latest_successful_backup_run(conn, max_age=max_age)
    if db_status:
        return db_status

    root = Path(backup_dir) if backup_dir else None
    if root is not None:
        files = sorted(root.rglob("unifiedanalyzer_*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            latest = files[0]
            stat = latest.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if stat.st_size > 0 and datetime.now(timezone.utc) - modified <= max_age:
                return f"backup file present: {latest}"

    raise BackupRequiredError(
        f"decision replay apply requires a successful analyzer DB backup from the last "
        f"{int(max_age.total_seconds() // 3600)}h; run `python -m src.main backup-db --kind daily` "
        "or pass --allow-no-backup for a scratch restore"
    )


async def _latest_successful_backup_run(conn, *, max_age: timedelta) -> str | None:
    try:
        row = await conn.fetchrow("""
            SELECT path, size_bytes, finished_at, restore_validation
            FROM analyzer_backup_runs
            WHERE status = 'success'
              AND COALESCE(size_bytes, 0) > 0
            ORDER BY finished_at DESC NULLS LAST, started_at DESC
            LIMIT 1
        """)
    except Exception:
        return None
    if not row:
        return None
    finished_at = _as_aware_datetime(_row_value(row, "finished_at"))
    if not finished_at or datetime.now(timezone.utc) - finished_at > max_age:
        return None
    restore_validation = (_row_value(row, "restore_validation") or "").lower()
    if restore_validation.startswith("failed") or restore_validation.startswith("error"):
        return None
    return (
        f"latest successful backup: {_row_value(row, 'path')} "
        f"({_row_value(row, 'size_bytes') or 0} bytes at {_row_value(row, 'finished_at')})"
    )


async def restore_audit_event(conn, event: dict[str, Any]) -> bool:
    _validate_replay_event(event)
    entity_ids = _uuid_list_or_none(event.get("entity_ids") or [])
    created_at = datetime.fromisoformat(event["created_at"])
    result = await conn.execute(
        """
        INSERT INTO audit_log (
            id, prev_sha256, sha256, action, actor, entity_ids, payload,
            idempotency_key, decision_jsonl_path, decision_jsonl_written_at,
            decision_jsonl_error, created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6::uuid[], $7::jsonb, $8, NULL, NOW(), NULL, $9)
        ON CONFLICT (id) DO NOTHING
        """,
        int(event["audit_id"]),
        event["prev_sha256"],
        event["sha256"],
        event["event_type"],
        event["actor"],
        entity_ids,
        json.dumps(event["payload"], default=str),
        event["idempotency_key"],
        created_at,
    )
    return str(result).endswith(" 1")


async def apply_decision_effect(conn, event: dict[str, Any], replay_event: DecisionReplayEvent) -> str:
    """Apply small idempotent effects during scratch restore.

    Destructive or underspecified decisions deliberately return unsupported; the
    dry-run report still tells the operator which derived systems must rebuild.
    """
    if replay_event.status != "restorable":
        return "skipped_unresolved"

    event_type = _canonical_event_type(event.get("event_type"))
    payload = event.get("payload") or {}
    entity_ids = replay_event.resolved_entity_ids

    if event_type == "dismiss_identity_candidate":
        if len(entity_ids) != 2:
            return "skipped_unresolved"
        a, b = sorted(entity_ids)
        features = _dismiss_feature_snapshot(payload, event.get("evidence_snapshot"))
        await conn.execute(
            """
            INSERT INTO identity_labels (entity_a, entity_b, features, label, source)
            VALUES ($1::uuid, $2::uuid, $3::jsonb, 0, 'decision_replay')
            ON CONFLICT (entity_a, entity_b)
            DO UPDATE SET features = EXCLUDED.features,
                          label = 0,
                          source = 'decision_replay',
                          created_at = NOW()
            """,
            a,
            b,
            json.dumps(features, default=str),
        )
        await conn.execute(
            """
            DELETE FROM entity_relationships
            WHERE relationship_type = 'same_person_probability'
              AND ((entity_a_id = $1::uuid AND entity_b_id = $2::uuid)
                OR (entity_a_id = $2::uuid AND entity_b_id = $1::uuid))
            """,
            a,
            b,
        )
        return "applied"

    if event_type in {"confirm_relationship", "reject_relationship"}:
        if len(entity_ids) != 2:
            return "skipped_unresolved"
        relationship_type = payload.get("relationship_type")
        if relationship_type != "same_person_probability":
            return "unsupported"
        a, b = sorted(entity_ids)
        label = 1 if event_type == "confirm_relationship" else 0
        features = _relationship_feature_snapshot(payload, event.get("evidence_snapshot"))
        await conn.execute(
            """
            INSERT INTO identity_labels (entity_a, entity_b, features, label, source)
            VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, 'decision_replay')
            ON CONFLICT (entity_a, entity_b)
            DO UPDATE SET features = EXCLUDED.features,
                          label = EXCLUDED.label,
                          source = 'decision_replay',
                          created_at = NOW()
            """,
            a,
            b,
            json.dumps(features, default=str),
            label,
        )
        if event_type == "reject_relationship":
            await conn.execute(
                """
                DELETE FROM entity_relationships
                WHERE relationship_type = 'same_person_probability'
                  AND ((entity_a_id = $1::uuid AND entity_b_id = $2::uuid)
                    OR (entity_a_id = $2::uuid AND entity_b_id = $1::uuid))
                """,
                a,
                b,
            )
        return "applied"

    if event_type == "assign_target_tier":
        if len(entity_ids) != 1:
            return "skipped_unresolved"
        await conn.execute(
            "UPDATE entities SET watch_status = $1, updated_at = NOW() WHERE id = $2::uuid",
            payload.get("watch_status"),
            entity_ids[0],
        )
        return "applied"

    if event_type == "add_note":
        if len(entity_ids) != 1:
            return "skipped_unresolved"
        await conn.execute(
            """
            UPDATE entities
            SET notes = COALESCE($1, notes),
                silence_threshold_days = COALESCE($2, silence_threshold_days),
                updated_at = NOW()
            WHERE id = $3::uuid
            """,
            payload.get("notes"),
            payload.get("silence_threshold_days"),
            entity_ids[0],
        )
        return "applied"

    if event_type == "adjust_source_confidence":
        if len(entity_ids) != 1:
            return "skipped_unresolved"
        source = payload.get("source")
        platform_id = payload.get("platform_id")
        confidence = payload.get("confidence")
        if source is None or platform_id is None or confidence is None:
            return "unsupported"
        await conn.execute(
            """
            UPDATE entity_platform_links
            SET confidence = $1,
                updated_at = NOW()
            WHERE entity_id = $2::uuid
              AND source = $3
              AND platform_id = $4
            """,
            confidence,
            entity_ids[0],
            source,
            platform_id,
        )
        return "applied"

    return "unsupported"


def _dismiss_feature_snapshot(
    payload: dict[str, Any],
    evidence_snapshot: dict[str, Any] | None = None,
) -> dict[str, float]:
    for source in _snapshot_sources(payload, evidence_snapshot):
        explicit = source.get("features")
        if isinstance(explicit, dict):
            features = _coerce_feature_snapshot(explicit)
            if features:
                return features

    for source in _snapshot_sources(payload, evidence_snapshot):
        features = _features_from_snapshot_container(source.get("candidate_evidence"))
        if features:
            return features

    for source in _snapshot_sources(payload, evidence_snapshot):
        features = _features_from_snapshot_container(source.get("relationship_snapshot"))
        if features:
            return features

    return {}


def _relationship_feature_snapshot(
    payload: dict[str, Any],
    evidence_snapshot: dict[str, Any] | None = None,
) -> dict[str, float]:
    for source in _snapshot_sources(payload, evidence_snapshot):
        explicit = source.get("features")
        if isinstance(explicit, dict):
            features = _coerce_feature_snapshot(explicit)
            if features:
                return features

    for source in _snapshot_sources(payload, evidence_snapshot):
        features = _features_from_snapshot_container(source.get("relationship_snapshot"))
        if features:
            return features

    return {}


def _snapshot_sources(
    payload: dict[str, Any],
    evidence_snapshot: dict[str, Any] | None,
):
    yield payload
    if isinstance(evidence_snapshot, dict):
        yield evidence_snapshot


def _features_from_snapshot_container(snapshot: Any) -> dict[str, float]:
    if not isinstance(snapshot, dict):
        return {}
    sources = snapshot.get("sources")
    if isinstance(sources, str):
        try:
            sources = json.loads(sources)
        except json.JSONDecodeError:
            sources = {}
    if not isinstance(sources, dict):
        return {}
    signals = sources.get("contributing_signals")
    if isinstance(signals, dict):
        return _coerce_feature_snapshot(signals)
    if not isinstance(signals, list):
        return {}

    features: dict[str, float] = {}
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        signal_type = signal.get("type")
        if not signal_type:
            continue
        try:
            confidence = float(signal.get("confidence") or 0.0)
        except (TypeError, ValueError):
            continue
        key = str(signal_type)
        if confidence > features.get(key, 0.0):
            features[key] = confidence
    return features


def _coerce_feature_snapshot(raw: dict[str, Any]) -> dict[str, float]:
    features: dict[str, float] = {}
    for key, value in raw.items():
        try:
            features[str(key)] = float(value or 0.0)
        except (TypeError, ValueError):
            continue
    return features


async def _reset_audit_log_sequence(conn) -> None:
    try:
        await conn.execute("""
            SELECT setval(
                pg_get_serial_sequence('audit_log', 'id'),
                GREATEST(COALESCE((SELECT MAX(id) FROM audit_log), 0), 1),
                true
            )
        """)
    except Exception:
        # Sequence repair is helpful after restoring explicit ids, but replay
        # reporting is still useful in fake/scratch DBs without a sequence.
        return


async def inspect_decision_line(conn, root: Path, path: Path, line_no: int, line: str) -> DecisionReplayEvent:
    event_type = None
    audit_id = None
    rel_path = _safe_relative(path, root)
    try:
        payload = json.loads(line)
        _validate_replay_event(payload)
        event_type = _canonical_event_type(payload.get("event_type"))
        audit_id = payload.get("audit_id")
        payload_summary = summarize_payload(payload.get("payload") or {})
    except Exception as exc:
        return DecisionReplayEvent(
            path=rel_path,
            line=line_no,
            event_type=event_type,
            audit_id=audit_id,
            status="invalid",
            reason=str(exc),
        )

    refs = stable_refs_from_event(payload)
    derived = list(DERIVED_REBUILD_BY_EVENT.get(event_type, ()))
    if not refs:
        return DecisionReplayEvent(
            path=rel_path,
            line=line_no,
            event_type=event_type,
            audit_id=audit_id,
            status="unresolved",
            reason="no stable platform references in decision payload",
            derived_rebuild_required=derived,
            payload_summary=payload_summary,
        )

    resolved = await resolve_stable_refs(conn, refs)
    if resolved["ambiguous"]:
        status = "ambiguous"
        reason = "stable reference resolves to multiple active entities"
    elif not resolved["entity_ids"]:
        status = "unresolved"
        reason = "stable references no longer resolve to an active entity"
    else:
        status = "restorable"
        reason = None

    return DecisionReplayEvent(
        path=rel_path,
        line=line_no,
        event_type=event_type,
        audit_id=audit_id,
        status=status,
        reason=reason,
        resolved_entity_ids=sorted(resolved["entity_ids"]),
        stable_refs=refs,
        derived_rebuild_required=derived,
        payload_summary=payload_summary,
    )


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "watch_status",
        "relationship_type",
        "is_real",
        "is_correct",
        "role",
        "confidence",
        "source",
        "platform_id",
        "notes",
        "reason",
    ):
        if key in payload:
            value = payload[key]
            if isinstance(value, str) and len(value) > 160:
                value = value[:157] + "..."
            summary[key] = value
    return summary


def stable_refs_from_event(event: dict[str, Any]) -> list[dict[str, str | None]]:
    refs: list[dict[str, str | None]] = []
    for ref in event.get("stable_refs") or []:
        if not isinstance(ref, dict):
            continue
        source = _clean(ref.get("source"))
        platform_id = _clean(ref.get("platform_id"))
        username = _clean(ref.get("platform_username"))
        if not source or not (platform_id or username):
            continue
        out = {
            "source": source,
            "platform_id": platform_id,
            "platform_username": username,
        }
        if out not in refs:
            refs.append(out)
    if refs:
        return refs

    payload = event.get("payload") or {}
    for snapshot in _walk_snapshots(payload):
        for link in snapshot.get("platform_links") or []:
            source = _clean(link.get("source"))
            platform_id = _clean(link.get("platform_id"))
            username = _clean(link.get("platform_username"))
            if not source or not (platform_id or username):
                continue
            ref = {
                "source": source,
                "platform_id": platform_id,
                "platform_username": username,
            }
            if ref not in refs:
                refs.append(ref)
    return refs


def _canonical_event_type(event_type: Any) -> str | None:
    if event_type is None:
        return None
    text = str(event_type)
    return LEGACY_EVENT_TYPE_ALIASES.get(text, text)


def _validate_replay_event(event: dict[str, Any]) -> None:
    event_type = event.get("event_type")
    if event_type not in LEGACY_EVENT_TYPE_ALIASES:
        _validate_decision_event(event)
        return

    normalized = dict(event)
    normalized["event_type"] = LEGACY_EVENT_TYPE_ALIASES[event_type]
    _validate_decision_event(normalized)


async def resolve_stable_refs(conn, refs: list[dict[str, str | None]]) -> dict[str, Any]:
    entity_ids: set[str] = set()
    ambiguous_refs: list[dict[str, str | None]] = []
    for ref in refs:
        rows = await conn.fetch(
            """
            SELECT DISTINCT entity_id::text AS entity_id
            FROM entity_platform_links
            WHERE source = $1
              AND retracted_at IS NULL
              AND (
                   ($2::text IS NOT NULL AND platform_id = $2)
                OR ($3::text IS NOT NULL AND lower(platform_username) = lower($3))
              )
            """,
            ref["source"],
            ref["platform_id"],
            ref["platform_username"],
        )
        matches = {str(row["entity_id"]) for row in rows}
        if len(matches) > 1:
            ambiguous_refs.append(ref)
        entity_ids.update(matches)
    return {"entity_ids": entity_ids, "ambiguous": ambiguous_refs}


def _walk_snapshots(value: Any):
    if isinstance(value, dict):
        if isinstance(value.get("platform_links"), list):
            yield value
        for child in value.values():
            yield from _walk_snapshots(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_snapshots(child)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _uuid_list_or_none(values: list[str]) -> list[str] | None:
    if not values:
        return None
    return [str(UUID(str(value))) for value in values]


def _as_aware_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _row_value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)
