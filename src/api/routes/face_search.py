import asyncio
import base64
import io
import logging
import os
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from src.api.face_lookup import face_crop_url
from src.db.connection import get_analyzer_pool, get_collector_pool

router = APIRouter(tags=["face-search"])
logger = logging.getLogger(__name__)

_MAX_K = 200
_DEFAULT_K = 40
_DEFAULT_MIN_SIMILARITY = 0.35
_detector = None
_detector_lock = asyncio.Lock()


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _vector_literal(vector: Any) -> str:
    return "[" + ",".join(f"{float(v):.7f}" for v in vector) + "]"


async def _get_detector():
    global _detector
    if _detector is not None:
        return _detector
    async with _detector_lock:
        if _detector is None:
            os.environ.setdefault("FACE_ONNX_THREADS", "2")
            from src.face.engine.detector import FaceDetector

            import anyio

            _detector = await anyio.to_thread.run_sync(FaceDetector)
        return _detector


async def _detect_embedding(image_bytes: bytes) -> tuple[list[float], dict]:
    if not image_bytes:
        raise HTTPException(status_code=400, detail="image is empty")

    import anyio
    import numpy as np
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_array = np.array(img.convert("RGB"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"could not read image: {exc}") from exc

    detector = await _get_detector()
    faces = await anyio.to_thread.run_sync(lambda: detector.detect(img_array, extract_embeddings=True))
    usable = [face for face in faces if face.embedding is not None]
    if not usable:
        raise HTTPException(status_code=400, detail="no usable face embedding detected")

    best = max(usable, key=lambda face: face.quality_score)
    bbox = [int(v) for v in best.bbox.tolist()] if hasattr(best.bbox, "tolist") else [int(v) for v in best.bbox]
    return [float(v) for v in best.embedding], {
        "detected_faces": len(faces),
        "selected": {
            "bbox": bbox,
            "quality": round(float(best.quality_score or 0), 4),
            "confidence": round(float(best.confidence or 0), 4),
        },
    }


def _decode_image_value(value: str | None) -> bytes | None:
    if not value:
        return None
    payload = value.split(",", 1)[1] if value.startswith("data:") and "," in value else value
    try:
        return base64.b64decode(payload, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"image must be base64: {exc}") from exc


async def _read_request(request: Request) -> tuple[int | None, bytes | None, int, float]:
    content_type = request.headers.get("content-type", "")
    face_id: int | None = None
    image_bytes: bytes | None = None
    k = int(request.query_params.get("k", _DEFAULT_K))
    min_similarity = float(request.query_params.get("min_similarity", _DEFAULT_MIN_SIMILARITY))

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        raw_face_id = form.get("face_id")
        if raw_face_id not in (None, ""):
            face_id = int(str(raw_face_id))
        raw_k = form.get("k")
        if raw_k not in (None, ""):
            k = int(str(raw_k))
        raw_min = form.get("min_similarity")
        if raw_min not in (None, ""):
            min_similarity = float(str(raw_min))
        upload = form.get("image") or form.get("file")
        if hasattr(upload, "read"):
            image_bytes = await upload.read()
    else:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            raw_face_id = payload.get("face_id")
            if raw_face_id not in (None, ""):
                face_id = int(raw_face_id)
            if payload.get("k") is not None:
                k = int(payload["k"])
            if payload.get("min_similarity") is not None:
                min_similarity = float(payload["min_similarity"])
            image_bytes = _decode_image_value(payload.get("image"))

    k = max(1, min(_MAX_K, k))
    min_similarity = max(-1.0, min(1.0, min_similarity))
    return face_id, image_bytes, k, min_similarity


async def _search_target_face(face_id: int, k: int) -> tuple[list, dict]:
    pool = get_analyzer_pool()
    fetch_limit = min(_MAX_K, max(k * 3, k))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL ivfflat.probes = 10")
            query = await conn.fetchrow("""
                SELECT id, cluster_id
                FROM facetracker.faces
                WHERE id = $1
                  AND embedding_vec IS NOT NULL
                  AND COALESCE(is_junk, FALSE) = FALSE
            """, face_id)
            if not query:
                raise HTTPException(status_code=404, detail="face not found or not searchable")
            rows = await conn.fetch("""
                SELECT f.id AS face_id,
                       f.cluster_id,
                       f.quality_score,
                       f.detection_confidence,
                       1 - (f.embedding_vec <=> (
                           SELECT embedding_vec FROM facetracker.faces WHERE id = $1
                       )) AS similarity,
                       img.file_hash,
                       img.file_path,
                       img.width,
                       img.height,
                       img.created_at AS image_created_at,
                       ef.entity_id::text AS entity_id,
                       e.canonical_name AS entity_name,
                       ef.confidence AS entity_confidence,
                       ef.media_item_id
                FROM facetracker.faces f
                JOIN facetracker.images img ON img.id = f.image_id
                LEFT JOIN LATERAL (
                    SELECT entity_id, media_item_id, confidence
                    FROM public.entity_faces
                    WHERE face_id = f.id
                    ORDER BY confidence DESC NULLS LAST
                    LIMIT 1
                ) ef ON TRUE
                LEFT JOIN public.entities e ON e.id = ef.entity_id
                WHERE f.embedding_vec IS NOT NULL
                  AND COALESCE(f.is_junk, FALSE) = FALSE
                ORDER BY f.embedding_vec <=> (
                    SELECT embedding_vec FROM facetracker.faces WHERE id = $1
                )
                LIMIT $2
            """, face_id, fetch_limit)
    return rows, {"face_id": face_id, "cluster_id": query["cluster_id"]}


async def _search_vector(vector_literal: str, k: int) -> list:
    pool = get_analyzer_pool()
    fetch_limit = min(_MAX_K, max(k * 3, k))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET LOCAL enable_seqscan = off")
            await conn.execute("SET LOCAL ivfflat.probes = 10")
            return await conn.fetch("""
                SELECT f.id AS face_id,
                       f.cluster_id,
                       f.quality_score,
                       f.detection_confidence,
                       1 - (f.embedding_vec <=> $1::vector) AS similarity,
                       img.file_hash,
                       img.file_path,
                       img.width,
                       img.height,
                       img.created_at AS image_created_at,
                       ef.entity_id::text AS entity_id,
                       e.canonical_name AS entity_name,
                       ef.confidence AS entity_confidence,
                       ef.media_item_id
                FROM facetracker.faces f
                JOIN facetracker.images img ON img.id = f.image_id
                LEFT JOIN LATERAL (
                    SELECT entity_id, media_item_id, confidence
                    FROM public.entity_faces
                    WHERE face_id = f.id
                    ORDER BY confidence DESC NULLS LAST
                    LIMIT 1
                ) ef ON TRUE
                LEFT JOIN public.entities e ON e.id = ef.entity_id
                WHERE f.embedding_vec IS NOT NULL
                  AND COALESCE(f.is_junk, FALSE) = FALSE
                ORDER BY f.embedding_vec <=> $1::vector
                LIMIT $2
            """, vector_literal, fetch_limit)


def _infer_platform(row) -> str | None:
    def value(key: str) -> Any:
        try:
            return row[key]
        except Exception:
            return None

    text = " ".join(str(value(key) or "") for key in ("file_path", "media_item_id")).lower()
    for source in ("github", "instagram", "telegram", "whatsapp", "strava", "youtube", "lemon8", "tiktok", "website"):
        if source in text:
            return source
    return None


async def _collector_media_context(rows: list) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict], bool]:
    media_ids = sorted({str(r["media_item_id"]) for r in rows if r["media_item_id"]})
    uuid_media_ids: list[str] = []
    for media_id in media_ids:
        try:
            uuid_media_ids.append(str(UUID(media_id)))
        except ValueError:
            continue
    if not uuid_media_ids:
        return {}, {}, {}, False

    try:
        collector = get_collector_pool()
        async with collector.acquire() as conn:
            records = await conn.fetch("""
                SELECT id::text AS media_item_id,
                       source,
                       content_type,
                       content_id,
                       filename,
                       file_path,
                       sha256,
                       source_url,
                       collected_at
                FROM media_items
                WHERE id = ANY($1::uuid[])
            """, uuid_media_ids)
    except Exception as exc:  # noqa: BLE001 - collector context is optional
        logger.warning("face search collector media context skipped: %s", exc)
        return {}, {}, {}, True

    by_id: dict[str, dict] = {}
    by_hash: dict[str, dict] = {}
    by_path: dict[str, dict] = {}
    for record in records:
        item = dict(record)
        if item.get("media_item_id"):
            by_id[str(item["media_item_id"])] = item
        if item.get("sha256"):
            by_hash[str(item["sha256"])] = item
        if item.get("file_path"):
            by_path[str(item["file_path"])] = item
    return by_id, by_hash, by_path, False


