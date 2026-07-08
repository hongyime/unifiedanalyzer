"""spaCy NER enrichment for entities.

For each tracked entity, extract PERSON/ORG/GPE/LOC named entities from its
bios (across every collected platform) and its most-recent timeline event
titles, using spaCy's `en_core_web_trf` transformer model. Aggregated
counts are stored to `entities.metadata->'enrichment'` (with an
`enriched_at` timestamp) so downstream cross-entity code — see
`src/pipeline/shared_life_context.py` — can emit a `shared_life_context`
identity_signal when two entities share a RARE employer / school /
location.

Runs alongside the existing `bio_nlp` (keyword/hashtag/category) and
`contact_extraction` (emails/phones/URLs) phases. Those handle
regex-based structured extraction; this phase adds free-text named
entity recognition on top.

Failure modes handled gracefully:
- spaCy not installed:       return {"skipped": "spacy_unavailable"}.
- Model file not downloaded: return {"skipped": "model_unavailable"}.
- Neither is fatal — the container's Dockerfile.dashboard bakes the
  model in, but a dev checkout may not have it locally.

Freshness:
- An entity is re-processed when `metadata->>'enriched_at'` is NULL or
  older than 7 days. Bounded per cycle by `NER_ENTITY_BATCH_PER_RUN`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.bio_mention import BIO_SOURCES

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Config (env-tunable — see .env.example).
# --------------------------------------------------------------------------- #
def _is_enabled() -> bool:
    return os.getenv("ENTITY_ENRICHMENT_ENABLED", "1") == "1"


def _max_chars() -> int:
    return int(os.getenv("NER_MAX_CHARS_PER_ENTITY", "20000"))


def _batch_size() -> int:
    return int(os.getenv("NER_ENTITY_BATCH_PER_RUN", "100"))


def _model_name() -> str:
    return os.getenv("NER_MODEL", "en_core_web_trf")


# spaCy label -> aggregation bucket in the stored payload. ORG covers both
# employers AND schools in spaCy's ontology (it doesn't distinguish), so we
# store them in one bucket named accordingly for downstream shared_life_context.
_LABEL_BUCKETS: dict[str, str] = {
    "PERSON": "persons_mentioned",
    "ORG": "employers_schools",
    "GPE": "locations",   # countries, cities, states
    "LOC": "locations",   # non-GPE locations (mountains, rivers, etc.)
}


# --------------------------------------------------------------------------- #
# spaCy singleton — loaded lazily on first use; cached across cycles.
# Sentinels: None = not yet attempted, False = tried and unavailable.
# --------------------------------------------------------------------------- #
_nlp: object | None | bool = None


def _load_spacy() -> tuple[object | None, str | None]:
    """Load spaCy once. Returns (nlp, error). error is None on success,
    'spacy_unavailable' if the package isn't installed, or 'model_unavailable'
    if the model isn't downloaded."""
    global _nlp
    if _nlp is False:
        return None, "spacy_unavailable"
    if isinstance(_nlp, bool):  # cached model-missing sentinel
        return None, "model_unavailable"
    if _nlp is not None:
        return _nlp, None

    try:
        import spacy  # type: ignore
    except ImportError:
        logger.warning("entity_enrichment: `spacy` not installed; skipping NER")
        _nlp = False
        return None, "spacy_unavailable"

    model = _model_name()
    try:
        _nlp = spacy.load(model)  # type: ignore[assignment]
        logger.info("entity_enrichment: loaded spaCy model %s", model)
        return _nlp, None
    except OSError:
        # Model not downloaded. In production the Dockerfile bakes it in via
        # `python -m spacy download en_core_web_trf`; in dev, run that once.
        logger.warning(
            "entity_enrichment: spaCy model %s not available; "
            "run `python -m spacy download %s`",
            model, model,
        )
        # Use a distinct truthy sentinel so we don't retry every cycle.
        _nlp = True  # type: ignore[assignment]
        return None, "model_unavailable"
    except Exception:
        logger.exception("entity_enrichment: unexpected error loading spaCy model %s", model)
        _nlp = False
        return None, "spacy_unavailable"


# --------------------------------------------------------------------------- #
# NER work — pure, sync, wrapped in asyncio.to_thread by the caller.
# --------------------------------------------------------------------------- #
def _run_ner(nlp, text: str) -> dict[str, dict[str, int]]:
    """Run spaCy NER over one entity's concatenated text and aggregate label
    counts. Returns {bucket: {surface: count}} where bucket is one of
    persons_mentioned / employers_schools / locations."""
    buckets: dict[str, dict[str, int]] = {
        "persons_mentioned": {},
        "employers_schools": {},
        "locations": {},
    }
    if not text.strip():
        return buckets
    doc = nlp(text)
    for ent in doc.ents:
        bucket = _LABEL_BUCKETS.get(ent.label_)
        if not bucket:
            continue
        # Normalize surface form: collapse whitespace, strip control chars.
        surface = " ".join(ent.text.split()).strip()
        if not surface or len(surface) > 200:
            continue
        buckets[bucket][surface] = buckets[bucket].get(surface, 0) + 1
    return buckets


def _sort_bucket(counts: dict[str, int], top_n: int = 50) -> list[dict]:
    """Frequency-sorted list capped at top_n for compact storage."""
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
    return [{"text": t, "count": c} for t, c in ranked]


