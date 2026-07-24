from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Iterable, Mapping


SUPPRESSED_LOCATION_STATUSES = {"rejected", "suppressed"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_confidence(value: Any) -> float | None:
    confidence = _coerce_float(value)
    if confidence is None or confidence < 0:
        return None
    if confidence > 1:
        confidence = confidence / 100.0
    return min(confidence, 1.0)


def _iso_datetime(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    text = _clean(value)
    return text


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _rounded_coord(value: Any) -> float | None:
    coord = _coerce_float(value)
    if coord is None:
        return None
    return round(coord, 7)


def location_evidence_key(
    *,
    entity_id: str,
    source: str | None,
    evidence_type: str | None,
    source_table: str | None = None,
    source_record_id: str | None = None,
    occurred_at: Any = None,
    lat: Any = None,
    lng: Any = None,
    label: str | None = None,
) -> str:
    """Return a deterministic key for one normalized location claim."""
    stable = {
        "entity_id": str(entity_id),
        "source": _clean(source) or "unknown",
        "evidence_type": _clean(evidence_type) or "inferred",
        "source_table": _clean(source_table),
        "source_record_id": _clean(source_record_id),
    }
    if not stable["source_record_id"]:
        stable.update({
            "occurred_at": _iso_datetime(occurred_at),
            "lat": _rounded_coord(lat),
            "lng": _rounded_coord(lng),
            "label": _clean(label),
        })
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evidence_key_from_location_ref(entity_id: str, location_ref: Mapping[str, Any]) -> str | None:
    explicit = _clean(location_ref.get("evidence_key"))
    if explicit and len(explicit) == 64:
        return explicit.lower()
    source = location_ref.get("source")
    evidence_type = location_ref.get("evidence_type")
    if not source and not evidence_type:
        return None
    return location_evidence_key(
        entity_id=entity_id,
        source=source,
        evidence_type=evidence_type,
        source_table=location_ref.get("source_table"),
        source_record_id=location_ref.get("source_record_id"),
        occurred_at=location_ref.get("occurred_at") or location_ref.get("date"),
        lat=_first_present(location_ref.get("lat"), _first_coord(location_ref.get("start"), 0)),
        lng=_first_present(location_ref.get("lng"), _first_coord(location_ref.get("start"), 1)),
        label=location_ref.get("label") or location_ref.get("name"),
    )


def attach_location_evidence_key(entity_id: str, item: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["evidence_key"] = location_evidence_key(
        entity_id=entity_id,
        source=out.get("source"),
        evidence_type=out.get("evidence_type"),
        source_table=out.get("source_table"),
        source_record_id=out.get("source_record_id"),
        occurred_at=out.get("occurred_at") or out.get("date"),
        lat=out.get("lat") or _first_point_coord(out.get("points"), 0),
        lng=out.get("lng") or _first_point_coord(out.get("points"), 1),
        label=out.get("label") or out.get("name"),
    )
    return out


def is_location_suppressed(status: str | None) -> bool:
    return (status or "").lower() in SUPPRESSED_LOCATION_STATUSES


async def fetch_location_evidence_statuses(conn, evidence_keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    keys = sorted({str(key) for key in evidence_keys if key})
    if not keys:
        return {}
    rows = await conn.fetch(
        """
        SELECT evidence_key::text AS evidence_key, status, decision_notes, decided_at
        FROM location_evidence
        WHERE evidence_key::text = ANY($1::text[])
        """,
        keys,
    )
    return {
        row["evidence_key"]: {
            "status": row["status"],
            "decision_notes": row["decision_notes"],
            "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
        }
        for row in rows
    }


async def upsert_location_evidence_batch(conn, entity_id: str, items: Iterable[Mapping[str, Any]]) -> int:
    rows = [_row_from_item(entity_id, item) for item in items]
    rows = [row for row in rows if row is not None]
    if not rows:
        return 0
    await conn.executemany(
        """
        INSERT INTO location_evidence (
            evidence_key, entity_id, source, evidence_type, source_table,
            source_record_id, occurred_at, lat, lng, label, confidence,
            geometry, payload
        )
        VALUES (
            $1, $2::uuid, $3, $4, $5,
            $6, $7, $8, $9, $10, $11,
            $12::jsonb, $13::jsonb
        )
        ON CONFLICT (evidence_key) DO UPDATE SET
            entity_id = EXCLUDED.entity_id,
            source = EXCLUDED.source,
            evidence_type = EXCLUDED.evidence_type,
            source_table = EXCLUDED.source_table,
            source_record_id = EXCLUDED.source_record_id,
            occurred_at = COALESCE(EXCLUDED.occurred_at, location_evidence.occurred_at),
            lat = COALESCE(EXCLUDED.lat, location_evidence.lat),
            lng = COALESCE(EXCLUDED.lng, location_evidence.lng),
            label = COALESCE(EXCLUDED.label, location_evidence.label),
            confidence = GREATEST(
                COALESCE(location_evidence.confidence, 0),
                COALESCE(EXCLUDED.confidence, 0)
            ),
            geometry = CASE
                WHEN EXCLUDED.geometry <> '{}'::jsonb THEN EXCLUDED.geometry
                ELSE location_evidence.geometry
            END,
            payload = location_evidence.payload || EXCLUDED.payload,
            updated_at = NOW()
        """,
        rows,
    )
    return len(rows)


async def apply_location_decision(
    conn,
    *,
    entity_id: str,
    location_ref: Mapping[str, Any],
    is_correct: bool,
    confidence: Any = None,
    notes: str | None = None,
    audit_id: int | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    evidence_key = evidence_key_from_location_ref(entity_id, location_ref)
    if not evidence_key:
        return {"updated": 0, "evidence_key": None, "status": None}
    status = "confirmed" if is_correct else "rejected"
    row = _row_from_item(entity_id, {**dict(location_ref), "evidence_key": evidence_key})
    if row is None:
        return {"updated": 0, "evidence_key": evidence_key, "status": None}
    row = list(row)
    explicit_confidence = _coerce_confidence(confidence)
    if explicit_confidence is not None:
        row[10] = explicit_confidence
    decision_audit_id = audit_id if isinstance(audit_id, int) and audit_id > 0 else None
    await conn.execute(
        """
        INSERT INTO location_evidence (
            evidence_key, entity_id, source, evidence_type, source_table,
            source_record_id, occurred_at, lat, lng, label, confidence,
            geometry, payload, status, decision_audit_id, decision_actor,
            decision_notes, decided_at
        )
        VALUES (
            $1, $2::uuid, $3, $4, $5,
            $6, $7, $8, $9, $10, $11,
            $12::jsonb, $13::jsonb, $14, $15, $16,
            $17, NOW()
        )
        ON CONFLICT (evidence_key) DO UPDATE SET
            status = EXCLUDED.status,
            confidence = GREATEST(
                COALESCE(location_evidence.confidence, 0),
                COALESCE(EXCLUDED.confidence, 0)
            ),
            decision_audit_id = EXCLUDED.decision_audit_id,
            decision_actor = EXCLUDED.decision_actor,
            decision_notes = EXCLUDED.decision_notes,
            decided_at = NOW(),
            lat = COALESCE(location_evidence.lat, EXCLUDED.lat),
            lng = COALESCE(location_evidence.lng, EXCLUDED.lng),
            label = COALESCE(location_evidence.label, EXCLUDED.label),
            payload = location_evidence.payload || EXCLUDED.payload,
            updated_at = NOW()
        """,
        *row,
        status,
        decision_audit_id,
        actor,
        notes or "",
    )
    return {"updated": 1, "evidence_key": evidence_key, "status": status}


def _row_from_item(entity_id: str, item: Mapping[str, Any]) -> tuple | None:
    evidence_key = _clean(item.get("evidence_key")) or evidence_key_from_location_ref(entity_id, item)
    if not evidence_key:
        return None
    source = _clean(item.get("source")) or "unknown"
    evidence_type = _clean(item.get("evidence_type")) or "inferred"
    occurred_at = _parse_datetime(item.get("occurred_at") or item.get("date"))
    lat = _coerce_float(_first_present(
        item.get("lat"),
        _first_point_coord(item.get("points"), 0),
        _first_coord(item.get("start"), 0),
    ))
    lng = _coerce_float(_first_present(
        item.get("lng"),
        _first_point_coord(item.get("points"), 1),
        _first_coord(item.get("start"), 1),
    ))
    label = _clean(item.get("label") or item.get("name"))
    confidence = _coerce_confidence(item.get("confidence")) or 0.0
    geometry = _geometry_from_item(item)
    payload = {
        "kind": item.get("kind"),
        "route_type": item.get("route_type") or item.get("type"),
        "point_count": item.get("point_count") or len(item.get("points") or []),
    }
    payload = {key: value for key, value in payload.items() if value not in (None, "", 0)}
    return (
        evidence_key,
        str(entity_id),
        source,
        evidence_type,
        _clean(item.get("source_table")),
        _clean(item.get("source_record_id")),
        occurred_at,
        lat,
        lng,
        label,
        confidence,
        json.dumps(geometry, default=str),
        json.dumps(payload, default=str),
    )


def _geometry_from_item(item: Mapping[str, Any]) -> dict[str, Any]:
    points = item.get("points")
    if isinstance(points, list) and points:
        return {"type": "LineString", "points": points}
    return {}


def _first_point_coord(points: Any, idx: int) -> float | None:
    if not isinstance(points, list) or not points:
        return None
    return _first_coord(points[0], idx)


def _first_coord(value: Any, idx: int) -> float | None:
    if isinstance(value, (list, tuple)) and len(value) > idx:
        return _coerce_float(value[idx])
    return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