def _media_for_row(row, by_id: dict[str, dict], by_hash: dict[str, dict], by_path: dict[str, dict]) -> dict:
    context = None
    if row["media_item_id"]:
        context = by_id.get(str(row["media_item_id"]))
    if context is None and row["file_hash"]:
        context = by_hash.get(str(row["file_hash"]))
    if context is None and row["file_path"]:
        context = by_path.get(str(row["file_path"]))

    file_path = (context or {}).get("file_path") or row["file_path"]
    return {
        "media_item_id": (context or {}).get("media_item_id") or row["media_item_id"],
        "platform": (context or {}).get("source") or _infer_platform(row),
        "content_type": (context or {}).get("content_type"),
        "content_id": (context or {}).get("content_id"),
        "filename": (context or {}).get("filename") or (os.path.basename(file_path) if file_path else None),
        "file_path": file_path,
        "url": (context or {}).get("source_url"),
        "date": _iso((context or {}).get("collected_at") or row["image_created_at"]),
        "metadata": None,
    }


async def _format_matches(rows: list, k: int, min_similarity: float) -> tuple[list[dict], bool]:
    filtered = [r for r in rows if float(r["similarity"] or 0) >= min_similarity][:k]
    by_id, by_hash, by_path, collector_skipped = await _collector_media_context(filtered)
    matches: list[dict] = []
    for row in filtered:
        face_id = row["face_id"]
        matches.append({
            "face_id": face_id,
            "cluster_id": row["cluster_id"],
            "similarity": round(float(row["similarity"] or 0), 4),
            "quality": round(float(row["quality_score"] or 0), 4),
            "detection_confidence": round(float(row["detection_confidence"] or 0), 4),
            "crop_url": face_crop_url(face_id),
            "entity": {
                "id": row["entity_id"],
                "name": row["entity_name"],
                "confidence": round(float(row["entity_confidence"] or 0), 4),
            } if row["entity_id"] else None,
            "source": _media_for_row(row, by_id, by_hash, by_path),
        })
    return matches, collector_skipped


