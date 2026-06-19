"""R5 — re-derive collector media artifacts whose Z: files were lost.

Context: the Z: reformat wiped the derived artifacts (PDF embedded images under
media_derived/pdf_images, video frames under media_derived/video_frames) but the
`media_analysis` rows survived. Phase 6 decides a PDF/video is "done" by the
presence of a MARKER row (analysis_type 'pdf_embedded_image' / 'video_frames',
parent_media_item_id IS NULL) — see fetch_unprocessed_media. So the normal runner
SKIPS those parents and never regenerates the files.

This backfill finds parents whose marker claims it produced files (count > 0) but
whose files are now MISSING on disk, deletes just that marker row, then re-runs
the existing extract_* functions. Those re-derive the files at the same
deterministic paths and re-upsert the per-image/per-frame rows (ON CONFLICT — same
ids), leaving downstream OCR/face/phash rows valid. Parents whose SOURCE media
hasn't refilled yet are skipped by extract_* (resolve_media_path -> None) and have
no marker recreated, so they're retried on the next pass — safe to run repeatedly
while the collector media is still refilling.

Idempotent, resumable, bounded.

Run (on a host with ffmpeg on PATH — same as the original Phase 6):
    python -m src.pipeline.backfill_derived                # clear stale + drain
    python -m src.pipeline.backfill_derived --dry-run      # report only
    python -m src.pipeline.backfill_derived --clear-only   # clear markers, let
                                                           # the running scheduler re-derive
    python -m src.pipeline.backfill_derived --limit 200 --passes 50

Note: video-frame re-derivation needs the `ffmpeg` binary on PATH; if absent,
extract_video_frames logs and skips (PDF images still backfill via PyMuPDF).
"""
import argparse
import asyncio
import json
import logging

from dotenv import load_dotenv

load_dotenv()

from src.db.connection import init_pools, close_pools, get_analyzer_pool
from src.pipeline.media_common import VIDEO_FRAME_DIR, PDF_IMAGE_DIR
from src.pipeline.media_analysis import extract_pdf_images
from src.pipeline.media_analysis_tier1 import extract_video_frames

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_derived")

# (marker analysis_type, result_json count key, on-disk existence check)
def _video_files_present(parent_id: str) -> bool:
    d = VIDEO_FRAME_DIR / parent_id
    return d.is_dir() and any(d.glob("frame_*.jpg"))


def _pdf_files_present(parent_id: str) -> bool:
    # extract_pdf_images writes "{parent}_{page}_{idx}.{ext}" into PDF_IMAGE_DIR.
    return PDF_IMAGE_DIR.is_dir() and any(PDF_IMAGE_DIR.glob(f"{parent_id}_*"))


_MARKERS = (
    ("video_frames", "frame_count", _video_files_present),
    ("pdf_embedded_image", "image_count", _pdf_files_present),
)


async def find_stale_markers() -> dict[str, list[str]]:
    """parent ids whose marker claims count>0 but whose derived files are gone."""
    analyzer = get_analyzer_pool()
    stale: dict[str, list[str]] = {}
    async with analyzer.acquire() as conn:
        for analysis_type, count_key, files_present in _MARKERS:
            rows = await conn.fetch(
                "SELECT media_item_id, result_json FROM media_analysis "
                "WHERE analysis_type = $1 AND parent_media_item_id IS NULL",
                analysis_type,
            )
            ids: list[str] = []
            for r in rows:
                rj = r["result_json"]
                if isinstance(rj, str):
                    rj = json.loads(rj)
                rj = rj or {}
                count = rj.get(count_key, 0) or 0
                if count > 0 and not files_present(r["media_item_id"]):
                    ids.append(r["media_item_id"])
            stale[analysis_type] = ids
    return stale


async def clear_markers(analysis_type: str, ids: list[str], chunk: int = 1000) -> int:
    """Delete marker rows so fetch_unprocessed_media re-selects these parents."""
    if not ids:
        return 0
    analyzer = get_analyzer_pool()
    deleted = 0
    async with analyzer.acquire() as conn:
        for i in range(0, len(ids), chunk):
            batch = ids[i:i + chunk]
            res = await conn.execute(
                "DELETE FROM media_analysis "
                "WHERE analysis_type = $1 AND parent_media_item_id IS NULL "
                "AND media_item_id = ANY($2::text[])",
                analysis_type, batch,
            )
            # res like "DELETE <n>"
            try:
                deleted += int(res.split()[-1])
            except (ValueError, IndexError):
                pass
    return deleted


async def drain(limit: int, passes: int) -> dict:
    """Re-run extract_* until they stop processing (drained) or `passes` hit.

    Each pass derives whatever is currently restorable (source media present);
    parents still missing their source are simply retried next pass.
    """
    totals = {"video_passes": 0, "videos": 0, "pdf_passes": 0, "pdfs": 0}
    for _ in range(passes):
        vid = await extract_video_frames(limit=limit)
        v = vid.get("videos_processed", 0)
        totals["videos"] += v
        totals["video_passes"] += 1
        if v:
            logger.info("video pass: %s", vid)
        if v == 0:
            break
    for _ in range(passes):
        pdf = await extract_pdf_images(limit=limit)
        p = pdf.get("pdfs_processed", 0)
        totals["pdfs"] += p
        totals["pdf_passes"] += 1
        if p:
            logger.info("pdf pass: %s", pdf)
        if p == 0:
            break
    return totals


async def main_async(args) -> None:
    await init_pools()
    try:
        stale = await find_stale_markers()
        n_vid = len(stale.get("video_frames", []))
        n_pdf = len(stale.get("pdf_embedded_image", []))
        logger.info("stale markers (file missing, count>0): videos=%d pdfs=%d", n_vid, n_pdf)

        if args.dry_run:
            logger.info("dry-run: no changes made")
            return

        for analysis_type, ids in stale.items():
            deleted = await clear_markers(analysis_type, ids)
            logger.info("cleared %d %s markers", deleted, analysis_type)

        if args.clear_only:
            logger.info("clear-only: markers cleared; the running scheduler will re-derive")
            return

        totals = await drain(limit=args.limit, passes=args.passes)
        logger.info("backfill done: %s", totals)
    finally:
        await close_pools()


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-derive lost media_derived artifacts (R5).")
    ap.add_argument("--limit", type=int, default=200, help="parents per extract pass")
    ap.add_argument("--passes", type=int, default=100, help="max passes per type")
    ap.add_argument("--dry-run", action="store_true", help="report stale markers only")
    ap.add_argument("--clear-only", action="store_true",
                    help="clear stale markers but let the running scheduler re-derive")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
