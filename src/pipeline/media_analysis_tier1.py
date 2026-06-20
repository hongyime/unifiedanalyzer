"""
Phase 6, Tier 1 — media content analysis requiring OCR / ML models. Lower
priority than Tier 0 (src/pipeline/media_analysis.py) since these are slower
per-item and/or depend on optional binaries (tesseract, ffmpeg) or the
downloaded ONNX face models.

  6D  analyze_media_ocr()     — Tesseract OCR -> email_match/phone_match/
                                  cross_platform_link/shared_website reuse
  6F  analyze_media_faces()   — YuNet detection + SFace embeddings (feed the
                                  media gallery "has_face" filter). The
                                  media_face_match identity signal is built by
                                  _build_face_match_signals() from facetracker's
                                  InsightFace/ArcFace 512-dim corpus (F3).
  6H  extract_video_frames()  — ffmpeg sparse keyframes (personal-comms
                                  video only), feeds 6B/6D/6F

See docs/media_analysis_plan.md for the full spec.
"""
import asyncio
import json
import logging
import io
import os
import shutil
import subprocess
from collections import defaultdict

import cv2
import numpy as np
from PIL import Image

# Cap OpenCV's internal thread pool (DNN face detect/embed) so it doesn't peg
# every core. Runtime call — independent of env-var import timing. media_common
# (imported below) already set OMP/BLAS caps for numpy/onnxruntime.
cv2.setNumThreads(int(os.getenv("MEDIA_CV_NUM_THREADS", "2")))

