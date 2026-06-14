"""
Phase 6 media-analysis backfill (task 15, docs/media_analysis_plan.md).

Front-loads the full media backlog so the live scheduler doesn't have to grind
through ~120k items at the incremental batch sizes. Runs in dependency order:

    6C  pdf_text          (unbounded — cheap)
    6C.2 pdf_images       (unbounded — derived-row producer for 6B/6D/6F)
    6H  video_frames      (unbounded — derived-row producer for 6D/6F)
    6A  exif_gps          (unbounded — cheap)
    6B  phash             (unbounded — cheap; also covers derived pdf_image rows)
    6D  ocr_text          (capped — Tesseract is slow; remainder handled by the
                           scheduled incremental cycles)
    6F  faces             (capped — face embed + O(n^2) match build is heavy;
                           remainder handled by the scheduled incremental cycles)

Run in the BACKGROUND — Tier 0 alone can take a long time. Uses
init_pools(apply_schema_ddl=False) so it never contends with the live "serve"
process for the schema-DDL relation lock (see src/db/connection.py).

NOTE for a continuing agent: this is re-runnable and idempotent. Each function
only fetches rows that have no existing media_analysis row for its analysis_type
(fetch_unprocessed_media / fetch_unprocessed_derived), so re-launching after an
interruption resumes where it left off. The OCR/face caps below can be raised
(or set to None for unbounded) once the cheaper Tier 0 backlog is drained.
"""
import asyncio
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(r"C:\unifiedanalyzer")
sys.path.insert(0, r"C:\unifiedanalyzer")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(r"C:\unifiedanalyzer\.env")

from src.db.connection import close_pools, get_analyzer_pool, init_pools  # noqa: E402
from src.pipeline.media_analysis import (  # noqa: E402
    analyze_media_exif,
    analyze_media_pdf_text,
    analyze_media_phash,
    extract_pdf_images,
)
from src.pipeline.media_analysis_tier1 import (  # noqa: E402
    analyze_media_faces,
    analyze_media_ocr,
    extract_video_frames,
)

# Tier 1 backfill caps — keep one launch bounded; scheduled cycles drain the rest.
OCR_BACKFILL_LIMIT = int(os.getenv("MEDIA_OCR_BACKFILL_LIMIT", "3000"))
FACE_BACKFILL_LIMIT = int(os.getenv("MEDIA_FACE_BACKFILL_LIMIT", "3000"))


async def _drain(label: str, fn, batch: int, progress_key: str, max_total: int | None = None):
    """Call fn(limit=batch) in a loop until it stops making progress.

    Each call persists its own chunked upserts (see upsert_media_analysis), so a
    crash or restart resumes from where the DB left off — no work is redone and
    nothing is lost all-or-nothing. progress_key is the stats field that counts
    items handled this call; 0 means the backlog for this stage is drained.
    max_total caps a single launch for the expensive Tier 1 stages (the rest is
    picked up by the scheduled incremental cycles or a re-launch of this script).
    """
    t0 = time.monotonic()
    total = 0
    rounds = 0
    print(f"\n=== {label} START (batch={batch}, cap={max_total}) ===", flush=True)
    while True:
        try:
            stats = await fn(limit=batch)
        except Exception as e:  # noqa: BLE001 — one round failing must not abort the rest
            print(f"  {label} round {rounds} FAILED: {e!r}", flush=True)
            import traceback

            traceback.print_exc()
            break
        n = int(stats.get(progress_key, 0) or 0)
        total += n
        rounds += 1
        print(f"  {label} round {rounds}: +{n} ({progress_key}), total={total}, {stats}", flush=True)
        if n == 0:
            break
        if max_total is not None and total >= max_total:
            print(f"  {label} hit cap {max_total}; remainder deferred to scheduled cycles", flush=True)
            break
    dt = time.monotonic() - t0
    print(f"=== {label} DONE in {dt:.0f}s: total={total} over {rounds} rounds ===", flush=True)


async def main():
    await init_pools(apply_schema_ddl=False)

    # --- Tier 0: drain fully (cheap), dependency-ordered. 6C/6C.2/6H produce
    # derived rows that 6B/6D/6F consume, so they run first. ---
    await _drain("6C  pdf_text", analyze_media_pdf_text, batch=200, progress_key="processed")
    await _drain("6C.2 pdf_images", extract_pdf_images, batch=100, progress_key="pdfs_processed")
    await _drain("6H  video_frames", extract_video_frames, batch=25, progress_key="videos_processed")
    await _drain("6A  exif_gps", analyze_media_exif, batch=1000, progress_key="processed")
    await _drain("6B  phash", analyze_media_phash, batch=1000, progress_key="processed")

    # --- Tier 1: capped per launch (expensive: Tesseract OCR, ONNX face embed). ---
    await _drain("6D  ocr_text", analyze_media_ocr, batch=300, progress_key="processed", max_total=OCR_BACKFILL_LIMIT)
    await _drain("6F  faces", analyze_media_faces, batch=300, progress_key="processed", max_total=FACE_BACKFILL_LIMIT)

    # --- Final tallies ---
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        print("\n=== media_analysis row counts by analysis_type ===", flush=True)
        rows = await conn.fetch(
            """
            SELECT analysis_type, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE parent_media_item_id IS NOT NULL) AS derived
            FROM media_analysis GROUP BY analysis_type ORDER BY analysis_type
            """
        )
        for r in rows:
            print(f"  {r['analysis_type']:<20} {r['n']:>8}  (derived: {r['derived']})", flush=True)

        print("\n=== identity_signals: Phase 6 signal types ===", flush=True)
        rows = await conn.fetch(
            """
            SELECT signal_type, COUNT(*) AS n FROM identity_signals
            WHERE signal_type IN ('media_gps_colocation', 'media_perceptual_match', 'media_face_match')
            GROUP BY signal_type ORDER BY signal_type
            """
        )
        for r in rows:
            print(f"  {r['signal_type']:<25} {r['n']}", flush=True)
        if not rows:
            print("  (none)", flush=True)

        print("\n=== identity_signals: media-derived contact signals ===", flush=True)
        rows = await conn.fetch(
            """
            SELECT signal_type, source_column, COUNT(*) AS n FROM identity_signals
            WHERE source_table = 'media_items'
            GROUP BY signal_type, source_column ORDER BY signal_type, source_column
            """
        )
        for r in rows:
            print(f"  {r['signal_type']:<20} {r['source_column']:<10} {r['n']}", flush=True)
        if not rows:
            print("  (none)", flush=True)

    await close_pools()
    print("\n=== BACKFILL COMPLETE ===", flush=True)


asyncio.run(main())
