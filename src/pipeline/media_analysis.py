"""
Phase 6, Tier 0 — media content analysis requiring no ML models, safe to
backfill the entire 120k-item backlog in one sitting.

  6A  analyze_media_exif()   — EXIF GPS + capture time -> media_gps_colocation
  6B  analyze_media_phash()  — perceptual hash -> media_perceptual_match
  6C  analyze_media_pdf_text()  — PDF text -> email_match/phone_match/
                                   cross_platform_link/shared_website reuse
  6C.2 extract_pdf_images()  — PDF embedded images, feeds 6B/6D/6F

See docs/media_analysis_plan.md for the full spec. Tier 1 (6D/6F/6H) lives in
src/pipeline/media_analysis_tier1.py.
"""
import json
import logging
import os
import concurrent.futures
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import fitz  # PyMuPDF
import imagehash
import pypdf
from PIL import ExifTags, Image

from src.db.connection import get_analyzer_pool
from src.pipeline.media_common import (
    PDF_IMAGE_DIR,
    _MEDIA_CONFINEMENT_ROOT,
    build_contact_lookups,
    build_entity_lookup,
    emit_media_contact_signals,
    fetch_media_item_entities,
    fetch_unprocessed_derived,
    fetch_unprocessed_media,
    lookup_entity,
    resolve_media_path,
    upsert_media_analysis,
)

logger = logging.getLogger(__name__)

# Per-incremental-cycle batch sizes (incremental_runner.py passes these
# explicitly). A bare `limit=None` call (used by the one-off Tier 0 backfill
# script) processes the entire remaining backlog.
MEDIA_EXIF_BATCH_SIZE = int(os.getenv("MEDIA_EXIF_BATCH_SIZE", "500"))
MEDIA_PHASH_BATCH_SIZE = int(os.getenv("MEDIA_PHASH_BATCH_SIZE", "500"))
MEDIA_PDF_TEXT_BATCH_SIZE = int(os.getenv("MEDIA_PDF_TEXT_BATCH_SIZE", "50"))
MEDIA_PDF_IMAGE_BATCH_SIZE = int(os.getenv("MEDIA_PDF_IMAGE_BATCH_SIZE", "50"))


def _drive_available() -> bool:
    return _MEDIA_CONFINEMENT_ROOT.is_dir()


# ── 6A: EXIF GPS extraction ──

_EXIF_CONTENT_TYPES = [(None, "image"), (None, "photo"), (None, "activity_photo")]
_GPS_CLUSTER_PRECISION = 3  # ~111m grid, same as route_similarity.py
_GPS_TIME_WINDOW = timedelta(hours=24)


def _dms_to_decimal(dms, ref) -> float | None:
    try:
        deg, minutes, seconds = dms
        decimal = float(deg) + float(minutes) / 60.0 + float(seconds) / 3600.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


# P2-7 device fingerprint EXIF tags. A SERIAL number identifies a *physical*
# camera body/lens — same serial across two accounts' media is strong same-owner
# evidence. Make+Model alone (e.g. "Apple iPhone 13") is NOT distinctive, so a
# fingerprint is only emitted when a serial is present.
_EXIF_MAKE = 271          # 0x010F
_EXIF_MODEL = 272         # 0x0110
_EXIF_BODY_SERIAL = 42033  # 0xA431 BodySerialNumber
_EXIF_LENS_MODEL = 42036   # 0xA434 LensModel
_EXIF_LENS_SERIAL = 42037  # 0xA435 LensSerialNumber


def _extract_exif_device(exif, exif_ifd) -> dict | None:
    """Build a device fingerprint dict from EXIF, or None if no serial number is
    present (Make+Model alone is too common to be identity evidence)."""
    make = exif.get(_EXIF_MAKE)
    model = exif.get(_EXIF_MODEL)
    body_serial = exif_ifd.get(_EXIF_BODY_SERIAL)
    lens_serial = exif_ifd.get(_EXIF_LENS_SERIAL)
    lens_model = exif_ifd.get(_EXIF_LENS_MODEL)

    def _clean(v):
        s = str(v).strip() if v is not None else ""
        return s or None

    body_serial, lens_serial = _clean(body_serial), _clean(lens_serial)
    if not body_serial and not lens_serial:
        return None  # no physical-camera identifier -> not distinctive enough
    return {
        "make": _clean(make), "model": _clean(model),
        "lens_model": _clean(lens_model),
        "body_serial": body_serial, "lens_serial": lens_serial,
    }