@router.post("/faces/search")
async def search_faces(
    request: Request,
    k: int = Query(_DEFAULT_K, ge=1, le=_MAX_K),
    min_similarity: float = Query(_DEFAULT_MIN_SIMILARITY, ge=-1.0, le=1.0),
):
    t0 = time.perf_counter()
    body_face_id, image_bytes, body_k, body_min = await _read_request(request)
    k = body_k if body_k != _DEFAULT_K else k
    min_similarity = body_min if body_min != _DEFAULT_MIN_SIMILARITY else min_similarity

    query: dict[str, Any]
    if body_face_id is not None:
        rows, query = await _search_target_face(body_face_id, k)
    elif image_bytes is not None:
        embedding, query = await _detect_embedding(image_bytes)
        rows = await _search_vector(_vector_literal(embedding), k)
        query["face_id"] = None
    else:
        raise HTTPException(status_code=400, detail="provide face_id or image")

    matches, collector_skipped = await _format_matches(rows, k, min_similarity)
    return {
        "query": query,
        "matches": matches,
        "count": len(matches),
        "took_ms": round((time.perf_counter() - t0) * 1000, 1),
        "index": {
            "method": "pgvector_ivfflat",
            "name": "idx_faces_embedding_vec_ivfflat",
            "operator": "vector_cosine_ops",
        },
        "collector_skipped": collector_skipped,
    }
