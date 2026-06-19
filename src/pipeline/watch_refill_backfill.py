"""Token-free watcher: auto-run the R5 derived-artifact backfill once the
collector media refill plateaus. NO Claude / LLM involvement — a plain Python
process. Run it and walk away.

Why: after the Z: reformat the collector is re-downloading media
(COLLECTOR_RECOVER_MISSING). The derived-artifact backfill
(src/pipeline/backfill_derived.py) must run AFTER refill finishes — re-deriving
from a half-downloaded file yields corrupt frames. This watcher polls the media
tree on Z:; when the file count stops growing for a sustained window (refill
done/idle), it runs the backfill once and exits.

Run (host with ffmpeg on PATH; survives logoff via Task Scheduler or `start`):
    python -m src.pipeline.watch_refill_backfill
    python -m src.pipeline.watch_refill_backfill --interval 600 --stable-ticks 6
    python -m src.pipeline.watch_refill_backfill --dry-run   # just report plateau, don't backfill

Defaults: poll every 600s; declare plateau after 6 consecutive unchanged counts
(~1h stable); give up after --max-hours (default 48) as a safety cap.
"""
import argparse
import asyncio
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("watch_refill_backfill")

# Z:/unifiedcollector — the dir that CONTAINS "media/". Same env the pipeline uses.
_MEDIA_ROOT = Path(os.getenv("COLLECTOR_MEDIA_ROOT", "Z:/unifiedcollector")) / "media"


def _count_files(root: Path) -> int:
    """Recursive file count under root via os.scandir (fast, no hydration risk —
    this is Z:, not C:/OneDrive). Returns 0 if the dir is missing."""
    if not root.is_dir():
        return 0
    total = 0
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        total += 1
        except OSError:
            continue
    return total


def _run_backfill() -> None:
    """Invoke the R5 backfill in-process (reuses its async main)."""
    from src.pipeline.backfill_derived import main_async

    class _Args:
        limit = 200
        passes = 100
        dry_run = False
        clear_only = False

    asyncio.run(main_async(_Args()))


def main() -> None:
    ap = argparse.ArgumentParser(description="Watch media refill; backfill on plateau (no tokens).")
    ap.add_argument("--interval", type=int, default=600, help="seconds between polls")
    ap.add_argument("--stable-ticks", type=int, default=6, help="consecutive unchanged polls = plateau")
    ap.add_argument("--max-hours", type=float, default=48.0, help="safety cap before giving up")
    ap.add_argument("--dry-run", action="store_true", help="detect plateau but don't run the backfill")
    args = ap.parse_args()

    logger.info("watching %s — poll %ds, plateau after %d stable ticks, cap %.0fh",
                _MEDIA_ROOT, args.interval, args.stable_ticks, args.max_hours)

    deadline = time.time() + args.max_hours * 3600
    last = -1
    stable = 0
    while time.time() < deadline:
        count = _count_files(_MEDIA_ROOT)
        if count == last:
            stable += 1
        else:
            stable = 0
        logger.info("media files=%d (stable %d/%d)", count, stable, args.stable_ticks)
        if count > 0 and stable >= args.stable_ticks:
            logger.info("refill plateaued at %d files — running backfill", count)
            if args.dry_run:
                logger.info("dry-run: skipping backfill")
            else:
                try:
                    _run_backfill()
                    logger.info("backfill complete — exiting watcher")
                except Exception:
                    logger.exception("backfill failed")
            return
        last = count
        time.sleep(args.interval)

    logger.warning("max-hours reached without a clear plateau — exiting without backfill")


if __name__ == "__main__":
    main()