def _extract_exif_gps(path) -> tuple[float | None, float | None, datetime | None, dict | None]:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None, None, None, None
            lat = lon = None
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
            if gps:
                lat_dms, lat_ref = gps.get(2), gps.get(1)
                lon_dms, lon_ref = gps.get(4), gps.get(3)
                if lat_dms and lat_ref:
                    lat = _dms_to_decimal(lat_dms, lat_ref)
                if lon_dms and lon_ref:
                    lon = _dms_to_decimal(lon_dms, lon_ref)
            taken_at = None
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            dt_str = exif_ifd.get(36867) or exif.get(306)  # DateTimeOriginal / DateTime
            if dt_str:
                try:
                    taken_at = datetime.strptime(str(dt_str), "%Y:%m:%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    taken_at = None
            device = _extract_exif_device(exif, exif_ifd)
            return lat, lon, taken_at, device
    except Exception:
        logger.debug("EXIF read failed for %s", path, exc_info=True)
        return None, None, None, None


async def analyze_media_exif(limit: int | None = None) -> dict:
    stats = {"processed": 0, "with_gps": 0, "media_gps_colocation_signals": 0}
    if not _drive_available():
        logger.warning("6A EXIF GPS: media drive unavailable, skipping")
        return {**stats, "skipped": "drive_unavailable"}

    items = await fetch_unprocessed_media(_EXIF_CONTENT_TYPES, "exif_gps", limit=limit)
    rows = []
    for item in items:
        path = resolve_media_path(item["file_path"])
        if path is None:
            continue  # missing/unreadable — leave unprocessed, retry next run
        lat, lon, taken_at, device = _extract_exif_gps(path)
        if lat is not None and lon is not None:
            stats["with_gps"] += 1
        if device:
            stats["with_device"] = stats.get("with_device", 0) + 1
        rows.append({
            "media_item_id": item["id"], "source": item["source"],
            "content_type": item["content_type"], "analysis_type": "exif_gps",
            "gps_lat": lat, "gps_lon": lon, "taken_at": taken_at,
            # P2-7: store the device fingerprint alongside GPS in result_json.
            "result_json": {"device": device} if device else None,
            "model_version": "pillow-exif-v2",
        })
    await upsert_media_analysis(rows)
    stats["processed"] = len(rows)

    # Idle-cycle skip: no new EXIF rows -> clusters unchanged (CPU saver).
    if stats["processed"]:
        stats["media_gps_colocation_signals"] = await _build_gps_colocation_signals()
        stats["media_device_match_signals"] = await _build_device_match_signals()
    logger.info("6A EXIF GPS: %s", stats)
    return stats


async def backfill_exif_device(limit: int = 500) -> dict:
    """P2-7 retroactive: re-read EXIF from disk for already-processed (v1) images
    that have no device fingerprint yet, add it to result_json, and rebuild the
    media_device_match signal. Bounded per call for resumable trickling.

    NOTE: on a corpus of social-media images (EXIF largely stripped) the yield is
    ~0 — worth running mainly once drive-original media (which keeps EXIF) flows
    through this pipeline. Reads pixels/metadata from disk, so needs the media
    drive mounted (_drive_available)."""
    stats = {"scanned": 0, "updated": 0, "with_device": 0}
    if not _drive_available():
        return {**stats, "skipped": "drive_unavailable"}

    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        rows = await conn.fetch("""
            SELECT media_item_id, source, content_type
            FROM media_analysis
            WHERE analysis_type = 'exif_gps'
              AND (result_json -> 'device') IS NULL
              AND model_version <> 'pillow-exif-v2'
            LIMIT $1
        """, limit)
    if not rows:
        return {**stats, "done": True}

    # Resolve file paths for these media items from the collector DB.
    ids = [r["media_item_id"] for r in rows]
    paths = await _fetch_media_paths(ids)

    out_rows = []
    for r in rows:
        stats["scanned"] += 1
        fp = paths.get(r["media_item_id"])
        disk = resolve_media_path(fp) if fp else None
        if disk is None:
            continue
        lat, lon, taken_at, device = _extract_exif_gps(disk)
        if device:
            stats["with_device"] += 1
        out_rows.append({
            "media_item_id": r["media_item_id"], "source": r["source"],
            "content_type": r["content_type"], "analysis_type": "exif_gps",
            "gps_lat": lat, "gps_lon": lon, "taken_at": taken_at,
            "result_json": {"device": device} if device else None,
            "model_version": "pillow-exif-v2",
        })
    if out_rows:
        await upsert_media_analysis(out_rows)
        stats["updated"] = len(out_rows)
        stats["media_device_match_signals"] = await _build_device_match_signals()
    return stats


async def _fetch_media_paths(media_item_ids: list[str]) -> dict[str, str]:
    """{media_item_id: file_path} from the collector DB for the given ids."""
    from src.db.connection import get_collector_pool
    if not media_item_ids:
        return {}
    collector = get_collector_pool()
    async with collector.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, file_path FROM media_items WHERE id = ANY($1::uuid[])",
            media_item_ids,
        )
    return {r["id"]: r["file_path"] for r in rows if r["file_path"]}