from src.db.connection import get_analyzer_pool
from src.pipeline.media_common import (
    MODEL_DIR,
    NO_WINDOW_FLAGS,
    VIDEO_FRAME_DIR,
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

MEDIA_OCR_ENABLED = os.getenv("MEDIA_OCR_ENABLED", "true").lower() == "true"
MEDIA_OCR_BATCH_SIZE = int(os.getenv("MEDIA_OCR_BATCH_SIZE", "200"))
MEDIA_FACE_BATCH_SIZE = int(os.getenv("MEDIA_FACE_BATCH_SIZE", "200"))
MEDIA_VIDEO_FRAME_BATCH_SIZE = int(os.getenv("MEDIA_VIDEO_FRAME_BATCH_SIZE", "5"))
# Ceiling on embeddings loaded/compared per face-match rebuild — bounds RAM and
# CPU as the embedding corpus grows (most-recent-first). See .env.
MEDIA_FACE_MATCH_MAX = int(os.getenv("MEDIA_FACE_MATCH_MAX", "20000"))


def _drive_available() -> bool:
    return _MEDIA_CONFINEMENT_ROOT.is_dir()


# ── 6D: OCR text extraction ──

# Priority order per docs/media_analysis_plan.md: high text-density sources
# first (search/website screenshots, telegram photos), then PDF-embedded
# images / video frames from 6C.2 / 6H, then everything else (lemon8 last —
# largest volume, lowest signal density). Each list below has no overlapping
# (source, content_type) pairs — fetch_unprocessed_media's done-id check is
# only correct within a single call when pairs don't overlap.
_OCR_PRIORITY_1 = [("search", "image"), ("website", "image"), ("telegram", "photo")]
_OCR_PRIORITY_2 = [
    (None, "profile_photo"), ("beeper", "image"), ("whatsapp", "photo"),
    ("tiktok", "post"), ("github", "image"), ("instagram", "image"), ("lemon8", "image"),
]
_OCR_MAX_DIM = 2000
_MAX_OCR_TEXT_LEN = 50_000


def _ocr_image(path) -> str:
    # Call tesseract directly (stdin->stdout) instead of via pytesseract: on
    # Windows pytesseract spawns a visible console window per call (dozens per
    # cycle) with no way to suppress it. Driving the binary ourselves lets us
    # pass CREATE_NO_WINDOW. Same default OCR behaviour (psm 3, eng).
    with Image.open(path) as img:
        img = img.convert("RGB")
        if max(img.size) > _OCR_MAX_DIM:
            ratio = _OCR_MAX_DIM / max(img.size)
            size = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
            img = img.resize(size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
    result = subprocess.run(
        ["tesseract", "stdin", "stdout"],
        input=buf.getvalue(),
        capture_output=True,
        timeout=60,
        creationflags=NO_WINDOW_FLAGS,
    )
    if result.returncode != 0:
        logger.debug("tesseract failed: %s", result.stderr.decode(errors="replace")[:300])
        return ""
    return result.stdout.decode("utf-8", errors="replace")


async def analyze_media_ocr(limit: int | None = None) -> dict:
    stats: dict = {"processed": 0}
    if not MEDIA_OCR_ENABLED:
        return {**stats, "skipped": "ocr_disabled"}
    if shutil.which("tesseract") is None:
        logger.warning("6D OCR: tesseract binary not found on PATH, skipping")
        return {**stats, "skipped": "tesseract_unavailable"}
    if not _drive_available():
        logger.warning("6D OCR: media drive unavailable, skipping")
        return {**stats, "skipped": "drive_unavailable"}

    batch_limit = limit if limit is not None else MEDIA_OCR_BATCH_SIZE

    items = await fetch_unprocessed_media(_OCR_PRIORITY_1, "ocr_text", limit=batch_limit)
    if len(items) < batch_limit:
        items += await fetch_unprocessed_derived(
            ["pdf_image", "video_frame"], "ocr_text", limit=batch_limit - len(items)
        )
    if len(items) < batch_limit:
        items += await fetch_unprocessed_media(_OCR_PRIORITY_2, "ocr_text", limit=batch_limit - len(items))

    entity_lookup = await build_entity_lookup()
    entity_texts: dict[str, list[tuple[str, str]]] = defaultdict(list)

    derived_parent_ids = {item["parent_media_item_id"] for item in items if item.get("parent_media_item_id")}
    item_entities = await fetch_media_item_entities(list(derived_parent_ids)) if derived_parent_ids else {}

    rows = []
    for item in items:
        path = resolve_media_path(item["file_path"])
        if path is None:
            continue
        text = ""
        try:
            text = _ocr_image(path)[:_MAX_OCR_TEXT_LEN]
        except Exception:
            logger.debug("OCR failed for %s", path, exc_info=True)

        rows.append({
            "media_item_id": item["id"], "parent_media_item_id": item.get("parent_media_item_id"),
            "source": item["source"], "content_type": item["content_type"],
            "analysis_type": "ocr_text", "extracted_text": text or None,
            "model_version": "tesseract-v1",
        })
        if not text:
            continue

        parent_id = item.get("parent_media_item_id")
        eid = None
        if parent_id:
            src = item_entities.get(parent_id)
            if src:
                eid = lookup_entity(entity_lookup, src[0], src[1])
        elif item.get("entity_id"):
            eid = lookup_entity(entity_lookup, item["source"], item["entity_id"])
        if eid:
            entity_texts[eid].append((item["source"], text))

    await upsert_media_analysis(rows)
    stats["processed"] = len(rows)

    # Idle-cycle skip (CPU saver). Same TODO as 6C pdf_text: emit rebuilds from
    # this batch's entity_texts only, so steady-state cycles overwrite prior
    # ocr_text signals rather than accumulating.
    if stats["processed"]:
        lookups = await build_contact_lookups()
        stats.update(await emit_media_contact_signals(entity_texts, lookups, "ocr_text"))
    logger.info("6D OCR: %s", stats)
    return stats


# ── 6F: face detection + embeddings ──

_FACE_DETECTOR_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
_FACE_RECOGNIZER_MODEL = MODEL_DIR / "face_recognition_sface_2021dec.onnx"

# Profile photos first (github/lemon8/telegram/instagram), then personal-comms
# photos (telegram/whatsapp/beeper), then bulk image sources last.
_FACE_CONTENT_TYPES_PRIORITY = [
    (None, "profile_photo"),
    ("telegram", "photo"), ("whatsapp", "photo"), ("beeper", "image"),
    ("lemon8", "image"), ("search", "image"), ("website", "image"),
]
_FACE_MAX_DIM = 1600
# Cosine similarity between SFace embeddings. OpenCV's model docs cite ~0.363
# as the threshold at FAR=1e-3; pick a bit stricter to stay precision-first.
# NOTE: SFace embeddings (analyze_media_faces) still feed the media gallery's
# "has_face" filter, but they NO LONGER drive identity matching — F3 moved the
# media_face_match signal onto InsightFace/facetracker embeddings (below).
_FACE_MATCH_THRESHOLD = 0.40

# F3: cross-entity matching now uses facetracker's 512-dim ArcFace (InsightFace)
# embeddings instead of the 128-dim SFace ones. ArcFace cosine separates
# same/different identity much more cleanly; 0.40 keeps this precision-first
# (same-person pairs typically land 0.45-0.7, impostors below ~0.2).
_INSIGHTFACE_MATCH_THRESHOLD = float(os.getenv("INSIGHTFACE_MATCH_THRESHOLD", "0.40"))


def _face_models_available() -> bool:
    return _FACE_DETECTOR_MODEL.is_file() and _FACE_RECOGNIZER_MODEL.is_file()


def _load_image_for_face(path):
    img = cv2.imread(str(path))
    if img is None:
        return None
    h, w = img.shape[:2]
    if max(h, w) > _FACE_MAX_DIM:
        scale = _FACE_MAX_DIM / max(h, w)
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
    return img


def _detect_and_embed(img, detector, recognizer):
    h, w = img.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(img)
    if faces is None:
        return [], -1, None

    faces_info = []
    best_idx, best_area = -1, 0.0
    for i, f in enumerate(faces):
        x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])
        faces_info.append({"bbox": [x, y, fw, fh], "score": score})
        area = fw * fh
        if area > best_area:
            best_area, best_idx = area, i

    embedding = None
    if best_idx >= 0:
        try:
            aligned = recognizer.alignCrop(img, faces[best_idx])
            feat = recognizer.feature(aligned)
            embedding = feat.flatten().astype(float).tolist()
        except Exception:
            logger.debug("Face embedding failed", exc_info=True)
    return faces_info, best_idx, embedding


