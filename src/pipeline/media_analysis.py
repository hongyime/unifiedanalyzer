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
import logging
import os
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


def _extract_exif_gps(path) -> tuple[float | None, float | None, datetime | None]:
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None, None, None
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
            return lat, lon, taken_at
    except Exception:
        logger.debug("EXIF read failed for %s", path, exc_info=True)
        return None, None, None


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
        lat, lon, taken_at = _extract_exif_gps(path)
        if lat is not None and lon is not None:
            stats["with_gps"] += 1
        rows.append({
            "media_item_id": item["id"], "source": item["source"],
            "content_type": item["content_type"], "analysis_type": "exif_gps",
            "gps_lat": lat, "gps_lon": lon, "taken_at": taken_at,
            "model_version": "pillow-exif-v1",
        })
    await upsert_media_analysis(rows)
    stats["processed"] = len(rows)

    stats["media_gps_colocation_signals"] = await _build_gps_colocation_signals()
    logger.info("6A EXIF GPS: %s", stats)
    return stats


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

    lookups = await build_contact_lookups()
    stats.update(await emit_media_contact_signals(entity_texts, lookups, "pdf_text"))
    logger.info("6C PDF text: %s", stats)
    return stats


# ── 6C.2: PDF embedded image extraction ──

_MIN_EMBEDDED_IMAGE_DIM = 100


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
        count = 0
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
                    derived_path.write_bytes(base["image"])
                    image_rows.append({
                        "media_item_id": f"{item['id']}:pdf_img:{page_num}:{idx}",
                        "parent_media_item_id": item["id"],
                        "source": item["source"], "content_type": "image",
                        "analysis_type": "pdf_image",
                        "result_json": {
                            "derived_path": str(derived_path), "page": page_num,
                            "idx": idx, "width": width, "height": height,
                        },
                        "model_version": "pymupdf-v1",
                    })
                    count += 1
            doc.close()
        except Exception:
            logger.debug("PDF image extraction failed for %s", path, exc_info=True)

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
