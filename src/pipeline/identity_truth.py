"""Corroborated identity truth promotion.

SpiderFoot/recon observations are weak leads. This module only promotes an
``auto_truth`` assertion when an independent hard Analyzer signal corroborates
the same entity/value pair.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any


SPIDERFOOT_PLATFORMS = {"spiderfoot", "recon"}
SPIDERFOOT_TABLES = {"recon_observations", "recon_targets"}
HARD_SIGNAL_TYPES = {
    "cross_platform_link",
    "email_match",
    "exact_email",
    "exact_phone",
    "phone_match",
    "shared_domain",
    "shared_website",
    "verified_profile_link",
}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        if isinstance(row, dict):
            return row.get(key, default)
        return default


@dataclass(frozen=True)
class SignalEvidence:
    id: str | None
    signal_type: str
    source_platform: str
    source_table: str | None
    value: str
    confidence: float
    metadata: dict[str, Any]

    @property
    def family(self) -> str:
        if self.source_platform in SPIDERFOOT_PLATFORMS or (self.source_table or "") in SPIDERFOOT_TABLES:
            return "spiderfoot"
        if self.source_platform:
            return self.source_platform
        return "analyzer"

    @property
    def is_spiderfoot(self) -> bool:
        return self.family == "spiderfoot"

    @property
    def is_hard(self) -> bool:
        if self.is_spiderfoot:
            return False
        if self.signal_type in HARD_SIGNAL_TYPES:
            return True
        return self.confidence >= 0.9


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def coerce_signal(row: Any) -> SignalEvidence:
    confidence = _row_get(row, "confidence", 0.0) or 0.0
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    return SignalEvidence(
        id=str(_row_get(row, "id") or "") or None,
        signal_type=str(_row_get(row, "signal_type") or "").strip().lower(),
        source_platform=str(_row_get(row, "source_platform") or "").strip().lower(),
        source_table=str(_row_get(row, "source_table") or "").strip().lower() or None,
        value=str(_row_get(row, "value") or "").strip(),
        confidence=max(0.0, min(confidence, 1.0)),
        metadata=_json_dict(_row_get(row, "metadata")),
    )


def corroborated_auto_truth(signals: list[Any], *, min_confidence: float = 0.85) -> tuple[bool, float, dict[str, Any]]:
    evidence = [coerce_signal(row) for row in signals if str(_row_get(row, "value") or "").strip()]
    spiderfoot = [row for row in evidence if row.is_spiderfoot]
    hard = [row for row in evidence if row.is_hard]
    hard_families = sorted({row.family for row in hard})
    if not spiderfoot or not hard:
        return False, 0.0, {
            "reason": "requires_spiderfoot_and_independent_hard_signal",
            "spiderfoot_count": len(spiderfoot),
            "hard_count": len(hard),
            "hard_families": hard_families,
        }
    confidence = min(0.99, max(min_confidence, (max(row.confidence for row in hard) + max(row.confidence for row in spiderfoot)) / 2))
    if confidence < min_confidence:
        return False, confidence, {
            "reason": "below_min_confidence",
            "spiderfoot_count": len(spiderfoot),
            "hard_count": len(hard),
            "hard_families": hard_families,
        }
    return True, confidence, {
        "reason": "spiderfoot_corroborated_by_hard_signal",
        "spiderfoot_count": len(spiderfoot),
        "hard_count": len(hard),
        "hard_families": hard_families,
        "signal_types": sorted({row.signal_type for row in evidence}),
    }


def build_truth_assertion(entity_id: str, value: str, signals: list[Any], *, min_confidence: float = 0.85) -> dict[str, Any] | None:
    ok, confidence, summary = corroborated_auto_truth(signals, min_confidence=min_confidence)
    if not ok:
        return None
    evidence = [coerce_signal(row) for row in signals]
    return {
        "assertion_type": "same_person",
        "entity_id": str(entity_id),
        "value": str(value),
        "truth_state": "auto_truth",
        "confidence": confidence,
        "evidence_count": len(evidence),
        "evidence_signal_ids": [row.id for row in evidence if row.id],
        "evidence_summary": summary,
        "source_platform": "analyzer",
        "source_table": "identity_signals",
    }


async def promote_spiderfoot_truth(
    conn,
    *,
    write: bool = True,
    min_confidence: float = 0.85,
    limit: int = 5000,
) -> dict[str, Any]:
    rows = await conn.fetch(
        """
        SELECT id::text, entity_id::text, signal_type, source_platform,
               source_table, value, confidence, metadata
        FROM identity_signals
        WHERE value IS NOT NULL
          AND value <> ''
          AND (
              source_platform IN ('spiderfoot', 'recon')
              OR source_table IN ('recon_observations', 'recon_targets')
              OR signal_type = ANY($1::text[])
              OR confidence >= 0.90
          )
        ORDER BY created_at DESC
        LIMIT $2
        """,
        sorted(HARD_SIGNAL_TYPES),
        max(1, int(limit)),
    )
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in rows:
        entity_id = str(_row_get(row, "entity_id") or "")
        value = str(_row_get(row, "value") or "").strip()
        if entity_id and value:
            grouped[(entity_id, value)].append(row)

    assertions = [
        assertion
        for (entity_id, value), signals in grouped.items()
        if (assertion := build_truth_assertion(entity_id, value, signals, min_confidence=min_confidence))
    ]
    if write and assertions:
        await conn.executemany(
            """
            INSERT INTO identity_truth_assertions (
                assertion_type, entity_id, value, truth_state, confidence,
                evidence_count, evidence_signal_ids, evidence_summary,
                source_platform, source_table, updated_at
            )
            VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::uuid[], $8::jsonb, $9, $10, NOW())
            ON CONFLICT (assertion_type, entity_id, value, truth_state) DO UPDATE SET
                confidence = GREATEST(identity_truth_assertions.confidence, EXCLUDED.confidence),
                evidence_count = GREATEST(identity_truth_assertions.evidence_count, EXCLUDED.evidence_count),
                evidence_signal_ids = EXCLUDED.evidence_signal_ids,
                evidence_summary = EXCLUDED.evidence_summary,
                updated_at = NOW()
            """,
            [
                (
                    row["assertion_type"],
                    row["entity_id"],
                    row["value"],
                    row["truth_state"],
                    row["confidence"],
                    row["evidence_count"],
                    row["evidence_signal_ids"],
                    json.dumps(row["evidence_summary"], default=str),
                    row["source_platform"],
                    row["source_table"],
                )
                for row in assertions
            ],
        )
    return {
        "candidates": len(grouped),
        "promoted": len(assertions) if write else 0,
        "would_promote": len(assertions),
        "write": write,
        "min_confidence": min_confidence,
    }
