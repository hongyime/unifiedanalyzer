"""
Phase 6: shared helpers for media content analysis.

See docs/media_analysis_plan.md for the full spec. Used by
src/pipeline/media_analysis.py (Tier 0: 6A/6B/6C/6C.2) and
src/pipeline/media_analysis_tier1.py (Tier 1: 6D/6F/6H).
"""
import json
import logging
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

# Windows: spawn child processes (tesseract, ffmpeg, …) WITHOUT a visible
# console window. 0 on POSIX (the flag doesn't exist there). Pass this as
# creationflags= to every subprocess call in the pipeline so no stray cmd.exe
# windows pop up during analysis cycles.
NO_WINDOW_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# CPU caps (see .env "CPU / RAM CAPS"). Set BEFORE numpy/onnxruntime/cv2 are
# imported anywhere (this module is imported ahead of the cv2/numpy imports in
# media_analysis_tier1) so the native BLAS/OMP runtimes pick them up at init.
# setdefault: never override an explicit value already loaded from .env.
for _var, _default in (
    ("OMP_THREAD_LIMIT", "1"),
    ("OMP_NUM_THREADS", "2"),
    ("OPENBLAS_NUM_THREADS", "2"),
    ("MKL_NUM_THREADS", "2"),
):
    os.environ.setdefault(_var, _default)

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.pipeline.bio_mention import _normalize_mention
from src.pipeline.contact_extraction import (
    EMAIL_RE, PHONE_CANDIDATE_RE, WEBSITE_RE, _GENERIC_EMAIL_PREFIXES,
    _extract_emails, _extract_phone_numbers, _extract_platform_links,
    _extract_website_domains,
)

logger = logging.getLogger(__name__)

# media_items.file_path values look like "/media/<source>/..." — this is the
# directory that CONTAINS the "media/" folder (see .env comment).
COLLECTOR_MEDIA_ROOT = os.getenv("COLLECTOR_MEDIA_ROOT", "Z:/unifiedcollector")
_MEDIA_CONFINEMENT_ROOT = (Path(COLLECTOR_MEDIA_ROOT) / "media").resolve()

# Derived artifacts (PDF-embedded images, video frames, downloaded ONNX
# models) live entirely within unifiedanalyzer's own tree.
# Default lives on Z: — these artifacts grow without bound and must stay off the
# space-constrained C: drive. Override with MEDIA_DERIVED_PATH if needed.
MEDIA_DERIVED_PATH = Path(os.getenv("MEDIA_DERIVED_PATH", "Z:/unifiedanalyzer/media_derived")).resolve()
PDF_IMAGE_DIR = MEDIA_DERIVED_PATH / "pdf_images"
VIDEO_FRAME_DIR = MEDIA_DERIVED_PATH / "video_frames"
MODEL_DIR = MEDIA_DERIVED_PATH / "models"

for _d in (PDF_IMAGE_DIR, VIDEO_FRAME_DIR, MODEL_DIR):
    _d.mkdir(parents=True, exist_ok=True)


_DERIVED_MARKER = "media_derived/"


def resolve_media_path(file_path: str | None) -> Path | None:
    """Resolve a media file reference to an absolute path on disk.

    Two shapes are accepted:
      - collector media, e.g. "/media/search/default/image/x.jpg" — rebased
        under COLLECTOR_MEDIA_ROOT/media.
      - derived-artifact paths (PDF images, video frames written by this
        pipeline) — rebased under MEDIA_DERIVED_PATH.

    Resolution is by *content marker*, not by Path.is_absolute(), so it works
    identically on the Windows host (where the DB stores "Z:\\...\\media_derived"
    and "/media/..." paths) and inside a Linux container (where those same
    stored strings must map onto the container's bind mounts). We split each
    stored path on the "media_derived/" tail (derived) or strip its drive/leading
    slashes down to the "media/..." tail (collector), then re-root it onto the
    locally-configured directory. relative_to() still enforces confinement.

    Returns None (never raises) if the path is missing, unreadable, or escapes
    its confinement root — "graceful offline" / poisoned-path safety.
    """
    if not file_path:
        return None
    raw = str(file_path).replace("\\", "/")
    try:
        idx = raw.find(_DERIVED_MARKER)
        if idx != -1:
            # Derived artifact: keep the tail after "media_derived/".
            tail = raw[idx + len(_DERIVED_MARKER):].lstrip("/")
            full = (MEDIA_DERIVED_PATH / tail).resolve()
            full.relative_to(MEDIA_DERIVED_PATH)
        else:
            # Collector media: drop any drive letter ("z:/") + leading slashes,
            # leaving the "media/..." tail to re-root under COLLECTOR_MEDIA_ROOT.
            rel = raw.lstrip("/")
            if len(rel) >= 2 and rel[1] == ":":
                rel = rel[2:].lstrip("/")
            full = (Path(COLLECTOR_MEDIA_ROOT) / rel).resolve()
            full.relative_to(_MEDIA_CONFINEMENT_ROOT)
    except (ValueError, OSError):
        return None
    if not full.is_file():
        return None
    return full


