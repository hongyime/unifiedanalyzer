"""Emit `shared_life_context` cross-entity identity signals.

Reads the NER enrichment payload produced by
`src/pipeline/entity_enrichment.py` (stored at
`entities.metadata->'enrichment'`) and, for every pair of entities that
share a RARE organization / school / location, emits a
`shared_life_context` identity_signals row with a confidence that scales
with how many rare items they overlap on.

Rarity guard — key to precision:
  A "rare" item is one that appears in fewer than
  `SHARED_LIFE_CONTEXT_RARITY` (default 5%) of enriched entities.
  Ubiquitous mentions ("Singapore", "Google") produce nothing; a shared
  small university, church, or startup does. Without this guard the
  scorer would drown in noise from generic locations.

Signal shape:
  entity_id           = lexicographically smaller entity UUID
  target_record_id    = the OTHER entity's UUID as text
  signal_type         = 'shared_life_context'
  value               = 'shared:{first rare item, alphabetical}'
  confidence          = min(0.9, 0.35 + 0.15 * (num_shared_rare_items - 1))
  metadata JSONB      = {shared_items: [...], model: ..., rarity_threshold: ...}

Weight in the scorer: `identity_scorer._TYPE_WEIGHT["shared_life_context"] = 0.35`
(the actual same-person contribution is `weight * confidence`).

Persistence pattern: delete-then-insert (same as topical_similarity,
face_pair_knn) — signals are recomputed from scratch each cycle so they
stay consistent with the latest enrichment payload.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from itertools import combinations

from src.db.connection import get_analyzer_pool

logger = logging.getLogger(__name__)

_SIGNAL_TYPE = "shared_life_context"


def _is_enabled() -> bool:
    return os.getenv("SHARED_LIFE_CONTEXT_ENABLED", "1") == "1"


def _rarity_threshold() -> float:
    """Max fraction of enriched entities that may mention an item for it to
    count as 'rare' (default 0.05 = 5%)."""
    try:
        return float(os.getenv("SHARED_LIFE_CONTEXT_RARITY", "0.05"))
    except ValueError:
        return 0.05


def _base_confidence() -> float:
    """Single-item shared confidence — anchored to the scorer weight so the
    combined contribution starts at ~0.12 for one shared item and rises with
    each additional overlap. Cannot exceed 0.9."""
    return 0.35


def _step_confidence() -> float:
    return 0.15


def _decode_enrichment(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _normalize_item(text: str) -> str | None:
    """Fold case + collapse whitespace so 'Google' == 'google  ' at index-build
    time. Anything shorter than 3 chars after normalization is discarded (too
    generic to be evidence)."""
    if not text:
        return None
    v = " ".join(text.split()).lower()
    if len(v) < 3:
        return None
    return v


# The three buckets we cross-index; must match entity_enrichment._LABEL_BUCKETS
# except we deliberately EXCLUDE persons_mentioned — a shared PERSON mention
# usually means both entities talk ABOUT the same third party, not that they
# are the same person.
_INDEXED_BUCKETS: tuple[str, ...] = ("employers_schools", "locations")


async def emit_shared_life_context_signals() -> dict:
    if not _is_enabled():
        return {"skipped": "disabled"}

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        # Only entities that already have an enrichment payload; anything else
        # can't contribute to the pairwise index.
        rows = await conn.fetch(
            """
            SELECT id::text AS id, metadata->'enrichment' AS enrichment
            FROM entities
            WHERE metadata ? 'enrichment'
              AND metadata->'enrichment' IS NOT NULL
            """
        )

    total_entities = len(rows)
    if total_entities < 2:
        return {"pairs_evaluated": 0, "signals_emitted": 0,
                "reason": "too_few_enriched_entities",
                "enriched_entities": total_entities}

    # Build inverted index: normalized_item -> {entity_ids}.
    # Also remember the original casing (first-seen) for the payload/value.
    index: dict[str, set[str]] = defaultdict(set)
    display: dict[str, str] = {}
    for r in rows:
        enrichment = _decode_enrichment(r["enrichment"])
        seen_here: set[str] = set()
        for bucket in _INDEXED_BUCKETS:
            for item in enrichment.get(bucket) or []:
                surface = None
                if isinstance(item, dict):
                    surface = item.get("text")
                elif isinstance(item, str):
                    surface = item
                norm = _normalize_item(surface or "")
                if not norm or norm in seen_here:
                    continue
                seen_here.add(norm)
                index[norm].add(r["id"])
                display.setdefault(norm, (surface or "").strip())

    rarity = _rarity_threshold()
    max_entities_per_item = max(1, int(total_entities * rarity))

    rare_items: dict[str, set[str]] = {
        item: eids for item, eids in index.items()
        if 2 <= len(eids) <= max_entities_per_item
    }

    # pair (a<b) -> set of shared rare items
    pair_overlap: dict[tuple[str, str], set[str]] = defaultdict(set)
    for item, eids in rare_items.items():
        for a, b in combinations(sorted(eids), 2):
            pair_overlap[(a, b)].add(item)

    # Human-dismissed pairs (same convention as identity_scorer): don't
    # re-surface signals for pairs the analyst has already said are different.
    async with analyzer.acquire() as conn:
        dismissed_rows = await conn.fetch(
            """
            SELECT entity_a::text AS a, entity_b::text AS b
            FROM identity_labels
            WHERE label = 0 AND (source IS NULL OR source NOT LIKE 'auto\\_%' ESCAPE '\\')
            """
        )
    dismissed = {(r["a"], r["b"]) for r in dismissed_rows}

    base = _base_confidence()
    step = _step_confidence()
    inserts: list[tuple] = []
    for (a, b), shared_norms in pair_overlap.items():
        if (a, b) in dismissed:
            continue
        if not shared_norms:
            continue

        # Sort for stability so `value` and metadata are deterministic across runs.
        ordered_norms = sorted(shared_norms)
        ordered_display = [display.get(n, n) for n in ordered_norms]
        n_shared = len(ordered_norms)

        confidence = min(0.9, base + step * (n_shared - 1))
        first_item = ordered_display[0]
        value = f"shared:{first_item}"

        metadata = {
            "shared_items": ordered_display,
            "n_shared": n_shared,
            "rarity_threshold": rarity,
            "total_enriched_entities": total_entities,
        }

        inserts.append((
            a,                          # entity_id (smaller UUID)
            _SIGNAL_TYPE,
            "analyzer",                 # source_platform
            "entities",                 # source_table
            "metadata->enrichment",     # source_column
            None,                       # source_record_id
            None,                       # target_platform
            b,                          # target_record_id (other UUID as text)
            value,
            round(confidence, 4),
            json.dumps(metadata, default=str),
        ))

    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = $1", _SIGNAL_TYPE,
        )
        if inserts:
            await conn.executemany(
                """
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence, metadata)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                """,
                inserts,
            )

    stats = {
        "enriched_entities": total_entities,
        "unique_items_indexed": len(index),
        "rare_items": len(rare_items),
        "pairs_evaluated": len(pair_overlap),
        "signals_emitted": len(inserts),
        "rarity_threshold": rarity,
    }
    logger.info("shared_life_context: %s", stats)
    return stats