async def _build_device_match_signals() -> int:
    """P2-7: emit `media_device_match` when two DIFFERENT entities have media from
    the same physical camera (identical body/lens serial). Fan-out filtered
    (exactly 2 entities) and cross-platform, mirroring the GPS/pHash builders."""
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        dev_rows = await conn.fetch("""
            SELECT media_item_id, parent_media_item_id, result_json
            FROM media_analysis
            WHERE analysis_type = 'exif_gps'
              AND result_json IS NOT NULL
              AND result_json -> 'device' IS NOT NULL
        """)

    new_signals: list[tuple] = []
    if dev_rows:
        real_ids = list({r["parent_media_item_id"] or r["media_item_id"] for r in dev_rows})
        item_entities = await fetch_media_item_entities(real_ids)
        entity_lookup = await build_entity_lookup()

        # fingerprint -> [(eid, source, media_item_id), ...]
        buckets: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for r in dev_rows:
            raw = r["result_json"]
            dev = (raw if isinstance(raw, dict) else json.loads(raw)).get("device") or {}
            serial = dev.get("body_serial") or dev.get("lens_serial")
            if not serial:
                continue
            # Fingerprint keyed on the physical-camera serial (+ make/model for
            # readability). Serial alone already identifies the body/lens.
            fp = "|".join(str(dev.get(k) or "") for k in ("make", "model", "body_serial", "lens_serial"))
            real_id = r["parent_media_item_id"] or r["media_item_id"]
            src = item_entities.get(real_id)
            if not src:
                continue
            source, raw_eid = src
            eid = lookup_entity(entity_lookup, source, raw_eid)
            if not eid:
                continue
            buckets[fp].append((eid, source, r["media_item_id"]))

        for fp, entries in buckets.items():
            distinct_entities = {eid for eid, _, _ in entries}
            if len(distinct_entities) != 2:
                continue  # fan-out filter: a shared serial across >2 = suspect
            a_eid, b_eid = sorted(distinct_entities)
            # require the two entities to be on different platforms
            a_srcs = {src for eid, src, _ in entries if eid == a_eid}
            b_srcs = {src for eid, src, _ in entries if eid == b_eid}
            if a_srcs == b_srcs and len(a_srcs) == 1:
                continue  # same single platform — not cross-identity evidence
            a_mid = next(m for eid, _, m in entries if eid == a_eid)
            new_signals.append((
                a_eid, "media_device_match", "multi", "media_items", "exif_device", a_mid,
                "multi", b_eid, fp, 0.65,
            ))

    async with analyzer.acquire() as conn:
        await conn.execute("DELETE FROM identity_signals WHERE signal_type = 'media_device_match'")
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)
    return len(new_signals)