async def build_entity_lookup() -> dict[tuple[str, str], str]:
    """(source, media_items.entity_id) -> analyzer entity_id.

    media_items.entity_id varies by source: numeric platform ids
    (telegram/github/instagram), usernames (lemon8/tiktok), WhatsApp JIDs.
    entity_platform_links covers both platform_id and platform_username per
    source, so this lookup tries both (exact and lowercased) for each link.
    Sources with no entity_platform_links rows (search, website, beeper) —
    or media items whose entity_id is a group JID / query hash / domain
    rather than a person — simply won't be found here, and are skipped by
    callers (media is still analyzed and stored, just not entity-attributed).
    """
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id, platform_username FROM entity_platform_links"
        )
    lookup: dict[tuple[str, str], str] = {}
    for l in links:
        if l["platform_id"]:
            lookup[(l["source"], l["platform_id"])] = l["entity_id"]
        if l["platform_username"]:
            lookup.setdefault((l["source"], l["platform_username"]), l["entity_id"])
            lookup.setdefault((l["source"], l["platform_username"].lower()), l["entity_id"])
    return lookup


def lookup_entity(lookup: dict[tuple[str, str], str], source: str, entity_id: str | None) -> str | None:
    if not entity_id:
        return None
    eid = lookup.get((source, entity_id))
    if eid:
        return eid
    return lookup.get((source, entity_id.lower()))


async def fetch_unprocessed_media(
    source_content_pairs: list[tuple[str | None, str]],
    analysis_type: str,
    limit: int | None = None,
) -> list[dict]:
    """media_items (collector DB) not yet analyzed for analysis_type.

    source_content_pairs is a priority-ordered list of (source, content_type)
    — source=None matches any source for that content_type. Results are
    accumulated in priority order until `limit` is reached (or all pairs
    exhausted if limit is None — full Tier 0 backfill).
    """
    analyzer = get_analyzer_pool()
    collector = get_collector_pool()

    async with analyzer.acquire() as conn:
        done_rows = await conn.fetch(
            "SELECT media_item_id FROM media_analysis WHERE analysis_type = $1 AND parent_media_item_id IS NULL",
            analysis_type,
        )
    done_ids = {r["media_item_id"] for r in done_rows}

    result: list[dict] = []
    async with collector.acquire() as conn:
        for source, content_type in source_content_pairs:
            if limit and len(result) >= limit:
                break
            if source:
                rows = await conn.fetch("""
                    SELECT id::text, source, entity_id, entity_name, content_type, file_path
                    FROM media_items WHERE source = $1 AND content_type = $2
                    ORDER BY collected_at DESC
                """, source, content_type)
            else:
                rows = await conn.fetch("""
                    SELECT id::text, source, entity_id, entity_name, content_type, file_path
                    FROM media_items WHERE content_type = $1
                    ORDER BY collected_at DESC
                """, content_type)
            for r in rows:
                if r["id"] in done_ids:
                    continue
                result.append(dict(r))
                if limit and len(result) >= limit:
                    break
    return result


async def fetch_unprocessed_derived(
    source_analysis_types: list[str],
    target_analysis_type: str,
    limit: int | None = None,
) -> list[dict]:
    """Derived media (media_analysis rows for pdf_embedded_image /
    video_frame, written by 6C.2 / 6H) not yet analyzed for
    target_analysis_type. Single-database query (analyzer only) — the
    derived "file" is a path this pipeline wrote itself under
    MEDIA_DERIVED_PATH, stored in result_json->>'derived_path'.
    """
    analyzer = get_analyzer_pool()
    query = """
        SELECT da.media_item_id AS id, da.parent_media_item_id, da.source, da.content_type,
               da.result_json
        FROM media_analysis da
        WHERE da.analysis_type = ANY($1::text[])
          AND NOT EXISTS (
              SELECT 1 FROM media_analysis x
              WHERE x.media_item_id = da.media_item_id AND x.analysis_type = $2
          )
        ORDER BY da.processed_at DESC
    """
    params: list = [source_analysis_types, target_analysis_type]
    if limit:
        query += " LIMIT $3"
        params.append(limit)

    async with analyzer.acquire() as conn:
        rows = await conn.fetch(query, *params)

    out = []
    for r in rows:
        rj = r["result_json"]
        if isinstance(rj, str):
            rj = json.loads(rj)
        rj = rj or {}
        out.append({
            "id": r["id"],
            "parent_media_item_id": r["parent_media_item_id"],
            "source": r["source"],
            "entity_id": None,
            "entity_name": None,
            "content_type": r["content_type"],
            "file_path": rj.get("derived_path"),
        })
    return out


