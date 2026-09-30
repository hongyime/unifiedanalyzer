"""GAN-face screening (Do Next #4).

Periodically scores unscored ``entity_faces`` rows with the offline
``GanDetector`` and emits ``likely_synthetic_avatar_pair`` signals when
two entities each have a face above the pair threshold.

This is the FIRST negative-weight identity signal in the scorer
(_TYPE_WEIGHT["likely_synthetic_avatar_pair"] = -0.35). Two synthetic
avatars sharing traits should LOWER same-person probability, not raise
it - GAN faces are cheap to produce and often mass-shared across a fake
network.

**Enable path**:
  1. Run ``scripts/download_gan_model.py`` once to fetch the ONNX weights.
  2. Set ``GAN_DETECTOR_ENABLED=1`` in analyzer env.
  3. Wired into the scheduler as a periodic job (not incremental_runner;
     runs at ``GAN_SCREENING_INTERVAL_HOURS`` cadence, default 12h).

Default DISABLED. Fails cleanly when the model file is missing.

Ref: Z:\\...\\research\\spec-do-next-4-gan-detector.md
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
from typing import Optional

from src.db.connection import get_analyzer_pool, get_collector_pool
from src.face.engine.gan_detector import GAN_MODEL_VERSION, GanDetector

logger = logging.getLogger(__name__)

_ENABLED = "GAN_DETECTOR_ENABLED"
_BATCH = int(os.getenv("GAN_SCREENING_BATCH", "50"))
_PAIR_THRESHOLD = float(os.getenv("GAN_PAIR_THRESHOLD", "0.65"))
_MIN_FACE_QUALITY = float(os.getenv("GAN_MIN_FACE_QUALITY", "0.4"))


def _is_enabled() -> bool:
    return os.getenv(_ENABLED, "0") == "1"


async def _load_face_image_bytes(analyzer, face_id: int) -> Optional[bytes]:
    """Fetch the face crop bytes for a face_id from facetracker.faces.

    Schema references (verified via prior grep):
    - entity_faces.face_id -> facetracker.faces.id
    - facetracker.faces has embedding_vec (pgvector) but also a crop
      column or reference (varies by install). We try common patterns.
    """
    async with analyzer.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT crop_bytes, crop_path
            FROM facetracker.faces
            WHERE id = $1
            LIMIT 1
            """,
            face_id,
        )
    if not row:
        return None
    # Prefer inline bytes if the schema stored them; else read from crop_path.
    if row.get("crop_bytes"):
        return row["crop_bytes"]
    path = row.get("crop_path")
    if path:
        try:
            return Path(path).read_bytes()
        except Exception as exc:
            logger.debug("gan_screening: crop_path unreadable %s: %s", path, exc)
    return None


async def run_gan_face_screening() -> dict:
    summary = {"skipped": None, "scanned": 0, "scored": 0, "high_score": 0,
               "signals_emitted": 0, "errors": 0, "model_available": False}
    if not _is_enabled():
        summary["skipped"] = "disabled"
        return summary

    detector = GanDetector()
    if not detector.available():
        summary["skipped"] = "model_unavailable"
        return summary
    summary["model_available"] = True

    try:
        analyzer = get_analyzer_pool()
    except Exception:
        summary["skipped"] = "no_analyzer_pool"
        return summary

    # Pick unscored entity_faces. Prefer operator-tier or high-confidence rows.
    async with analyzer.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT ef.face_id, ef.entity_id
            FROM entity_faces ef
            WHERE ef.gan_score IS NULL
              AND ef.confidence >= $1
              AND NOT EXISTS (
                SELECT 1 FROM face_gan_overrides fgo
                WHERE fgo.face_id = ef.face_id
              )
            ORDER BY ef.confidence DESC, ef.created_at ASC
            LIMIT $2
            """,
            _MIN_FACE_QUALITY,
            _BATCH,
        )

    if not rows:
        return summary

    scored_face_ids: list[tuple[int, str, float]] = []  # (face_id, entity_id, score)

    for row in rows:
        face_id = row["face_id"]
        entity_id = str(row["entity_id"]) if row["entity_id"] else None
        summary["scanned"] += 1
        img = await _load_face_image_bytes(analyzer, face_id)
        if img is None:
            # Record a "no_image" version so we don't retry this face every cycle.
            async with analyzer.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE entity_faces
                    SET gan_score = NULL,
                        gan_model_version = 'no_image',
                        gan_scored_at = now()
                    WHERE face_id = $1
                    """,
                    face_id,
                )
            continue

        try:
            score = detector.score(img)
        except Exception:
            summary["errors"] += 1
            logger.exception("gan_screening: inference failed for face_id=%s", face_id)
            continue

        if score is None:
            summary["errors"] += 1
            continue

        summary["scored"] += 1
        if score > _PAIR_THRESHOLD:
            summary["high_score"] += 1
        if entity_id:
            scored_face_ids.append((face_id, entity_id, score))

        async with analyzer.acquire() as conn:
            await conn.execute(
                """
                UPDATE entity_faces
                SET gan_score = $2,
                    gan_model_version = $3,
                    gan_scored_at = now()
                WHERE face_id = $1
                """,
                face_id,
                float(score),
                GAN_MODEL_VERSION,
            )

    # Pairwise signals: any two entities with a face each above threshold.
    high_scorers = [t for t in scored_face_ids if t[2] > _PAIR_THRESHOLD]
    if len(high_scorers) >= 2:
        signal_rows: list[tuple] = []
        for i, (face_a, ent_a, score_a) in enumerate(high_scorers):
            for face_b, ent_b, score_b in high_scorers[i + 1:]:
                if ent_a == ent_b:
                    continue
                signal_rows.append((
                    ent_a,
                    "likely_synthetic_avatar_pair",
                    "gan_detector",
                    "entity_faces",
                    "gan_score",
                    str(face_a),
                    "entity",
                    ent_b,
                    json.dumps({
                        "face_a": face_a,
                        "face_b": face_b,
                        "score_a": score_a,
                        "score_b": score_b,
                        "model": GAN_MODEL_VERSION,
                    }),
                    min(score_a, score_b),
                ))

        if signal_rows:
            async with analyzer.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO identity_signals
                        (entity_id, signal_type, source_platform, source_table, source_column,
                         source_record_id, target_platform, target_record_id, value, confidence)
                    VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT DO NOTHING
                    """,
                    signal_rows,
                )
            summary["signals_emitted"] = len(signal_rows)

    logger.info("gan_screening: %s", summary)
    return summary


__all__ = ["run_gan_face_screening"]