async def _build_gps_colocation_signals() -> int:
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        gps_rows = await conn.fetch("""
            SELECT media_item_id, gps_lat, gps_lon, taken_at
            FROM media_analysis
            WHERE analysis_type = 'exif_gps' AND gps_lat IS NOT NULL AND gps_lon IS NOT NULL
        """)

    new_signals: list[tuple] = []
    if gps_rows:
        item_entities = await fetch_media_item_entities([r["media_item_id"] for r in gps_rows])
        entity_lookup = await build_entity_lookup()

        cluster_points: dict[tuple[float, float], list[tuple[str, datetime | None, str]]] = defaultdict(list)
        for r in gps_rows:
            src = item_entities.get(r["media_item_id"])
            if not src:
                continue
            source, raw_eid = src
            eid = lookup_entity(entity_lookup, source, raw_eid)
            if not eid:
                continue
            cluster = (round(r["gps_lat"], _GPS_CLUSTER_PRECISION), round(r["gps_lon"], _GPS_CLUSTER_PRECISION))
            cluster_points[cluster].append((eid, r["taken_at"], r["media_item_id"]))

        for cluster, points in cluster_points.items():
            distinct_entities = {eid for eid, _, _ in points}
            if len(distinct_entities) != 2:
                continue  # fan-out filter: >2 = public place, <2 = no cross-entity match
            a_eid, b_eid = sorted(distinct_entities)
            a_points = [(t, m) for eid, t, m in points if eid == a_eid]
            b_points = [(t, m) for eid, t, m in points if eid == b_eid]
            match = None
            for a_t, a_m in a_points:
                for b_t, b_m in b_points:
                    if a_t and b_t and abs(a_t - b_t) <= _GPS_TIME_WINDOW:
                        match = (a_m, b_m)
                        break
                if match:
                    break
            if not match:
                continue
            lat, lon = cluster
            new_signals.append((
                a_eid, "media_gps_colocation", "multi", "media_items", "exif_gps", match[0],
                "multi", b_eid, f"{lat},{lon}", 0.70,
            ))

    async with analyzer.acquire() as conn:
        await conn.execute("DELETE FROM identity_signals WHERE signal_type = 'media_gps_colocation'")
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)
    return len(new_signals)


# ── 6B: perceptual hashing ──

_PHASH_CONTENT_TYPES = [
    (None, "image"), (None, "photo"), (None, "profile_photo"),
    (None, "activity_photo"), (None, "sticker"), (None, "thumbnail"), (None, "post"),
]
_PHASH_DISTANCE_THRESHOLD = 6


def _hamming(h1: str, h2: str) -> int:
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except (ValueError, TypeError):
        return 999


async def analyze_media_phash(limit: int | None = None) -> dict:
    stats = {"processed": 0, "media_perceptual_match_signals": 0}
    if not _drive_available():
        logger.warning("6B perceptual hash: media drive unavailable, skipping")
        return {**stats, "skipped": "drive_unavailable"}

    items = await fetch_unprocessed_media(_PHASH_CONTENT_TYPES, "phash", limit=limit)
    items += await fetch_unprocessed_derived(["pdf_image", "video_frame"], "phash", limit=limit)

    rows = []
    for item in items:
        path = resolve_media_path(item["file_path"])
        if path is None:
            continue
        try:
            with Image.open(path) as img:
                phash = imagehash.phash(img)
        except Exception:
            logger.debug("phash failed for %s", path, exc_info=True)
            continue
        rows.append({
            "media_item_id": item["id"], "parent_media_item_id": item.get("parent_media_item_id"),
            "source": item["source"], "content_type": item["content_type"],
            "analysis_type": "phash", "perceptual_hash": str(phash),
            "model_version": "imagehash-phash-v1",
        })
    await upsert_media_analysis(rows)
    stats["processed"] = len(rows)

    # Idle-cycle skip: no new hashes -> match buckets unchanged (CPU saver).
    if stats["processed"]:
        stats["media_perceptual_match_signals"] = await _build_perceptual_match_signals()
    logger.info("6B perceptual hash: %s", stats)
    return stats