# Max rows per executemany inside upsert_media_analysis (see chunking note there).
_UPSERT_CHUNK = 500

_MEDIA_ANALYSIS_COLUMNS = [
    ("media_item_id", "text"),
    ("parent_media_item_id", "text"),
    ("source", "varchar"),
    ("content_type", "varchar"),
    ("analysis_type", "varchar"),
    ("extracted_text", "text"),
    ("result_json", "jsonb"),
    ("gps_lat", "double precision"),
    ("gps_lon", "double precision"),
    ("taken_at", "timestamptz"),
    ("perceptual_hash", "varchar"),
    ("face_embedding", "double precision[]"),
    ("model_version", "varchar"),
]


async def upsert_media_analysis(rows: list[dict]) -> int:
    """Insert/update media_analysis rows, one per (media_item_id, analysis_type).

    Each dict may set any subset of _MEDIA_ANALYSIS_COLUMNS; unset columns are
    written as NULL. The UNIQUE(media_item_id, analysis_type) constraint
    doubles as the "already processed" cursor for resumable batching.
    """
    if not rows:
        return 0
    analyzer = get_analyzer_pool()
    names = [c for c, _ in _MEDIA_ANALYSIS_COLUMNS]
    placeholders = ", ".join(f"${i + 1}::{t}" for i, (_, t) in enumerate(_MEDIA_ANALYSIS_COLUMNS))
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in names if c not in ("media_item_id", "analysis_type"))
    query = f"""
        INSERT INTO media_analysis ({", ".join(names)}, processed_at)
        VALUES ({placeholders}, NOW())
        ON CONFLICT (media_item_id, analysis_type) DO UPDATE SET
            {set_clause}, processed_at = NOW()
    """
    values = []
    for r in rows:
        row = []
        for c, _ in _MEDIA_ANALYSIS_COLUMNS:
            v = r.get(c)
            # PostgreSQL text/jsonb cannot store NUL (0x00). Extracted PDF/OCR
            # text routinely contains stray NUL bytes; left in, one bad row
            # fails the whole executemany batch with CharacterNotInRepertoireError.
            # Strip raw 0x00 from text, and the literal backslash-u0000 escape
            # from the dumped JSON (jsonb rejects that escape even though it is
            # valid JSON syntax).
            if c == "extracted_text" and isinstance(v, str):
                v = v.replace("\x00", "")
            elif c == "result_json" and v is not None:
                v = json.dumps(v).replace("\\u0000", "")
            row.append(v)
        values.append(tuple(row))

    # Chunk the executemany. A single unbounded call (the limit=None backfill
    # can hand us tens of thousands of rows, each with large extracted_text)
    # would build one enormous bind-execute message; chunking bounds memory and
    # per-statement blast radius. asyncpg autocommits each executemany, so a
    # crash mid-loop still persists completed chunks (resumable backfill).
    async with analyzer.acquire() as conn:
        for i in range(0, len(values), _UPSERT_CHUNK):
            await conn.executemany(query, values[i:i + _UPSERT_CHUNK])
    return len(values)


# ── Contact-signal reuse (6C pdf_text, 6D ocr_text) ──
#
# Mirrors contact_extraction.py's email/phone/link/website extraction, scoped
# to text extracted FROM MEDIA (PDF text, OCR'd images) rather than bios/post
# captions. Signals are persisted with source_table='media_items' and
# source_column set to 'pdf_text' or 'ocr_text', so contact_extraction.py's
# own delete (scoped to source_table IS NULL) never clobbers these and vice
# versa.

class ContactLookups:
    __slots__ = ("pid_to_entity", "platform_username_to_entity", "phone_to_entity")

    def __init__(self, pid_to_entity, platform_username_to_entity, phone_to_entity):
        self.pid_to_entity = pid_to_entity
        self.platform_username_to_entity = platform_username_to_entity
        self.phone_to_entity = phone_to_entity


async def build_contact_lookups() -> ContactLookups:
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id, platform_username FROM entity_platform_links"
        )

    pid_to_entity = {(l["source"], l["platform_id"]): l["entity_id"] for l in links}

    platform_username_to_entity: dict[tuple[str, str], str] = {}
    for l in links:
        if l["platform_username"]:
            norm = _normalize_mention(l["platform_username"])
            if norm:
                platform_username_to_entity[(l["source"], norm)] = l["entity_id"]

    phone_to_entity: dict[str, str] = {}
    for l in links:
        if l["source"] == "whatsapp" and l["platform_id"] and l["platform_id"].endswith("@s.whatsapp.net"):
            digits = l["platform_id"].split("@", 1)[0]
            if digits.isdigit() and 9 <= len(digits) <= 15:
                phone_to_entity[digits] = l["entity_id"]

    return ContactLookups(pid_to_entity, platform_username_to_entity, phone_to_entity)