async def analyze_media_faces(limit: int | None = None) -> dict:
    stats = {"processed": 0, "faces_detected": 0, "embeddings": 0, "media_face_match_signals": 0}
    if not _drive_available():
        logger.warning("6F face detection: media drive unavailable, skipping")
        return {**stats, "skipped": "drive_unavailable"}
    if not _face_models_available():
        logger.warning("6F face detection: model files not found in %s, skipping", MODEL_DIR)
        return {**stats, "skipped": "models_unavailable"}

    batch_limit = limit if limit is not None else MEDIA_FACE_BATCH_SIZE
    items = await fetch_unprocessed_media(_FACE_CONTENT_TYPES_PRIORITY, "face_detection", limit=batch_limit)
    if len(items) < batch_limit:
        items += await fetch_unprocessed_derived(
            ["pdf_image", "video_frame"], "face_detection", limit=batch_limit - len(items)
        )

    detector = cv2.FaceDetectorYN_create(str(_FACE_DETECTOR_MODEL), "", (320, 320))
    recognizer = cv2.FaceRecognizerSF_create(str(_FACE_RECOGNIZER_MODEL), "")

    detection_rows, embedding_rows = [], []
    for item in items:
        path = resolve_media_path(item["file_path"])
        if path is None:
            continue
        img = _load_image_for_face(path)
        if img is None:
            continue
        try:
            faces_info, best_idx, embedding = _detect_and_embed(img, detector, recognizer)
        except Exception:
            logger.debug("Face detection failed for %s", path, exc_info=True)
            continue

        detection_rows.append({
            "media_item_id": item["id"], "parent_media_item_id": item.get("parent_media_item_id"),
            "source": item["source"], "content_type": item["content_type"],
            "analysis_type": "face_detection",
            "result_json": {"face_count": len(faces_info), "faces": faces_info},
            "model_version": "yunet-2023mar",
        })
        stats["faces_detected"] += len(faces_info)

        if embedding is not None:
            embedding_rows.append({
                "media_item_id": item["id"], "parent_media_item_id": item.get("parent_media_item_id"),
                "source": item["source"], "content_type": item["content_type"],
                "analysis_type": "face_embedding",
                "result_json": {
                    "bbox": faces_info[best_idx]["bbox"], "face_index": best_idx,
                    "face_count": len(faces_info),
                },
                "face_embedding": embedding,
                "model_version": "sface-2021dec",
            })
            stats["embeddings"] += 1

    await upsert_media_analysis(detection_rows)
    await upsert_media_analysis(embedding_rows)
    stats["processed"] = len(detection_rows)

    # NOTE: the media_face_match identity signal is NO LONGER built here. It is
    # InsightFace-based (facetracker corpus) and must run even when the SFace
    # models are absent — but this function early-returns above in that case. So
    # the rebuild lives in rebuild_face_match_signals(), called independently by
    # the runner (see incremental_runner). This function now only produces the
    # SFace face_detection/face_embedding rows that feed the gallery has_face.
    logger.info("6F face detection: %s", stats)
    return stats