async def _build_perceptual_match_signals() -> int:
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        hash_rows = await conn.fetch("""
            SELECT media_item_id, parent_media_item_id, perceptual_hash
            FROM media_analysis
            WHERE analysis_type = 'phash' AND perceptual_hash IS NOT NULL
        """)

    new_signals: list[tuple] = []
    if hash_rows:
        real_ids = list({r["parent_media_item_id"] or r["media_item_id"] for r in hash_rows})
        item_entities = await fetch_media_item_entities(real_ids)
        entity_lookup = await build_entity_lookup()

        # top-16-bit bucket -> [(eid, source_platform, hash, media_item_id), ...]
        buckets: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
        for r in hash_rows:
            real_id = r["parent_media_item_id"] or r["media_item_id"]
            src = item_entities.get(real_id)
            if not src:
                continue
            source, raw_eid = src
            eid = lookup_entity(entity_lookup, source, raw_eid)
            if not eid:
                continue
            h = r["perceptual_hash"]
            if not h or len(h) < 4:
                continue
            buckets[h[:4]].append((eid, source, h, r["media_item_id"]))

        for entries in buckets.values():
            distinct_entities = {eid for eid, _, _, _ in entries}
            if len(distinct_entities) != 2:
                continue  # fan-out filter: shared by >2 entities = stock/meme image
            a_eid, b_eid = sorted(distinct_entities)
            a_entries = [(src, h, mid) for eid, src, h, mid in entries if eid == a_eid]
            b_entries = [(src, h, mid) for eid, src, h, mid in entries if eid == b_eid]
            match = None
            for a_src, a_hash, a_mid in a_entries:
                for b_src, b_hash, b_mid in b_entries:
                    if a_src == b_src:
                        continue  # same-platform near-dupes aren't cross-identity evidence
                    if _hamming(a_hash, b_hash) <= _PHASH_DISTANCE_THRESHOLD:
                        match = (a_hash, a_mid, b_mid)
                        break
                if match:
                    break
            if not match:
                continue
            new_signals.append((
                a_eid, "media_perceptual_match", "multi", "media_items", "phash", match[1],
                "multi", b_eid, match[0], 0.65,
            ))

    async with analyzer.acquire() as conn:
        await conn.execute("DELETE FROM identity_signals WHERE signal_type = 'media_perceptual_match'")
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)
    return len(new_signals)


# ── 6C: PDF text extraction ──

_PDF_CONTENT_TYPES = [(None, "pdf")]
_MAX_PDF_TEXT_LEN = 200_000


async def analyze_media_pdf_text(limit: int | None = None) -> dict:
    stats: dict = {"processed": 0}
    if not _drive_available():
        logger.warning("6C PDF text: media drive unavailable, skipping")
        return {**stats, "skipped": "drive_unavailable"}

    items = await fetch_unprocessed_media(_PDF_CONTENT_TYPES, "pdf_text", limit=limit)
    entity_lookup = await build_entity_lookup()
    entity_texts: dict[str, list[tuple[str, str]]] = defaultdict(list)

    rows = []
    for item in items:
        path = resolve_media_path(item["file_path"])
        if path is None:
            continue
        text = ""
        try:
            reader = pypdf.PdfReader(str(path))
            parts, total = [], 0
            for page in reader.pages:
                t = page.extract_text() or ""
                parts.append(t)
                total += len(t)
                if total >= _MAX_PDF_TEXT_LEN:
                    break
            text = "\n".join(parts)[:_MAX_PDF_TEXT_LEN]
        except Exception:
            logger.debug("PDF text extraction failed for %s", path, exc_info=True)

        rows.append({
            "media_item_id": item["id"], "source": item["source"],
            "content_type": item["content_type"], "analysis_type": "pdf_text",
            "extracted_text": text or None, "model_version": "pypdf-v1",
        })
        if text:
            eid = lookup_entity(entity_lookup, item["source"], item["entity_id"])
            if eid:
                entity_texts[eid].append((item["source"], text))

    await upsert_media_analysis(rows)
    stats["processed"] = len(rows)

    # Idle-cycle skip: nothing new this cycle -> leave existing pdf_text contact
    # signals untouched (also avoids the needless delete+reinsert).
    # TODO(continuing agent): emit_media_contact_signals rebuilds from THIS
    # batch's entity_texts only, so steady-state cycles overwrite prior pdf_text
    # signals rather than accumulating. Fine during a full backfill (one pass);
    # if incremental coverage matters, rescan all pdf_text rows here instead.
    if stats["processed"]:
        lookups = await build_contact_lookups()
        stats.update(await emit_media_contact_signals(entity_texts, lookups, "pdf_text"))
    logger.info("6C PDF text: %s", stats)
    return stats


