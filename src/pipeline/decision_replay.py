from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.util.audit_log import DECISION_LOG_DIR, _validate_decision_event


DERIVED_REBUILD_BY_EVENT = {
    "merge_confirmed": ("identity_scores", "entity_graph", "timeline_events"),
    "split_person": ("identity_scores", "entity_graph", "timeline_events"),
    "dismiss_identity_candidate": ("identity_scores", "review_candidates"),
    "confirm_relationship": ("entity_graph", "relationship_views"),
    "reject_relationship": ("entity_graph", "relationship_views"),
    "confirm_location": ("location_claims", "map_layers"),
    "reject_location": ("location_claims", "map_layers"),
    "assign_media_owner": ("media_attribution", "timeline_events"),
    "assign_person_in_photo": ("face_links", "timeline_events"),
    "assign_target_tier": ("priority_hints", "watch_views"),
    "add_note": ("person_views",),
    "adjust_source_confidence": ("identity_scores", "relationship_views", "location_claims"),
}


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "log_dir": str(self.log_dir),
            "scanned": self.scanned,
            "restorable": self.restorable,
            "unresolved": self.unresolved,
            "ambiguous": self.ambiguous,
            "invalid": self.invalid,
            "by_event_type": dict(sorted(self.by_event_type.items())),
            "derived_rebuild_required": dict(sorted(self.derived_rebuild_required.items())),
            "events": [event.to_dict() for event in self.events],
        }

    def to_text(self) -> str:
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
        if self.events:
            lines.append("")
            lines.append("Events:")
            for event in self.events[:50]:
                suffix = f" ({event.reason})" if event.reason else ""
                lines.append(f"  {event.status}: {event.event_type or 'unknown'} audit={event.audit_id}{suffix}")
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


async def inspect_decision_line(conn, root: Path, path: Path, line_no: int, line: str) -> DecisionReplayEvent:
    event_type = None
    audit_id = None
    rel_path = _safe_relative(path, root)
    try:
        payload = json.loads(line)
        _validate_decision_event(payload)
        event_type = payload.get("event_type")
        audit_id = payload.get("audit_id")
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
    )


def stable_refs_from_event(event: dict[str, Any]) -> list[dict[str, str | None]]:
    refs: list[dict[str, str | None]] = []
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


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return str(path)