# Module-level cache of the entity_faces row count at the last successful signal
# rebuild. -1 forces a rebuild on the first cycle after process start.
_last_bridge_count = -1


async def rebuild_face_match_signals() -> int:
    """Rebuild media_face_match signals only when the bridged-face corpus
    (public.entity_faces) has changed since the last build. Returns the number
    of signals written, or -1 when the rebuild was skipped (corpus unchanged).

    F3: this is decoupled from analyze_media_faces (the SFace detector) on
    purpose — the signal is built from facetracker's InsightFace corpus, which
    face_worker grows independently, so it must run even when the SFace YuNet/
    SFace models are missing. The cheap COUNT gate keeps the O(n^2) rebuild off
    idle cycles. Called directly by incremental_runner after the face step."""
    global _last_bridge_count
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM public.entity_faces")
    if count == _last_bridge_count:
        return -1  # unchanged — existing signals still valid, skip the rebuild
    n = await _build_face_match_signals()
    _last_bridge_count = count
    return n


def _parse_pgvector(text: str) -> list[float]:
    """pgvector renders vector(512) as '[0.1,0.2,...]'. asyncpg has no codec for
    the type registered on the analyzer pool, so we SELECT embedding_vec::text and
    parse here (the bracketed form is valid JSON)."""
    return json.loads(text)