# ── 6C.2: PDF embedded image extraction ──

_MIN_EMBEDDED_IMAGE_DIM = 100


# Embedded-image bytes are written to the derived dir, which on the dockerized
# stack is a Windows bind-mount (Z:) through Docker Desktop's WSL2 layer — slow
# PER FILE (~200ms+), and a single PDF can yield 100s of images. doc.extract_image
# returns the already-stored bytes (no decode), so the write latency dominates.
# Dispatch the independent writes to a small thread pool so that latency overlaps
# (~4x measured). Writes still go to Z: only — nothing is written into the
# container's own (C:-backed) layer, so the docker image/vhdx does not grow.
_PDF_WRITE_WORKERS = int(os.getenv("MEDIA_PDF_WRITE_WORKERS", "8"))
_pdf_write_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=_PDF_WRITE_WORKERS, thread_name_prefix="pdf-img-write"
)


async def extract_pdf_images(limit: int | None = None) -> dict:
    stats = {"pdfs_processed": 0, "images_extracted": 0}
    if not _drive_available():
        logger.warning("6C.2 PDF images: media drive unavailable, skipping")
        return {**stats, "skipped": "drive_unavailable"}

    items = await fetch_unprocessed_media(_PDF_CONTENT_TYPES, "pdf_embedded_image", limit=limit)
    marker_rows, image_rows = [], []

    for item in items:
        path = resolve_media_path(item["file_path"])
        if path is None:
            continue
        # (write-future, image_row) pairs — the row is recorded only once its file
        # has actually landed, so DB rows never reference a missing file.
        pending: list[tuple[concurrent.futures.Future, dict]] = []
        try:
            doc = fitz.open(str(path))
            for page_num in range(len(doc)):
                page = doc[page_num]
                for idx, img_info in enumerate(page.get_images(full=True)):
                    xref, width, height = img_info[0], img_info[2], img_info[3]
                    if width < _MIN_EMBEDDED_IMAGE_DIM or height < _MIN_EMBEDDED_IMAGE_DIM:
                        continue
                    try:
                        base = doc.extract_image(xref)
                    except Exception:
                        continue
                    ext = base.get("ext", "png")
                    derived_path = PDF_IMAGE_DIR / f"{item['id']}_{page_num}_{idx}.{ext}"
                    fut = _pdf_write_pool.submit(derived_path.write_bytes, base["image"])
                    pending.append((fut, {
                        "media_item_id": f"{item['id']}:pdf_img:{page_num}:{idx}",
                        "parent_media_item_id": item["id"],
                        "source": item["source"], "content_type": "image",
                        "analysis_type": "pdf_image",
                        "result_json": {
                            "derived_path": str(derived_path), "page": page_num,
                            "idx": idx, "width": width, "height": height,
                        },
                        "model_version": "pymupdf-v1",
                    }))
            doc.close()
        except Exception:
            logger.debug("PDF image extraction failed for %s", path, exc_info=True)

        # Drain this PDF's writes before recording its marker. Only count images
        # whose write succeeded.
        count = 0
        for fut, row in pending:
            try:
                fut.result()
            except Exception:
                logger.debug("PDF image write failed for %s", row["result_json"]["derived_path"], exc_info=True)
                continue
            image_rows.append(row)
            count += 1

        # Marker row on the PDF's own id — done-cursor for fetch_unprocessed_media.
        # Per-image rows above (analysis_type='pdf_image') are what 6B/6D/6F pick up
        # via fetch_unprocessed_derived.
        marker_rows.append({
            "media_item_id": item["id"], "source": item["source"],
            "content_type": item["content_type"], "analysis_type": "pdf_embedded_image",
            "result_json": {"image_count": count}, "model_version": "pymupdf-v1",
        })
        stats["images_extracted"] += count

    await upsert_media_analysis(image_rows)
    await upsert_media_analysis(marker_rows)
    stats["pdfs_processed"] = len(marker_rows)
    logger.info("6C.2 PDF embedded images: %s", stats)
    return stats