# --------------------------------------------------------------------------- #
# Collector-DB fan-out for bios (matches bio_nlp.analyze_bios pattern).
# Loaded once per cycle since one query per platform is far cheaper than one
# query per (entity, platform).
# --------------------------------------------------------------------------- #
async def _load_platform_bios() -> dict[tuple[str, str], str]:
    """(source, platform_id) -> bio text. Same source set as bio_nlp so we
    stay consistent with the rest of the pipeline. Note: the task spec
    mentioned `entity_platform_links.metadata.bio` — that column does not
    exist in the current schema (see src/db/schema.sql :19-37), so bios are
    fetched from the collector per-platform tables via BIO_SOURCES, which is
    where they actually live. If EPL.metadata is ever added with a bio field,
    add a merge step here."""
    collector = get_collector_pool()
    out: dict[tuple[str, str], str] = {}
    async with collector.acquire() as conn:
        for source, query in BIO_SOURCES:
            try:
                rows = await conn.fetch(query)
            except Exception:
                logger.debug("entity_enrichment: skipping bio source %s", source, exc_info=True)
                continue
            for r in rows:
                bio = r["bio"]
                if bio:
                    out[(source, str(r["pid"]))] = bio
    return out


# --------------------------------------------------------------------------- #
# Public entry point (wired into incremental_runner._secondary_phases).
# --------------------------------------------------------------------------- #
async def enrich_entities_with_ner() -> dict:
    if not _is_enabled():
        return {"skipped": "disabled"}

    nlp, err = _load_spacy()
    if nlp is None:
        return {"skipped": err or "spacy_unavailable"}

    max_chars = _max_chars()
    batch = _batch_size()
    analyzer = get_analyzer_pool()

    # Pull entities needing enrichment: never enriched, or stale (>7d).
    async with analyzer.acquire() as conn:
        due_rows = await conn.fetch(
            """
            SELECT id::text AS id FROM entities
            WHERE metadata->>'enriched_at' IS NULL
               OR (metadata->>'enriched_at')::timestamptz < NOW() - INTERVAL '7 days'
            ORDER BY (metadata->>'enriched_at') NULLS FIRST, id
            LIMIT $1
            """,
            batch,
        )
        due_ids: list[str] = [r["id"] for r in due_rows]

        # entity -> [(source, platform_id), ...] for bio lookup.
        link_rows = await conn.fetch(
            """
            SELECT entity_id::text AS eid, source, platform_id
            FROM entity_platform_links
            WHERE entity_id = ANY($1::uuid[])
            """,
            due_ids,
        ) if due_ids else []
        links_by_entity: dict[str, list[tuple[str, str]]] = {}
        for r in link_rows:
            links_by_entity.setdefault(r["eid"], []).append((r["source"], r["platform_id"]))

    if not due_ids:
        return {"entities_scanned": 0, "entities_enriched": 0,
                "persons_extracted": 0, "orgs_extracted": 0, "locations_extracted": 0}

    platform_bios = await _load_platform_bios()

    stats = {
        "entities_scanned": len(due_ids),
        "entities_enriched": 0,
        "persons_extracted": 0,
        "orgs_extracted": 0,
        "locations_extracted": 0,
    }

    for eid in due_ids:
        # Recent timeline event titles.
        async with analyzer.acquire() as conn:
            title_rows = await conn.fetch(
                """
                SELECT title FROM timeline_events
                WHERE entity_id = $1::uuid AND title IS NOT NULL AND title <> ''
                ORDER BY occurred_at DESC
                LIMIT 200
                """,
                eid,
            )

        # Bios for this entity, deduped in insertion order.
        bio_parts: list[str] = []
        for source, pid in links_by_entity.get(eid, []):
            bio = platform_bios.get((source, pid))
            if bio:
                bio_parts.append(bio)

        title_parts = [r["title"] for r in title_rows if r["title"]]

        combined = "\n".join(bio_parts + title_parts)
        if not combined.strip():
            # Still stamp enriched_at so we don't re-scan an empty entity every cycle.
            payload: dict = {
                "persons_mentioned": [],
                "employers_schools": [],
                "locations": [],
                "text_chars": 0,
                "n_bios": len(bio_parts),
                "n_titles": len(title_parts),
                "model": _model_name(),
            }
        else:
            text = combined[:max_chars]
            try:
                buckets = await asyncio.to_thread(_run_ner, nlp, text)
            except Exception:
                logger.exception("entity_enrichment: NER failed for entity %s", eid)
                continue

            payload = {
                "persons_mentioned": _sort_bucket(buckets["persons_mentioned"]),
                "employers_schools": _sort_bucket(buckets["employers_schools"]),
                "locations": _sort_bucket(buckets["locations"]),
                "text_chars": len(text),
                "n_bios": len(bio_parts),
                "n_titles": len(title_parts),
                "model": _model_name(),
            }
            stats["persons_extracted"] += sum(v for v in buckets["persons_mentioned"].values())
            stats["orgs_extracted"] += sum(v for v in buckets["employers_schools"].values())
            stats["locations_extracted"] += sum(v for v in buckets["locations"].values())

        async with analyzer.acquire() as conn:
            await conn.execute(
                """
                UPDATE entities
                SET metadata = COALESCE(metadata, '{}'::jsonb)
                    || jsonb_build_object('enrichment', $1::jsonb, 'enriched_at', to_jsonb(NOW())),
                    updated_at = NOW()
                WHERE id = $2::uuid
                """,
                json.dumps(payload, default=str),
                eid,
            )
        stats["entities_enriched"] += 1

    logger.info("entity_enrichment: %s", stats)
    return stats