async def _build_face_match_signals() -> int:
    """F3 — derive the cross-entity media_face_match identity signal from the
    facetracker InsightFace (ArcFace, 512-dim) corpus, bridged to analyzer
    entities via public.entity_faces. Replaces the old SFace-embedding source.

    facetracker.faces + public.entity_faces live in the SAME analyzer DB (just
    other schemas), so one analyzer connection reaches both — no cross-DB hop.
    entity_faces already carries the resolved entity_id, so no entity_lookup is
    needed; we only resolve each face's SOURCE PLATFORM (via its media_item) to
    keep the cross-platform-only filter the signal depends on.
    """
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        # Highest-quality faces first so the bounded corpus keeps the best crops
        # as the table grows past tens of thousands of faces.
        emb_rows = await conn.fetch("""
            SELECT ef.entity_id::text   AS entity_id,
                   ef.media_item_id     AS media_item_id,
                   f.id                 AS face_id,
                   f.embedding_vec::text AS emb
            FROM public.entity_faces ef
            JOIN facetracker.faces f ON f.id = ef.face_id
            WHERE ef.entity_id IS NOT NULL AND f.embedding_vec IS NOT NULL
            ORDER BY f.quality_score DESC NULLS LAST
            LIMIT $1
        """, MEDIA_FACE_MATCH_MAX)

    new_signals: list[tuple] = []
    if emb_rows:
        # Resolve each face's source platform from its bridged collector media
        # item. Faces with no media_item_id (e.g. video-frame faces keyed on
        # video_path) get source=None and are simply excluded from the
        # same-platform skip — they can still match cross-identity.
        mi_ids = list({r["media_item_id"] for r in emb_rows if r["media_item_id"]})
        item_entities = await fetch_media_item_entities(mi_ids) if mi_ids else {}

        eids: list[str] = []
        srcs: list[str | None] = []
        mids: list[str] = []
        embs: list[list[float]] = []
        for r in emb_rows:
            mi = r["media_item_id"]
            source = item_entities.get(mi, (None, None))[0] if mi else None
            eids.append(r["entity_id"])
            srcs.append(source)
            mids.append(mi or f"face:{r['face_id']}")
            embs.append(_parse_pgvector(r["emb"]))

        n = len(embs)
        if n >= 2:
            # Normalize once; cosine similarity is then a single BLAS matrix-vector
            # product per row (M @ M[i]) — O(n^2) compute in C, but O(n*d) memory
            # (no full n*n matrix materialized) and orders of magnitude faster than
            # the previous Python double loop.
            M = np.asarray(embs, dtype=np.float32)
            norms = np.linalg.norm(M, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            M /= norms

            match_targets: dict[int, set[str]] = defaultdict(set)
            pairwise: list[tuple] = []  # (sim, i, j)
            for i in range(n - 1):
                sims = M[i + 1:] @ M[i]  # cosine sims of all j>i against i
                for jrel in np.nonzero(sims >= _INSIGHTFACE_MATCH_THRESHOLD)[0]:
                    j = i + 1 + int(jrel)
                    # Skip same entity, or same KNOWN platform (cross-platform is
                    # the whole point of this signal). Unknown source (None) is
                    # not treated as a shared platform, so it can still match.
                    if eids[i] == eids[j]:
                        continue
                    if srcs[i] is not None and srcs[i] == srcs[j]:
                        continue
                    sim = float(sims[jrel])
                    match_targets[i].add(eids[j])
                    match_targets[j].add(eids[i])
                    pairwise.append((sim, i, j))

            # Fan-out filter: drop matches where either face also resembles a
            # THIRD distinct entity (ambiguous / common-looking face), then keep
            # the best remaining match per entity pair.
            pair_best: dict[frozenset, tuple] = {}
            for sim, i, j in pairwise:
                if len(match_targets[i]) > 1 or len(match_targets[j]) > 1:
                    continue
                a_eid, b_eid = eids[i], eids[j]
                pair_key = frozenset((a_eid, b_eid))
                existing = pair_best.get(pair_key)
                if existing is None or sim > existing[0]:
                    pair_best[pair_key] = (sim, a_eid, mids[i], b_eid, mids[j])

            for sim, a_eid, a_mid, b_eid, b_mid in pair_best.values():
                new_signals.append((
                    a_eid, "media_face_match", "multi", "facetracker.faces",
                    "insightface_embedding", a_mid,
                    "multi", b_eid, f"sim:{sim:.3f}", round(min(sim, 0.99), 2),
                ))

    async with analyzer.acquire() as conn:
        await conn.execute("DELETE FROM identity_signals WHERE signal_type = 'media_face_match'")
        if new_signals:
            await conn.executemany("""
                INSERT INTO identity_signals
                    (entity_id, signal_type, source_platform, source_table, source_column,
                     source_record_id, target_platform, target_record_id, value, confidence)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, new_signals)
    return len(new_signals)


# ── 6H: video frame extraction ──

# Personal-comms video only — youtube/tiktok video is Tier 2 (out of scope).
_VIDEO_CONTENT_TYPES = [("telegram", "video"), ("beeper", "video"), ("whatsapp", "video")]
_VIDEO_FRAME_INTERVAL_SEC = 20
_VIDEO_FRAME_MAX = 12


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def extract_video_frames(limit: int | None = None) -> dict:
    stats = {"videos_processed": 0, "frames_extracted": 0}
    if not _drive_available():
        logger.warning("6H video frames: media drive unavailable, skipping")
        return {**stats, "skipped": "drive_unavailable"}
    if not _ffmpeg_available():
        logger.warning("6H video frames: ffmpeg not found on PATH, skipping")
        return {**stats, "skipped": "ffmpeg_unavailable"}

    batch_limit = limit if limit is not None else MEDIA_VIDEO_FRAME_BATCH_SIZE
    items = await fetch_unprocessed_media(_VIDEO_CONTENT_TYPES, "video_frames", limit=batch_limit)

    marker_rows, frame_rows = [], []
    for item in items:
        path = resolve_media_path(item["file_path"])
        if path is None:
            continue

        outdir = VIDEO_FRAME_DIR / item["id"]
        outdir.mkdir(parents=True, exist_ok=True)
        count = 0
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg", "-y", "-i", str(path),
                    "-vf", f"fps=1/{_VIDEO_FRAME_INTERVAL_SEC}",
                    "-frames:v", str(_VIDEO_FRAME_MAX), "-loglevel", "error",
                    str(outdir / "frame_%03d.jpg"),
                ],
                capture_output=True, timeout=120, creationflags=NO_WINDOW_FLAGS,
            )
            if result.returncode == 0:
                for frame_path in sorted(outdir.glob("frame_*.jpg")):
                    idx = int(frame_path.stem.split("_")[1])
                    sec = (idx - 1) * _VIDEO_FRAME_INTERVAL_SEC
                    frame_rows.append({
                        "media_item_id": f"{item['id']}:frame:{sec}",
                        "parent_media_item_id": item["id"],
                        "source": item["source"], "content_type": "image",
                        "analysis_type": "video_frame",
                        "result_json": {"derived_path": str(frame_path), "timestamp_sec": sec},
                        "model_version": "ffmpeg-fps-v1",
                    })
                    count += 1
            else:
                logger.debug("ffmpeg failed for %s: %s", path, result.stderr.decode(errors="replace")[:500])

            # Fallback: clips shorter than the sampling interval yield no frames
            # under fps=1/N (~half of messaging-app videos are sub-20s). Grab the
            # first frame so short videos still contribute to 6D/6F.
            if count == 0:
                first = outdir / "frame_001.jpg"
                fb = await asyncio.to_thread(
                    subprocess.run,
                    ["ffmpeg", "-y", "-i", str(path), "-frames:v", "1",
                     "-loglevel", "error", str(first)],
                    capture_output=True, timeout=120, creationflags=NO_WINDOW_FLAGS,
                )
                if fb.returncode == 0 and first.exists():
                    frame_rows.append({
                        "media_item_id": f"{item['id']}:frame:0",
                        "parent_media_item_id": item["id"],
                        "source": item["source"], "content_type": "image",
                        "analysis_type": "video_frame",
                        "result_json": {"derived_path": str(first), "timestamp_sec": 0},
                        "model_version": "ffmpeg-fps-v1",
                    })
                    count = 1
        except Exception:
            logger.debug("Frame extraction failed for %s", path, exc_info=True)

        # Marker row on the video's own id — done-cursor for
        # fetch_unprocessed_media. Per-frame rows above (analysis_type=
        # 'video_frame') are what 6B/6D/6F pick up via fetch_unprocessed_derived.
        marker_rows.append({
            "media_item_id": item["id"], "source": item["source"],
            "content_type": item["content_type"], "analysis_type": "video_frames",
            "result_json": {"frame_count": count}, "model_version": "ffmpeg-fps-v1",
        })
        stats["frames_extracted"] += count

    await upsert_media_analysis(frame_rows)
    await upsert_media_analysis(marker_rows)
    stats["videos_processed"] = len(marker_rows)
    logger.info("6H video frames: %s", stats)
    return stats