async def emit_media_contact_signals(
    entity_texts: dict[str, list[tuple[str, str]]],
    lookups: ContactLookups,
    source_column: str,
) -> dict:
    """Scan (source_platform, text) pairs per entity for emails/phones/links/
    websites, emit email_match / cross_platform_link / phone_match /
    shared_website signals with source_table='media_items',
    source_column=source_column (e.g. 'pdf_text', 'ocr_text').

    Same pairing rules as contact_extraction.extract_contacts(): email_match
    and shared_website require exactly 2 distinct entities; cross_platform_link
    and phone_match resolve a single target entity via entity_platform_links.
    """
    analyzer = get_analyzer_pool()

    email_to_entities: dict[str, set[str]] = defaultdict(set)
    domain_to_entities: dict[str, set[str]] = defaultdict(set)
    entity_links_found: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    entity_phones_found: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

    for eid, texts in entity_texts.items():
        for source_platform, text in texts:
            if not text:
                continue
            for email in _extract_emails(text):
                email_to_entities[email].add(eid)
            for domain in _extract_website_domains(text):
                domain_to_entities[domain].add(eid)
            for target_platform, handle in _extract_platform_links(text):
                target_eid = lookups.platform_username_to_entity.get((target_platform, handle))
                if target_eid and target_eid != eid:
                    entity_links_found[eid].add((source_platform, target_platform, handle, target_eid))
            for phone in _extract_phone_numbers(text):
                target_eid = lookups.phone_to_entity.get(phone)
                if target_eid and target_eid != eid:
                    entity_phones_found[eid].add((source_platform, phone, target_eid))

    new_signals: list[tuple] = []
    stats = {
        "email_match_signals": 0, "cross_platform_link_signals": 0,
        "phone_match_signals": 0, "shared_website_signals": 0,
    }

    for email, eids in email_to_entities.items():
        local_part = email.split("@", 1)[0]
        if local_part in _GENERIC_EMAIL_PREFIXES:
            continue
        if len(eids) != 2:
            continue
        a, b = sorted(eids)
        new_signals.append((a, "email_match", "multi", "media_items", source_column, None, "multi", b, email, 0.90))
        stats["email_match_signals"] += 1

    for eid, found in entity_links_found.items():
        distinct_targets = {target_eid for _, _, _, target_eid in found}
        if len(distinct_targets) > 2:
            continue
        for source_platform, target_platform, handle, target_eid in found:
            new_signals.append((
                eid, "cross_platform_link", source_platform, "media_items", source_column, None,
                target_platform, target_eid, f"{target_platform}:{handle}", 0.75,
            ))
            stats["cross_platform_link_signals"] += 1

    for eid, found in entity_phones_found.items():
        for source_platform, phone, target_eid in found:
            new_signals.append((
                eid, "phone_match", source_platform, "media_items", source_column, None,
                "whatsapp", target_eid, f"phone:{phone}", 0.90,
            ))
            stats["phone_match_signals"] += 1

    for domain, eids in domain_to_entities.items():
        if len(eids) != 2:
            continue
        a, b = sorted(eids)
        new_signals.append((a, "shared_website", "multi", "media_items", source_column, None, "multi", b, domain, 0.65))
        stats["shared_website_signals"] += 1

    async with analyzer.acquire() as conn:
        await conn.execute(
            "DELETE FROM identity_signals WHERE signal_type = ANY($1::text[]) "
            "AND source_table = 'media_items' AND source_column = $2",
            ["email_match", "cross_platform_link", "phone_match", "shared_website"],
            source_column,
        )
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)

    return stats


_UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.IGNORECASE)


async def fetch_media_item_entities(media_item_ids: list[str]) -> dict[str, tuple[str, str | None]]:
    """media_items.id -> (source, entity_id) for the given ids, from the
    collector DB. Used to resolve PARENT media items for derived rows
    (pdf_embedded_image / video_frame) when building entity-linked signals.

    Filters non-UUID inputs before the query. Phase-6 derived rows can have
    synthetic media_item_ids like '{parent_uuid}:pdf_img:{page}:{idx}' or
    40-char content-hash IDs from certain sources; those are TEXT-typed
    and would crash the ::uuid[] cast if passed through. Downstream callers
    already handle missing lookups (item_entities.get(...) returns None ->
    row skipped), so filtering out non-UUIDs is behaviour-preserving.
    """
    if not media_item_ids:
        return {}
    uuid_ids = [mid for mid in media_item_ids if mid and _UUID_RE.match(mid)]
    if not uuid_ids:
        return {}
    collector = get_collector_pool()
    async with collector.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text, source, entity_id FROM media_items WHERE id = ANY($1::uuid[])",
            uuid_ids,
        )
    return {r["id"]: (r["source"], r["entity_id"]) for r in rows}
